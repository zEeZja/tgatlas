"""The optional classifier sample must never advance the news cursor."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src import classification_samples as samples
from src.classification_review import write_review
from src.models import Post
from src.scraper import ScrapeError
from tests.test_classification_quality import record


def post(n, text, day=2):
    return Post(
        channel_id=0,
        tg_message_id=n,
        url=f"https://t.me/example/{n}",
        text=text,
        posted_at=datetime(2026, 11, day, tzinfo=timezone.utc),
    )


class ClassificationSampleTests(unittest.TestCase):
    def test_bounded_pages_filter_ads_duplicates_and_round_robin_dates(self):
        text = "Программирование на Python и разработка программного обеспечения"
        pages = [
            [
                post(20, text),
                post(19, text),
                post(18, "#реклама " + text),
                post(17, "коротко"),
            ],
            [post(16, text + " — инструменты", 1), post(15, text + " — библиотеки", 1)],
        ]
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(samples, "fetch_channel_html", return_value="html") as fetch,
            patch.object(samples, "parse_channel_page", side_effect=pages),
        ):
            result = samples.sample_channel("@Example", d, max_pages=2, limit=3)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.kwargs["before"], 17)
            self.assertEqual(len(result["posts"]), 3)
            self.assertEqual(result["excluded_ads"], 1)
            self.assertEqual(result["duplicate_texts"], 1)
            self.assertFalse(result["low_diversity"])
            self.assertEqual(result["posts"][1]["date"][:10], "2026-11-01")
            cached = samples.sample_channel("example", d, max_pages=2, limit=3)
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(fetch.call_count, 2)

    def test_failures_preserve_existing_cache(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "example.json"
            target.write_text("previous snapshot")
            for posts, error in [([], ScrapeError), ([post(1, "x")], ValueError)]:
                with (
                    self.subTest(posts=posts),
                    patch.object(
                        samples, "fetch_channel_html", return_value="unavailable"
                    ),
                    patch.object(samples, "parse_channel_page", return_value=posts),
                    patch.object(samples, "parse_channel_meta", return_value={}),
                ):
                    with self.assertRaises(error):
                        samples.sample_channel(
                            "example", d, max_pages=1, limit=3, refresh=True
                        )
                    self.assertEqual(target.read_text(), "previous snapshot")

    def test_validation_rejects_paths_and_unbounded_requests(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(samples, "fetch_channel_html") as fetch,
        ):
            for kw in [
                dict(username="../x"),
                dict(username="example", max_pages=4),
                dict(username="example", limit=99),
            ]:
                with self.assertRaises(ValueError):
                    samples.sample_channel(cache_dir=d, **kw)
            fetch.assert_not_called()

    def test_identical_or_single_day_posts_cannot_supply_automatic_suggestion(self):
        content = (
            "Программирование и разработка программ на Python. Обзор инструментов."
        )
        result = samples.evidence(
            dict(low_diversity=False, posts=[dict(text=content)] * 5)
        )
        self.assertEqual(result["usable_posts"], 1)
        self.assertFalse(result["eligible"])
        result = samples.evidence(
            dict(
                low_diversity=True,
                posts=[
                    dict(
                        text=content + str(i),
                        date=f"2026-11-0{1 + i % 2}T12:00:00+00:00",
                    )
                    for i in range(5)
                ],
            )
        )
        self.assertFalse(result["eligible"])
        result = samples.evidence(
            dict(
                low_diversity=False,
                posts=[
                    dict(
                        text=content + str(i),
                        date=f"2026-11-0{1 + i % 2}T12:00:00+00:00",
                    )
                    for i in range(5)
                ],
            )
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["topic"], "programming")
        self.assertTrue(result["suggestion_only"])

    def test_comparison_uses_confirmed_test_only_and_never_writes_labels(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            review = directory / "review.csv"
            test = record(username="example", name="Авторские заметки")
            test.update(
                description="", expected_topic="programming", expected_scope="unknown"
            )
            train = record(username="train_example", split="train")
            write_review(review, [test, train])
            cache = dict(
                username="example",
                purpose="classification_only",
                low_diversity=False,
                posts=[
                    dict(
                        text="Программирование и разработка программ на Python. Обзор инструментов "
                        + str(i),
                        date=f"2026-11-0{1 + i % 2}T12:00:00+00:00",
                    )
                    for i in range(5)
                ],
            )
            (directory / "example.json").write_text(json.dumps(cache))
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            result = samples.compare(review, directory)
            self.assertEqual(result["changed_suggestions"], 1)
            self.assertEqual(result["applied"], 0)
            self.assertFalse(result["active"])
            self.assertEqual(len(result["details"]), 1)
            self.assertEqual(
                before, {p.name: p.read_bytes() for p in directory.iterdir()}
            )
            self.assertGreater(
                result["metadata_with_post_suggestions"]["topic_accuracy"],
                result["metadata"]["topic_accuracy"],
            )

    def test_unreviewed_samples_cannot_be_used_for_quality_claims(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "review.csv"
            row = record()
            row["reviewed"] = "no"
            write_review(path, [row])
            with self.assertRaisesRegex(ValueError, "подтверждённые test"):
                samples.compare(path, d)
