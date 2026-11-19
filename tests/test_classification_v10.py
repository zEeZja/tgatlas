"""Контракты V10 без скачивания весов и изменения рабочего каталога."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

try:
    import numpy as np
except ImportError:
    np = None

from src.classification_encoder import CachedEncoder, validate_settings
from src.classification_v10 import (
    calibration,
    classify,
    decision_reasons,
    label_manifest,
    load_policy,
    load_model,
    load_results,
    prepare_review,
    publish_results,
    scores,
    train,
    validate_policy,
)
from src.classification_review import write_review
from src.topic_keywords import TOPICS
from src.semantic_pipeline import merge_result
from tests.test_classification_quality import record


@unittest.skipUnless(
    np is not None and importlib.util.find_spec("sklearn"),
    "Нужны необязательные ML-зависимости",
)
class ClassificationV10Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = load_policy()

    def encoder(self, factory=None, **changes):
        settings = {**self.policy["encoder"], "dimension": 3, **changes}
        encoder = CachedEncoder(
            settings, self.root / "vectors.sqlite", model_factory=factory
        )
        self.addCleanup(encoder.close)
        return encoder

    def test_manifest_covers_all_content_topics(self):
        self.assertEqual(
            [x["id"] for x in label_manifest(self.policy)],
            sorted(set(TOPICS) - {"other"}),
        )

    def test_invalid_numeric_settings(self):
        for changes in [
            {"threads": True},
            {"max_tokens": 64.5},
            {"revision": "z" * 40},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_settings({**self.policy["encoder"], **changes})
        for section, key, value in [
            ("posts", "profile_weight", float("nan")),
            ("selection", "target_precision", True),
            ("limits", "seconds", 1.5),
        ]:
            bad = copy.deepcopy(self.policy)
            bad[section][key] = value
            with self.assertRaises(ValueError):
                validate_policy(bad)

    def test_cache_deduplicates_sorts_and_restores_input_order(self):
        factory = Mock(side_effect=lambda texts: [[len(text), 1, 2] for text in texts])
        encoder = self.encoder(factory)
        first = encoder.encode(["long document", "x", "long document"])
        np.testing.assert_allclose(first[0], first[2])
        self.assertEqual(encoder.stats["computed_documents"], 2)
        self.assertEqual(factory.call_args.args[0], ["x", "long document"])
        np.testing.assert_allclose(encoder.encode(["x"])[0], first[1])
        self.assertEqual(factory.call_count, 1)
        other = self.encoder(
            Mock(side_effect=AssertionError("cache must avoid model")),
            threads=1,
            batch_size=2,
        )
        np.testing.assert_allclose(other.encode(["x"])[0], first[1])
        self.assertEqual(encoder.identity, other.identity)
        changed = self.encoder(lambda texts: [[1, 2, 3] for _ in texts], max_tokens=128)
        self.assertNotEqual(encoder.identity, changed.identity)
        changed.encode(["x"])
        self.assertEqual(changed.stats["computed_documents"], 1)

    def test_corrupt_cache_is_recomputed(self):
        factory = Mock(return_value=[[1, 2, 3]])
        encoder = self.encoder(factory)
        encoder.encode(["x"])
        with encoder.cache:
            encoder.cache.execute(
                "UPDATE embeddings SET vector=?",
                (np.array([float("nan")] * 3, dtype="<f4").tobytes(),),
            )
        self.assertTrue(np.isfinite(encoder.encode(["x"])).all())
        self.assertEqual(factory.call_count, 2)

    def test_invalid_model_vectors_are_not_cached(self):
        encoder = self.encoder(lambda texts: [[0, 0, 0]])
        with self.assertRaises(ValueError):
            encoder.encode(["x"])
        self.assertEqual(
            encoder.cache.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 0
        )

    def test_missing_example_class_has_neutral_evidence(self):
        result = scores(
            [[1, 0, 0]],
            np.eye(3),
            [[1, 0, 0], [0, 1, 0]],
            ["a", "b"],
            ["a", "b", "c"],
            0,
        )
        self.assertAlmostEqual(result[0, 2], 0)
        self.assertGreater(result[0, 0], result[0, 2])

    def test_insufficient_calibration_never_certifies_predictions(self):
        predictions = [[{"id": "science", "score": 2}, {"id": "tech", "score": 1}]] * 10
        operating = calibration(predictions, ["science"] * 10, self.policy["selection"])
        self.assertEqual(operating["status"], "insufficient_calibration")
        self.assertIn(
            "insufficient_calibration",
            decision_reasons(predictions[0], {"science": 10}, operating, self.policy),
        )

    def test_review_rejects_cross_split_duplicate(self):
        path = self.root / "review.csv"
        write_review(
            path, [record("first", split="train"), record("second", split="test")]
        )
        with self.assertRaises(ValueError):
            prepare_review(path, self.policy)

    def test_test_labels_do_not_change_training_or_thresholds(self):
        policy = copy.deepcopy(self.policy)
        policy["encoder"]["dimension"] = 3
        encoder = self.encoder(
            lambda texts: [
                [1 + value for value in hashlib.sha256(text.encode()).digest()[:3]]
                for text in texts
            ]
        )
        rows = [
            record(f"train_{i}", name=f"Обучающий профиль номер {i}", split="train")
            for i in range(20)
        ]
        for i, row in enumerate(rows):
            row["expected_topic"] = "science" if i % 2 else "tech"
        rows += [
            record(f"control_{i}", name=f"Контрольный профиль номер {i}")
            for i in range(3)
        ]
        review = self.root / "review.csv"
        write_review(review, rows)
        first = train(review, self.root / "first.json", encoder, policy)
        self.assertEqual(
            load_model(self.root / "first.json")["classes"], first["classes"]
        )
        damaged = dict(first, scoring={"kind": "unknown"})
        bad_path = self.root / "damaged.json"
        bad_path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_model(bad_path)
        for row in rows[-3:]:
            row["expected_topic"] = "crypto"
        write_review(review, rows)
        second = train(review, self.root / "second.json", encoder, policy)
        for key in (
            "training_fingerprint",
            "head",
            "anchor_vectors",
            "example_vectors",
            "example_labels",
            "selection",
            "calibration",
        ):
            self.assertEqual(first[key], second[key], key)

    def test_manual_priority_never_calls_encoder_or_post_provider(self):
        encoder = Mock(identity="manual", stats={})
        provider = Mock(side_effect=AssertionError("manual label needs no posts"))
        rules = dict(
            overrides={"demo": "science"},
            group_overrides={},
            tag_overrides={},
            geography_overrides={},
        )
        output, benchmark = classify(
            [dict(username="demo", name="Наука", description="Новости науки")],
            dict(policy=self.policy, encoder_identity="manual"),
            encoder,
            provider,
            rules,
        )
        encoder.encode.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(output["demo"]["topic_source"], "manual")
        self.assertEqual(benchmark["remaining"], 0)

    def test_publication_validates_before_replacing_file(self):
        path = self.root / "catalog.json"
        good = dict(
            classification_engine="semantic-v10",
            subcategory="science",
            topic_group=TOPICS["science"]["group"],
        )
        publish_results({"demo": good}, path)
        original = path.read_bytes()
        self.assertEqual(load_results(path), {"demo": good})
        for bad in [
            dict(good, subcategory="missing"),
            dict(good, topic_score=float("nan")),
        ]:
            with self.assertRaises(ValueError):
                publish_results({"demo": bad}, path)
            self.assertEqual(path.read_bytes(), original)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["taxonomy_version"] = "obsolete"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_results(path)

    def test_topic_review_does_not_mark_geography_as_preserved(self):
        row = dict(
            username="demo",
            name="Канал",
            description="Описание",
            subcategory="science",
            topic_group=TOPICS["science"]["group"],
            topic_source="heuristic",
            geography_source="profile",
            geography_scope="country",
        )
        candidate = dict(
            classification_engine="semantic-v10",
            classification_profile=dict(name="Канал", description="Описание"),
            subcategory="tech",
            topic_group=TOPICS["tech"]["group"],
            topic_review_required=True,
            geography_source="profile",
            geography_scope="unknown",
            geography_review_required=False,
        )
        merged = merge_result(row, {"demo": candidate})
        self.assertEqual(merged["subcategory"], "science")
        self.assertEqual(merged["geography_source"], "profile")
        self.assertEqual(merged["geography_scope"], "unknown")
        accepted = merge_result(
            merged, {"demo": dict(candidate, topic_review_required=False)}
        )
        self.assertNotIn("semantic_proposal", accepted)


if __name__ == "__main__":
    unittest.main()
