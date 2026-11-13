"""Sector distinctions, optional geography and independent owner confirmations."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock

from src.classification_review import (
    apply_review, inspect_review, read_review, write_review, metrics,
    prepare,
)
from src.classification_samples import sample_from_database
from src.topic_keywords import TAXONOMY, TOPICS, resolve_topic
from tests.test_classification_quality import record

ROOT = Path(__file__).resolve().parents[1]
SECTORS = [
    ("Кредитно-финансовая сфера", "credit"),
    ("Финансово-банковская сфера", "banking"),
    ("Деятельность органов государственной власти", "government"),
    ("Промышленность", "industry"), ("Военно-промышленный комплекс", "defense_industry"),
    ("ВПК", "defense_industry"), ("Космическая отрасль", "space_industry"),
    ("Новости военно-промышленного комплекса", "defense_industry"),
    ("Топливно-энергетический комплекс", "energy"),
    ("Ракетно-космическая отрасль", "space_industry"),
    ("Авиастроение", "aircraft_manufacturing"), ("ТЭК", "energy"),
    ("Транспорт", "transport"), ("Авиатранспорт", "air_transport"),
]


class SectorClassificationTests(unittest.TestCase):
    def test_requested_sectors_are_explicit_and_have_definitions(self):
        for name, topic in SECTORS:
            with self.subTest(name=name):
                result = resolve_topic(dict(name=name), {}, geography_overrides={})
                self.assertEqual(result["subcategory"], topic)
                self.assertTrue(TOPICS[topic]["definition"])
                if topic != "government":
                    self.assertEqual(result["geography_scope"], "nonlocal")
                    self.assertFalse(result["geography_review_required"])

    def test_industrial_and_operational_subjects_are_not_confused(self):
        for name, expected in [
            ("Производство самолетов", "aircraft_manufacturing"),
            ("Новости авиакомпаний и аэропортов", "air_transport"),
            ("Производство вооружений и гособоронзаказ", "defense_industry"),
            ("Военные сводки с фронта", "military"),
            ("Астрономия и звезды", "space"),
            ("Производство спутников", "space_industry"),
            ("Правительство Москвы", "government"),
            ("Политический анализ решений правительства", "politics"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(resolve_topic(dict(name=name), {}, geography_overrides={})["subcategory"], expected)

    def test_clear_sector_descriptions_work_without_a_descriptive_channel_name(self):
        for description, topic in SECTORS:
            if description == "ВПК":
                continue
            with self.subTest(description=description):
                self.assertEqual(resolve_topic(dict(name="Отраслевой обзор", description=description), {}, geography_overrides={})["subcategory"], topic)

    def test_sector_geography_ignores_legacy_region_but_respects_owner(self):
        channel = dict(username="sample_test", name="Авиастроение Москвы", country="RU", region="Москва")
        automatic = resolve_topic(channel, {}, geography_overrides={})
        self.assertEqual(automatic["geography_scope"], "nonlocal")
        self.assertEqual(automatic["geography_regions"], [])
        self.assertEqual(automatic["geography_country"], "")
        manual = resolve_topic(channel, {}, geography_overrides={"sample_test": dict(scope="region", regions=["Москва"], country="RU")})
        self.assertEqual(manual["geography_regions"], ["Москва"])

    def test_official_private_channel_is_not_a_government_body(self):
        result = resolve_topic(dict(name="Официальный канал банка", description="Официальный канал. Новости банковской сферы"), {}, geography_overrides={})
        self.assertEqual(result["subcategory"], "banking")

    def test_territorial_news_keep_geography_and_unknown_still_means_unknown(self):
        local = resolve_topic(dict(name="ЧП Казань"), {}, geography_overrides={})
        self.assertEqual(local["subcategory"], "emergencies")
        self.assertEqual(local["geography_regions"], ["Республика Татарстан"])
        unknown = resolve_topic(dict(name="Без сведений", country="RU"), {}, geography_overrides={})
        self.assertEqual(unknown["geography_scope"], "unknown")
        self.assertNotEqual(unknown["geography_scope"], "country")

    @unittest.skipUnless(shutil.which("node"), "Node is needed for parity")
    def test_sector_browser_parity(self):
        rows = [dict(name=name, country="RU") for name, _ in SECTORS] + [
            dict(name="Авиастроение Москвы", region="Москва"), dict(name="ЧП Казань"),
            dict(name="Афиша на Бали"), dict(name="Новости Новокузнецка"),
        ]
        script = """const fs=require('fs'),vm=require('vm');const c={window:{},localStorage:{getItem:()=>null}};vm.createContext(c);
        for(const f of ['taxonomy','topics'])vm.runInContext(fs.readFileSync('web/'+f+'.js','utf8'),c);
        console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(r=>c.window.Topics.enrich(r))));"""
        browser = json.loads(subprocess.check_output(["node", "-e", script], input=json.dumps(rows), cwd=ROOT, text=True))
        for row, actual in zip(rows, browser):
            for key, value in resolve_topic(row, {}, geography_overrides={}).items():
                self.assertEqual(actual[key], value, (row, key))


class IndependentReviewTests(unittest.TestCase):
    def test_priority_queue_excludes_confirmed_topics_and_protects_owner_csv(self):
        with tempfile.TemporaryDirectory() as d:
            source, review, output = Path(d) / "channels.json", Path(d) / "owner.csv", Path(d) / "next.csv"
            source.write_text(json.dumps({"channels": [dict(username="credit_test", name="Кредитование"), dict(username="bank_test", name="Банковская сфера"), dict(username="news_test", name="Новости Москвы")]}), encoding="utf-8")
            write_review(review, [record(username="credit_test", name="Кредитование")])
            before = review.read_bytes()
            report = prepare(source, output, size=100, focus_groups=["finance"], exclude_review=review)
            self.assertEqual(report["sample"], 1)
            self.assertEqual(report["excluded_reviewed"], 1)
            self.assertEqual(read_review(output, confirmed_only=False)[0]["username"], "bank_test")
            with self.assertRaises(ValueError):
                prepare(source, review, exclude_review=review)
            self.assertEqual(review.read_bytes(), before)

    def test_topic_can_be_read_and_applied_without_guessing_invalid_country(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d) / "review.csv", Path(d) / "labels.json"
            row = record()
            row.update(expected_scope="country", expected_country="")
            write_review(source, [row])
            before = source.read_bytes()
            report = inspect_review(source)
            self.assertEqual(report["valid"], dict(topic=1, geography=0))
            self.assertEqual(report["errors"][0]["dimension"], "geography")
            self.assertEqual(len(read_review(source, dimension="topic")), 1)
            with self.assertRaises(ValueError):
                read_review(source)
            apply_review(source, True, target, dimension="topic")
            label = json.loads(target.read_text(encoding="utf-8"))["channels"][row["username"]]
            self.assertEqual(label["topic"], "science")
            self.assertNotIn("scope", label)
            self.assertEqual(source.read_bytes(), before)

    def test_v3_partial_confirmation_is_not_turned_into_both(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "review.csv"
            row = record()
            row.update(review_schema_version="3", taxonomy_version=str(TAXONOMY["version"]),
                       topic_reviewed="yes", geography_reviewed="no", reviewed="no",
                       expected_scope="country", expected_country="")
            write_review(p, [row])
            parsed = read_review(p)
            self.assertEqual(parsed[0]["geography_reviewed"], "no")
            self.assertEqual(read_review(p, dimension="geography"), [])
            prediction = resolve_topic(dict(name=row["name"]), {}, geography_overrides={})
            result = metrics(parsed, [prediction])
            self.assertEqual(result["geography_examples"], 0)
            self.assertIsNone(result["geography_exact_accuracy"])

    def test_broad_group_is_not_scored_as_wrong_unknown_leaf(self):
        row = record()
        row.update(expected_topic="other", expected_group="finance")
        pred = resolve_topic(dict(name="Кредитование"), {}, geography_overrides={})
        result = metrics([row], [pred])
        self.assertEqual(result["topic_group_accuracy"], 1)
        self.assertEqual(result["topic_examples"], 0)
        self.assertIsNone(result["topic_accuracy"])

    def test_review_city_resolves_to_region_and_does_not_accept_wrong_country(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "review.csv"
            row = record()
            row.update(expected_scope="region", expected_regions="Новокузнецк", expected_country="")
            write_review(source, [row])
            parsed = read_review(source)[0]
            self.assertEqual(parsed["expected_regions"], "Кемеровская область")
            self.assertEqual(parsed["expected_country"], "RU")
            row["expected_country"] = "DE"
            write_review(source, [row])
            with self.assertRaises(ValueError):
                read_review(source)

    def test_local_posts_need_no_network_and_do_not_write_database(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [
            (n, "Авиастроение и производство самолетов. Отраслевой обзор " + str(n),
             datetime(2026, 11, 1 + n % 3, tzinfo=timezone.utc), f"https://t.me/example/{n}")
            for n in range(12, 0, -1)
        ]
        with tempfile.TemporaryDirectory() as d:
            sample = sample_from_database("example", conn, d, limit=6)
        self.assertEqual(len(sample["posts"]), 6)
        self.assertEqual(sample["source"], "database")
        self.assertEqual(sample["distinct_dates"], 3)
        sql = conn.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("SELECT", sql)
        self.assertNotIn("UPDATE", sql)
        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()
