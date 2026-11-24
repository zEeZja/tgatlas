"""Новые источники: только открытые метаданные, без живой сети и рабочей БД."""

import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src import catalog_growth as growth
from src.public_catalogues import PublicHTMLClient, SourceError, parse_zenodo_channels

ROOT = Path(__file__).resolve().parent.parent
CSV_URL = "https://zenodo.org/records/16949193/files/channels.csv?download=1"


def sample_csv():
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["username", "title", "about", "participants_count", "fake"])
    writer.writerows(
        [
            ["Public_News", "Новости", "Мир, политика\nи сводки", "75000", "False"],
            ["public_news", "Новости", "Сводки", "75000", "False"],
            ["military_news", "Военная аналитика", "Новости", "", "False"],
            ["small_news", "Новости", "Новости", "49999", "False"],
            ["fake_news", "Новости", "Новости", "100000", "True"],
            ["private+invite", "Private", "", "100000", "False"],
            ["support_bot", "Новости", "", "100000", "False"],
            ["recipe_channel", "Рецепты", "Выпечка", "200000", "False"],
        ]
    )
    return stream.getvalue()


class DatasetSourceTests(unittest.TestCase):
    def test_parser_filters_normalizes_and_does_not_make_topic_labels(self):
        cards = parse_zenodo_channels(sample_csv(), CSV_URL, 50000).cards
        self.assertEqual(
            [c["username"] for c in cards],
            ["recipe_channel", "public_news", "military_news"],
        )
        for card in cards:
            self.assertEqual(card["source"], "zenodo_csv")
            self.assertEqual(card["source_url"], CSV_URL)
            self.assertNotIn("subcategory", card)
            self.assertNotIn("country", card)
        self.assertIsNone(cards[-1]["catalog_subscribers"])
        with self.assertRaises(SourceError):
            parse_zenodo_channels("username,side\nchannel,neutral\n", CSV_URL)
        with self.assertRaises(SourceError):
            parse_zenodo_channels(
                "username,title,about,participants_count,fake\npublic_news,News\n",
                CSV_URL,
            )

    def test_new_plan_is_separate_and_has_no_tgramsearch(self):
        path = ROOT / "config/catalog_growth_multisource_news_military.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        jobs = growth.build_jobs(plan)
        self.assertEqual(len(jobs), 4)
        self.assertEqual({j["source"] for j in jobs}, {"tlgrm", "zenodo_csv"})
        self.assertFalse(plan["auto_export"])
        self.assertTrue(plan["stop_on_telegram_block"])
        self.assertEqual(plan["minimum_subscribers"], 50000)
        for key, value in [
            ("url", "https://example.com/private.csv"),
            ("pages", 2),
            ("md5", "unverified"),
        ]:
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["catalogues"][-1][key] = value
            with self.assertRaises(ValueError):
                growth.build_jobs(changed)

    def test_dataset_checkpoint_checksum_and_discovery_filter(self):
        text = sample_csv()
        job = dict(
            source="zenodo_csv",
            url=CSV_URL,
            phase="priority",
            pages=1,
            md5=hashlib.md5(text.encode("utf-8")).hexdigest(),
            require_discovery_hint=True,
        )
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                runner = object.__new__(growth.GrowthRun)
                runner.state = dict(
                    completed_jobs={}, completed_pages={}, job_errors={}
                )
                runner.plan = {"admission_patterns": ["Новости|Военная"]}
                runner.minimum = 50000
                runner.budget = Mock(return_value=False)
                runner.client = Mock()
                runner.client.get.return_value = text + ("changed" if corrupt else "")
                runner.process_cards = Mock(return_value=True)
                runner.save = Mock()
                runner.collect_job(job)
                runner.client.get.assert_called_once_with(
                    CSV_URL, max_age_hours=24 * 365, max_bytes=20_000_000
                )
                if corrupt:
                    runner.process_cards.assert_not_called()
                    self.assertIn(CSV_URL, runner.state["job_errors"])
                    self.assertNotIn(CSV_URL, runner.state["completed_jobs"])
                else:
                    cards = runner.process_cards.call_args.args[0]
                    self.assertEqual(
                        [c["username"] for c in cards], ["public_news", "military_news"]
                    )
                    self.assertIn(CSV_URL, runner.state["completed_jobs"])

    def test_cache_preserves_exact_csv_newlines_for_checksum(self):
        text = sample_csv()
        with tempfile.TemporaryDirectory() as folder:
            client = PublicHTMLClient({"zenodo.org"}, folder)
            with patch.object(
                client,
                "_request",
                side_effect=[(200, {}, "User-agent: *\nAllow: /"), (200, {}, text)],
            ) as request:
                first = client.get(CSV_URL, max_bytes=20_000_000)
                cached = client.get(CSV_URL, max_bytes=20_000_000)
            self.assertEqual(first, cached)
            self.assertEqual(first, text)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.kwargs, {"max_bytes": 20_000_000})

    def test_large_response_requires_explicit_bounded_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            client = PublicHTMLClient({"zenodo.org"}, folder)
            response = Mock(status_code=200, headers={})
            response.iter_content.return_value = [b"x" * 2_000_001]
            client.local.session = Mock()
            client.local.session.get.return_value = response
            with self.assertRaises(SourceError):
                client._request(CSV_URL)
            response.close.assert_called_once()
            with self.assertRaises(ValueError):
                client.get(CSV_URL, max_bytes=20_000_001)


if __name__ == "__main__":
    unittest.main()
