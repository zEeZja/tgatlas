"""Contract tests for hierarchy, territory semantics, and legacy owner labels."""

import csv
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.classification_review import (
    LEGACY_FIELDS,
    apply_review,
    read_review,
    write_review,
)
from src.geography import resolve_geography
from src.places import PLACES
from src.region_aliases import REGION_ALIASES
from src.topic_keywords import GROUPS, TOPICS, TAXONOMY, resolve_topic
from tests.test_classification_quality import record

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ("Новости кибербезопасности", "cybersecurity", "technology"),
    ("Программирование на Python", "programming", "technology"),
    ("Личные финансы", "personal_finance", "finance"),
    ("Новости авиации", "aviation", "transport"),
    ("Кино и сериалы", "cinema", "culture_group"),
    ("Кулинарные рецепты", "food", "living"),
    ("Психология отношений", "psychology", "health_group"),
    ("Кошки и собаки", "pets", "living"),
    ("Книжные обзоры", "books", "culture_group"),
    ("Английский для начинающих", "languages", "knowledge_group"),
]


class ClassificationV2Tests(unittest.TestCase):
    def test_taxonomy_and_place_references_are_valid(self):
        self.assertGreaterEqual(len(TOPICS), 64)
        self.assertGreaterEqual(len(GROUPS), 19)
        self.assertEqual(len({p["id"] for p in PLACES}), len(PLACES))
        for t in TOPICS.values():
            self.assertIn(t["group"], GROUPS)
            self.assertTrue(t["definition"])
            self.assertTrue(set(t.get("refines", [])) <= TOPICS.keys())
        for p in PLACES:
            self.assertIn(p["region"], REGION_ALIASES)
            self.assertTrue(p["aliases"])

    def test_specialized_topics_and_group_paths(self):
        for name, topic, group in CASES:
            with self.subTest(name=name):
                result = resolve_topic({"name": name}, {}, geography_overrides={})
                self.assertEqual(result["subcategory"], topic)
                self.assertEqual(result["topic_path"], [group, topic])
                self.assertEqual(result["topic_granularity"], "topic")

    def test_formats_are_independent_from_subject_and_manual_formats_can_be_empty(self):
        channel = dict(username="demo_test", name="Новости кибербезопасности")
        result = resolve_topic(channel, {}, geography_overrides={})
        self.assertEqual(result["subcategory"], "cybersecurity")
        self.assertIn("news", result["publication_formats"])
        result = resolve_topic(
            channel, {}, geography_overrides={}, format_overrides={"demo_test": []}
        )
        self.assertEqual(result["publication_formats"], [])
        self.assertEqual(result["format_evidence"], ["manual"])

    def test_legacy_declared_parent_refines_but_owner_label_has_priority(self):
        channel = dict(
            username="demo_test", name="Программирование Python", subcategory="tech"
        )
        self.assertEqual(
            resolve_topic(channel, {}, geography_overrides={})["subcategory"],
            "programming",
        )
        self.assertEqual(
            resolve_topic(channel, {"demo_test": "tech"}, geography_overrides={})[
                "subcategory"
            ],
            "tech",
        )

    def test_approved_group_without_leaf_is_retained(self):
        result = resolve_topic(
            dict(username="demo_test", name="Авторский блог"),
            {"demo_test": "other"},
            group_overrides={"demo_test": "enterprise"},
            geography_overrides={},
        )
        self.assertEqual(result["topic_path"], ["enterprise"])
        self.assertEqual(result["topic_granularity"], "group")

    def test_nonlocal_requires_positive_evidence_and_clears_default_country(self):
        result = resolve_geography(
            dict(name="Бизнес", description="Программирование", country="RU"), {}
        )
        self.assertEqual(result["geography_scope"], "nonlocal")
        self.assertEqual(result["geography_country"], "")
        self.assertEqual(result["geography_relevance"], "nonterritorial")
        vague = resolve_geography(dict(name="Авторский блог", country="RU"), {})
        self.assertEqual(vague["geography_scope"], "unknown")
        national = resolve_geography(
            dict(name="Налоги", description="Налоги и экономика России", country="RU"),
            {},
        )
        self.assertEqual(national["geography_scope"], "country")
        self.assertEqual(national["geography_country"], "RU")

    def test_homonyms_need_region_and_stems_do_not_choose_nearby_city(self):
        result = resolve_geography(dict(name="Кировск сегодня"), {})
        self.assertEqual(result["geography_scope"], "unknown")
        self.assertEqual(len(result["geography_candidates"]), 2)
        result = resolve_geography(
            dict(name="Кировск сегодня", region_hint="Мурманская область"), {}
        )
        self.assertEqual(result["geography_regions"], ["Мурманская область"])
        self.assertEqual(result["geography_places"][0]["id"], "ru:kirovsk:51")
        result = resolve_geography(
            dict(name="Советск", region_hint="Кировская область"), {}
        )
        self.assertEqual(result["geography_regions"], ["Кировская область"])
        result = resolve_geography(dict(name="Нижний Новгород сейчас"), {})
        self.assertEqual(result["geography_regions"], ["Нижегородская область"])

    def test_street_biography_and_ad_contacts_do_not_localize(self):
        for row in [
            dict(name="Ленинградский проспект"),
            dict(name="Автор", description="Я училась в Пензе. Канал о музыке."),
            dict(name="Автор", description="Наука.\nПо рекламе: Пенза @contact"),
            dict(name="Москва и Ташкент"),
        ]:
            with self.subTest(row=row):
                self.assertNotIn(resolve_geography(row, {})["geography_scope"], ("region", "multi_region"))

    def test_legacy_csv_keeps_checked_topic_and_scope_without_new_format_label(self):
        with tempfile.TemporaryDirectory() as d:
            review = Path(d) / "legacy.csv"
            target = Path(d) / "labels.json"
            row = record(name="Новости бизнеса")
            row.update(
                expected_topic="business",
                expected_scope="country",
                expected_country="RU",
            )
            with review.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f, LEGACY_FIELDS, delimiter=";", extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerow(row)
            parsed = read_review(review)[0]
            self.assertEqual(parsed["review_schema_version"], "1")
            self.assertEqual(parsed["expected_group"], "finance")
            with patch("src.classification_review.ROOT", Path(d)):
                target.parent.joinpath("config").mkdir()
                apply_review(review, True)
                labels = json.loads(
                    (Path(d) / "config/classification_labels.json").read_text()
                )
            label = labels["channels"][row["username"]]
            self.assertEqual(label["topic"], "business")
            self.assertEqual(label["scope"], "country")
            self.assertNotIn("formats", label)
            self.assertEqual(labels["taxonomy_version"], TAXONOMY["version"])

    def test_changed_profile_marks_review_without_discarding_manual_answer(self):
        row = dict(username="demo_test", name="Автор", description="Теперь про спорт")
        result = resolve_topic(
            row,
            {"demo_test": "business"},
            geography_overrides={},
            review_metadata={"demo_test": dict(name="Автор", description="Про бизнес")},
        )
        self.assertTrue(result["classification_review_stale"])
        self.assertTrue(result["topic_review_required"])
        self.assertEqual(result["subcategory"], "business")

    def test_v2_review_can_confirm_group_formats_and_nonlocal_scope(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v2.csv"
            row = record()
            row.update(
                review_schema_version="2",
                taxonomy_version="6",
                expected_topic="other",
                expected_group="enterprise",
                expected_formats="analysis,education",
                expected_scope="nonlocal",
                expected_country="RU",
                review_reason="missing_topic",
                proposed_topic="Маркетплейсы",
            )
            write_review(p, [row])
            parsed = read_review(p)[0]
            self.assertEqual(parsed["expected_country"], "")
            self.assertEqual(parsed["proposed_topic"], "Маркетплейсы")

    @unittest.skipUnless(shutil.which("node"), "Node needed for browser parity")
    def test_new_cases_have_python_browser_parity(self):
        rows = [dict(name=name) for name, _, _ in CASES] + [
            dict(name="Кировск сегодня"),
            dict(name="Советск", region_hint="Тульская область"),
            dict(name="Нижний Новгород"),
            dict(name="Автор", description="Я училась в Пензе. Канал о музыке."),
            dict(name="Бизнес", description="Программирование", country="RU"),
            dict(name="Налоги", description="Налоги и экономика России", country="RU"),
            dict(name="Москва и Ташкент"),
            dict(name="Ленинградский проспект"),
        ]
        script = """const fs=require('fs'),vm=require('vm');const c={window:{},localStorage:{getItem:()=>null}};vm.createContext(c);
        for(const f of ['taxonomy','topics'])vm.runInContext(fs.readFileSync('web/'+f+'.js','utf8'),c);
        console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(r=>c.window.Topics.enrich(r))));"""
        out = json.loads(
            subprocess.check_output(
                ["node", "-e", script], input=json.dumps(rows), text=True, cwd=ROOT
            )
        )
        for row, browser in zip(rows, out):
            for k, v in resolve_topic(row, {}, geography_overrides={}).items():
                self.assertEqual(browser[k], v, (row, k))
