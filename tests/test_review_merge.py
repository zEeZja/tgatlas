"""Local recovery path must preserve owner answers and the frozen control set."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from src.classification_review import read_review, write_review
from src.review_merge import merge_reviews
from tests.test_classification_quality import record


class ReviewMergeTests(unittest.TestCase):
    def test_latest_confirmed_dimension_wins_without_confirming_a_proposal(self):
        old = record(username="demo", split="test")
        old.update(expected_topic="tech", post_evidence="Сохранённые выдержки")
        new = {
            **old,
            "split": "train",
            "expected_topic": "investing",
            "review_schema_version": "3",
            "topic_reviewed": "no",
            "geography_reviewed": "yes",
            "expected_scope": "country",
            "expected_country": "CN",
            "post_evidence": "",
        }
        with tempfile.TemporaryDirectory() as d:
            a, b, output = [
                Path(d) / name for name in ("old.csv", "new.csv", "merged.csv")
            ]
            write_review(a, [old])
            write_review(b, [new])
            report = merge_reviews([a, b], output)
            row = read_review(output)[0]
        self.assertEqual(row["expected_topic"], "tech")
        self.assertEqual(row["expected_country"], "CN")
        self.assertEqual(row["split"], "test")
        self.assertEqual(row["post_evidence"], "Сохранённые выдержки")
        self.assertEqual(report["validation"]["valid"], dict(topic=1, geography=1))

    def test_changed_profile_drops_old_unreconfirmed_labels_and_evidence(self):
        old = record(username="demo")
        old["post_evidence"] = "Посты прежнего профиля"
        new = {
            **old,
            "name": "Другой профиль",
            "review_schema_version": "3",
            "topic_reviewed": "no",
            "geography_reviewed": "yes",
            "post_evidence": "",
        }
        with tempfile.TemporaryDirectory() as d:
            a, b, output = [
                Path(d) / name for name in ("old.csv", "new.csv", "merged.csv")
            ]
            write_review(a, [old])
            write_review(b, [new])
            merge_reviews([a, b], output)
            self.assertEqual(read_review(output, dimension="topic"), [])
            self.assertEqual(read_review(output)[0]["post_evidence"], "")

    def test_explicit_corrections_preserve_originals_and_confirmation_flags(self):
        row = record(username="demo")
        row.update(
            expected_topic="defense_industry",
            expected_scope="country",
            expected_country="CH",
        )
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / "source.csv", Path(d) / "merged.csv"
            write_review(source, [row])
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            report = merge_reviews(
                [source],
                output,
                unknown_topics=["demo"],
                country_corrections=["demo=CN"],
            )
            result = read_review(output)[0]
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
        self.assertEqual(result["expected_topic"], "other")
        self.assertEqual(result["expected_country"], "CN")
        self.assertEqual(len(report["changes"]), 2)

    def test_cross_split_duplicates_and_input_overwrites_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / "source.csv", Path(d) / "merged.csv"
            write_review(
                source,
                [
                    record(username="demo", split="train"),
                    record(username="other", split="test"),
                ],
            )
            with self.assertRaisesRegex(ValueError, "train/test"):
                merge_reviews([source], output)
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, "исходную"):
                merge_reviews([source], source)
