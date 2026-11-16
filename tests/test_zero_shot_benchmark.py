import argparse
import csv
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import benchmark_zero_shot as bench


class FakeParameter:
    dtype = "torch.float16"

    def is_floating_point(self):
        return True


class FakeModel:
    config = SimpleNamespace(
        _commit_hash="test-revision",
        id2label={0: "entailment", 1: "neutral", 2: "contradiction"},
    )

    def __init__(self):
        self.parameter = FakeParameter()

    def float(self):
        self.parameter.dtype = "torch.float32"
        return self

    def eval(self):
        return self

    def parameters(self):
        return iter([self.parameter])


class FakeClassifier:
    entailment_id = 0

    def __init__(self):
        self.model = FakeModel()

    def __call__(self, text, **kwargs):
        labels = kwargs["candidate_labels"]
        return dict(labels=labels, scores=[1 / len(labels)] * len(labels))


class ZeroShotBenchmarkTests(unittest.TestCase):
    def test_default_labels_are_subjects_without_taxonomy_editorial_notes(self):
        labels = bench.label_map()
        self.assertIn("искусственный интеллект", labels["ai"])
        for text in labels.values():
            self.assertNotIn("Упоминание в рекламе", text)
            self.assertNotIn("Материалы, основная", text)
            self.assertLess(len(text), 160)

    def test_custom_labels_must_cover_current_taxonomy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.json"
            path.write_text('{"ai": "нейросети"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "все ID"):
                bench.label_map(path)

    def test_loaded_half_weights_are_converted_and_recorded(self):
        classifier = FakeClassifier()
        self.assertEqual(
            str(next(classifier.model.parameters()).dtype), "torch.float16"
        )
        runtime = bench.prepare_model(classifier)
        self.assertEqual(runtime["actual_dtype"], "torch.float32")
        self.assertEqual(
            str(next(classifier.model.parameters()).dtype), "torch.float32"
        )
        self.assertEqual(runtime["entailment_id"], 0)

    def test_failed_cast_stops_inference(self):
        classifier = FakeClassifier()
        classifier.model.float = lambda: None
        with self.assertRaisesRegex(ValueError, "Не удалось включить float32"):
            bench.prepare_model(classifier)

    def test_invalid_entailment_mapping_is_rejected(self):
        classifier = FakeClassifier()
        classifier.entailment_id = 2
        with self.assertRaisesRegex(ValueError, "entailment"):
            bench.prepare_model(classifier)

    def args(self, directory):
        source = Path(directory) / "channels.json"
        source.write_text(
            json.dumps(
                dict(
                    channels=[
                        dict(
                            username="sample_one",
                            name="Программирование",
                            description="Разработка программ на Python",
                        ),
                        dict(
                            username="sample_two",
                            name="Новости авиации",
                            description="Пассажирские перевозки и авиакомпании",
                        ),
                        dict(
                            username="sample_three",
                            name="=Книги",
                            description="Книги и литературные рецензии",
                        ),
                        dict(
                            username="sample_four", name="Пустой канал", description=""
                        ),
                    ]
                )
            ),
            encoding="utf-8",
        )
        return argparse.Namespace(
            database=False,
            input=str(source),
            limit=100,
            seed=42,
            labels=None,
            max_chars=2000,
            model=bench.MODEL,
            revision=None,
            device="cpu",
            output=str(Path(directory) / "result"),
            resume=False,
            hypothesis="Основная тема — {}.",
            batch_size=4,
        )

    def test_all_subjects_are_unique_and_unknown_is_not_forced_semantic_label(self):
        labels = bench.label_map()
        self.assertNotIn("other", labels)
        self.assertEqual(len(labels), len(bench.TAXONOMY["topics"]) - 1)
        self.assertEqual(len(set(labels.values())), len(labels))
        self.assertIn("производство", labels["aircraft_manufacturing"])

    def test_real_profile_selection_output_and_resume_do_not_modify_input(self):
        with tempfile.TemporaryDirectory() as d:
            args = self.args(d)
            original = Path(args.input).read_bytes()
            factory = lambda *a, **kw: FakeClassifier()
            bench.run(args, factory=factory)
            data = json.loads(Path(args.output + ".json").read_text())
            self.assertEqual(len(data["results"]), 3)
            self.assertEqual(data["manifest"]["resolved_revision"], "test-revision")
            self.assertTrue(
                all(
                    r["reviewed"] == "no" and r["expected_topic"] == ""
                    for r in data["results"]
                )
            )
            self.assertEqual(Path(args.input).read_bytes(), original)
            with Path(args.output + ".csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
            self.assertTrue(
                next(r for r in rows if r["username"] == "sample_three")[
                    "name"
                ].startswith("'=")
            )
            with self.assertRaisesRegex(ValueError, "уже существует"):
                bench.run(args, factory=factory)
            args.resume = True
            bench.run(
                args,
                factory=lambda *a, **kw: self.fail(
                    "Complete report should not reload model"
                ),
            )
            args.seed = 9
            with self.assertRaisesRegex(ValueError, "изменились"):
                bench.run(args, factory=factory)

    def test_partial_run_can_resume_from_last_saved_profile(self):
        with tempfile.TemporaryDirectory() as d:
            args = self.args(d)
            classifier = FakeClassifier()
            calls = 0

            def interrupted(text, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt()
                return classifier(text, **kwargs)

            interrupted.model = classifier.model
            with self.assertRaises(KeyboardInterrupt):
                bench.run(args, factory=lambda *a, **kw: interrupted)
            self.assertEqual(
                len(json.loads(Path(args.output + ".json").read_text())["results"]), 1
            )
            args.resume = True
            bench.run(args, factory=lambda *a, **kw: classifier)
            self.assertEqual(
                len(json.loads(Path(args.output + ".json").read_text())["results"]), 3
            )

    def test_database_session_is_read_only_and_closed(self):
        from unittest.mock import MagicMock

        args = argparse.Namespace(database=True)
        conn = MagicMock()
        with (
            patch("src.db.connect", return_value=conn),
            patch("src.db.get_channels_with_meta", return_value=[]) as read,
        ):
            rows, source = bench.load_source(args)
        conn.set_session.assert_called_once_with(readonly=True)
        read.assert_called_once_with(conn)
        conn.close.assert_called_once()
        conn.commit.assert_not_called()
        self.assertEqual(rows, [])
        self.assertEqual(source, "database:active_channels")
