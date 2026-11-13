import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from src.classification_review import FIELDS, write_review
from src.topic_model import train, predict, suggest

HAS_TRAINING = importlib.util.find_spec("sklearn") is not None


def training_rows():
    rows = []
    for topic, base in [
        ("sport", "Футбол хоккей спорт стадион команда"),
        ("tech", "Программирование python linux разработка сервер"),
    ]:
        for i in range(30):
            row = {key: "" for key in FIELDS}
            row.update(
                username=f"{topic}_sample_{i}",
                name=f"{base} {i}",
                description=f"{base}. Выпуск номер {i}.",
                expected_topic=topic,
                expected_scope="unknown",
                reviewed="yes",
                split="train" if i < 25 else "test",
            )
            rows.append(row)
    return rows


class ModelApprovalTests(unittest.TestCase):
    def test_proposals_cannot_train_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            rows = training_rows()
            for row in rows:
                row["reviewed"] = "no"
            write_review(path / "review.csv", rows)
            with self.assertRaises(ValueError):
                train(path / "review.csv", path / "model.json")
            self.assertFalse((path / "model.json").exists())


@unittest.skipUnless(
    HAS_TRAINING, "Install requirements/locked-training.txt for model checks"
)
class TopicModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name)
        cls.review = cls.path / "review.csv"
        cls.model = cls.path / "model.json"
        write_review(cls.review, training_rows())
        cls.result = train(cls.review, cls.model, threshold=0.5)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_model_is_inactive_and_evaluates_only_confirmed_holdout(self):
        self.assertFalse(self.result["active"])
        self.assertEqual(self.result["training_examples"], 50)
        self.assertEqual(self.result["test_examples"], 10)
        self.assertFalse(self.result["evaluation"]["owner_overrides_used"])
        self.assertGreaterEqual(
            self.result["evaluation"]["model"]["topic_accuracy"], 0.9
        )

    def test_json_roundtrip_predictions_and_shape_validation(self):
        data = json.loads(self.model.read_text())
        rows = [
            {
                "name": "Футбол хоккей спорт",
                "username": "test_channel",
                "description": "Стадион команда матч",
            }
        ]
        result = predict(data, rows)
        self.assertEqual(result, predict(self.model, rows))
        self.assertEqual(result[0]["suggested_topic"], "sport")
        self.assertFalse(result[0]["reviewed"])
        data["coef"] = [[float("nan")]]
        with self.assertRaises(ValueError):
            predict(data, rows)

    def test_suggestions_never_modify_catalogue_or_become_approved_labels(self):
        source = self.path / "channels.json"
        output = self.path / "suggestions.json"
        original = json.dumps(
            {
                "channels": [
                    {
                        "username": "new_channel",
                        "name": "Футбол спорт",
                        "description": "Команда стадион",
                    }
                ]
            }
        )
        source.write_text(original)
        report = suggest(self.model, source, output)
        self.assertEqual(source.read_text(), original)
        self.assertEqual(report["applied"], 0)
        self.assertFalse(json.loads(output.read_text())["suggestions"][0]["reviewed"])

    def test_training_refuses_duplicate_texts_in_train_and_test(self):
        rows = training_rows()
        rows[-1]["name"] = rows[0]["name"]
        rows[-1]["description"] = rows[0]["description"]
        path = self.path / "leaking.csv"
        write_review(path, rows)
        with self.assertRaises(ValueError):
            train(path, self.path / "must_not_exist.json")

    def test_group_model_can_use_confirmed_group_without_a_leaf(self):
        rows = training_rows()
        for row in rows:
            row["expected_group"] = "sport_group" if row["expected_topic"] == "sport" else "technology"
            row["expected_topic"] = "other"
        review, output = self.path / "groups.csv", self.path / "group-model.json"
        write_review(review, rows)
        result = train(review, output, threshold=0.5, target="group")
        self.assertEqual(result["evaluation"]["fields_trained"], ["topic_group"])
        self.assertGreaterEqual(result["evaluation"]["model"]["topic_group_accuracy"], 0.9)
        suggestion = predict(output, [dict(name="Футбол хоккей спорт стадион")])[0]
        self.assertEqual(suggestion["suggested_group"], "sport_group")
        self.assertEqual(suggestion["suggested_topic"], "other")
        self.assertFalse(suggestion["reviewed"])
