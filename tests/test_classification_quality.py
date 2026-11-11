import csv
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil
from unittest.mock import patch

from src.classification_review import (
    prepare,
    read_review,
    write_review,
    evaluate,
    apply_review,
    metrics,
    FIELDS,
)
from src.topic_keywords import resolve_topic
from src.geography import resolve_geography
from src.classification_text import informative_description
from src.review_page import render_review

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    dict(
        username="transport_test",
        name="Морские вести России",
        description="Профессиональное СМИ о морском и речном транспорте.",
        topic="auto",
        regions=[],
    ),
    dict(
        username="oriflame_test",
        name="Oriflame события",
        description="Бизнес-пространство для Партнёров Oriflame",
        topic="business",
        regions=[],
    ),
    dict(
        username="tyumen_test",
        name="Тюмень",
        topic="general",
        regions=["Тюменская область"],
    ),
    dict(
        username="yakutsk_test",
        name="Якутск Вечерний",
        description="Канал общественно-политической газеты",
        topic="politics",
        regions=["Республика Саха"],
    ),
    dict(
        username="penza_test",
        name="Пенза Новости Live",
        topic="general",
        regions=["Пензенская область"],
    ),
    dict(
        username="bashkir_test",
        name="ПолитКурай",
        description="Главный политический телеграм-канал Башкирии.",
        topic="politics",
        regions=["Республика Башкортостан"],
    ),
    dict(
        username="nn_sobaka",
        name="НН.Собака.ru",
        description="Мультимедиа о людях, культуре, моде, трендах в Нижнем Новгороде.",
        topic="culture",
        regions=["Нижегородская область"],
    ),
    dict(
        username="enter_test",
        name="Enter",
        description="Медиа о жизни и развлечениях новой Казани.",
        topic="general",
        regions=["Республика Татарстан"],
    ),
    dict(
        username="kazan_test",
        name="Новости Казани",
        description="Реклама: @moscow https://t.me/Moscow",
        topic="general",
        regions=["Республика Татарстан"],
    ),
    dict(
        username="coala_test",
        name="Coala",
        description="Все новости угольной отрасли: добыча, перевалка, экология.",
        topic="industry",
        regions=[],
    ),
    dict(
        username="cyber_test",
        name="НеКасперский",
        description="IT и кибербез.\nPR: @NeKasper",
        topic="tech",
        regions=[],
    ),
    dict(
        username="health_test",
        name="Нутрициолог Вероника",
        description="Помогаю со здоровьем и питанием.",
        topic="health",
        regions=[],
    ),
    dict(
        username="sochi_test",
        name="Сочи.txt",
        topic="general",
        regions=["Краснодарский край"],
    ),
    dict(
        username="saratov_test",
        name="Саратов Life",
        topic="general",
        regions=["Саратовская область"],
    ),
    dict(
        username="belgrade_test",
        name="Встречи в Белграде",
        topic="other",
        regions=["Белград"],
    ),
    dict(username="bali_test", name="Афиша на Бали", topic="culture", regions=["Бали"]),
]


def record(username="sample_test", name="Новости науки", split="test"):
    row = {key: "" for key in FIELDS}
    row.update(
        username=username,
        name=name,
        description="Наука и открытия",
        split=split,
        expected_topic="science",
        expected_scope="unknown",
        expected_country="",
        reviewed="yes",
    )
    return row


class ClassificationQualityTests(unittest.TestCase):
    def test_real_profile_regressions(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                output = resolve_topic(case, {}, geography_overrides={})
                self.assertEqual(output["subcategory"], case["topic"])
                self.assertEqual(output["geography_regions"], case["regions"])

    def test_ad_contacts_biography_and_multiple_places_do_not_invent_a_region(self):
        for description in [
            "По рекламе: Москва @manager",
            "Регистрация в РКН: Москва",
            "Автор канала родился в Москве. Пишу про инвестиции.",
            "Канал про новости Москвы и Казани",
        ]:
            with self.subTest(description=description):
                result = resolve_geography(
                    {"name": "Авторский блог", "description": description}, {}
                )
                self.assertEqual(result["geography_scope"], "unknown")
        self.assertNotIn(
            "Москва", informative_description("О науке.\nПо рекламе: Москва @manager")
        )

    def test_lexical_false_friends_do_not_assign_topics(self):
        author = resolve_topic(
            {"username": "author_test", "name": "Авторский канал"}, {}
        )
        self.assertNotEqual(author["subcategory"], "auto")
        ai = resolve_topic({"name": "Искусственный интеллект"}, {})
        self.assertNotIn("culture", ai["topic_tags"])
        transport = resolve_topic(
            {
                "name": "Морские вести",
                "username": "transport_test",
                "description": "СМИ о морском и речном транспорте",
            },
            {},
        )
        self.assertNotIn("sport", transport["topic_tags"])

    def test_manual_global_and_federal_coverage_are_not_localized(self):
        result = resolve_geography(
            {
                "username": "rian_ru",
                "name": "Новости Москвы",
                "region": "Москва",
                "country": "RU",
            }
        )
        self.assertEqual(result["geography_scope"], "country")
        result = resolve_geography(
            {"username": "test", "name": "Новости Москвы"},
            {"test": {"scope": "global"}},
        )
        self.assertEqual(result["geography_scope"], "global")

    def test_secondary_topics_and_unsubstantiated_fallback_remain_visible(self):
        result = resolve_topic(
            {
                "name": "Политика и экономика",
                "description": "Парламент, выборы, бизнес и финансы",
            },
            {},
        )
        self.assertIn(result["subcategory"], ("business", "politics"))
        self.assertTrue(result["secondary_topics"])
        result = resolve_topic(
            {"name": "Авторский канал", "subcategory": "general"}, {}
        )
        self.assertTrue(result["topic_review_required"])
        self.assertEqual(result["topic_source"], "fallback")

    def test_browser_backend_parity_for_new_profiles(self):
        script = "const fs=require('fs'),vm=require('vm');const c={window:{},localStorage:{getItem:()=>null}};vm.createContext(c);for(const f of ['taxonomy','topics'])vm.runInContext(fs.readFileSync('web/'+f+'.js','utf8'),c);console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(c.window.Topics.enrich)));"
        rows = CASES + [
            dict(
                username="bio_test",
                name="Авторский блог",
                description="Автор канала родился в Москве. Пишу про инвестиции.",
            )
        ]
        results = json.loads(
            subprocess.check_output(
                ["node", "-e", script], input=json.dumps(rows), text=True, cwd=ROOT
            )
        )
        for row, js in zip(rows, results):
            for key, value in resolve_topic(row, {}).items():
                self.assertEqual(value, js.get(key), (row["username"], key))

    def test_sampling_is_deterministic_deduplicated_and_unreviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / "source.json"
            source.write_text(
                json.dumps(
                    {
                        "channels": [
                            dict(username="SAMPLE_TEST", name="Наука"),
                            dict(username="sample_test", name="Наука"),
                            *CASES,
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            first = prepare(source, path / "first.csv", size=8)
            prepare(source, path / "second.csv", size=8)
            self.assertEqual(first["case_alias_duplicates"], 1)
            self.assertEqual(
                (path / "first.csv").read_bytes(), (path / "second.csv").read_bytes()
            )
            self.assertEqual(read_review(path / "first.csv"), [])
            self.assertTrue((path / "first.html").exists())
            with self.assertRaises(ValueError):
                evaluate(path / "first.csv")

    def test_only_confirmed_labels_apply_and_preview_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            review = path / "review.csv"
            labels = path / "labels.json"
            labels.write_text('{"version":1,"channels":{}}')
            draft = record("draft_test")
            draft["reviewed"] = "no"
            write_review(review, [record(), draft])
            self.assertEqual(apply_review(review, labels_path=labels)["confirmed"], 1)
            self.assertEqual(json.loads(labels.read_text())["channels"], {})
            apply_review(review, True, labels)
            stored = json.loads(labels.read_text())["channels"]
            self.assertEqual(list(stored), ["sample_test"])
            self.assertEqual(stored["sample_test"]["topic"], "science")

    def test_owner_labels_reach_both_backend_and_generated_browser_rules(self):
        from src import topic_keywords
        from scripts import sync_topics

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "config", root / "config")
            review = root / "review.csv"
            row = record()
            row.update(
                expected_scope="country",
                expected_country="RU",
                expected_secondary_topics="ai",
            )
            write_review(review, [row])
            apply_review(review, True, root / "config/classification_labels.json")
            with patch.object(topic_keywords, "ROOT", root):
                rules = topic_keywords.load_classification_rules()
            channel = {"username": "sample_test", "name": "Авторский канал"}
            backend = resolve_topic(channel, **rules)
            self.assertEqual(backend["topic_source"], "manual")
            self.assertEqual(backend["secondary_topics"], ["ai"])
            self.assertEqual(backend["geography_scope"], "country")
            with patch.object(sync_topics, "ROOT", root):
                taxonomy = sync_topics.render()
            script = "const fs=require('fs'),vm=require('vm');const x=JSON.parse(fs.readFileSync(0,'utf8'));const c={window:{},localStorage:{getItem:()=>null}};vm.createContext(c);vm.runInContext(x.taxonomy,c);vm.runInContext(fs.readFileSync('web/topics.js','utf8'),c);console.log(JSON.stringify(c.window.Topics.enrich(x.channel)));"
            browser = json.loads(
                subprocess.check_output(
                    ["node", "-e", script],
                    input=json.dumps(dict(taxonomy=taxonomy, channel=channel)),
                    text=True,
                    cwd=ROOT,
                )
            )
            for key, value in backend.items():
                self.assertEqual(browser.get(key), value, key)

    def test_identical_text_cannot_cross_train_test(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            write_review(
                path,
                [
                    record("first_test", split="train"),
                    record("second_test", split="test"),
                ],
            )
            with self.assertRaisesRegex(ValueError, "train и test"):
                read_review(path)

    def test_invalid_confirmed_row_cannot_partially_overwrite_owner_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            review = path / "review.csv"
            labels = path / "labels.json"
            original = '{"version":1,"channels":{}}'
            labels.write_text(original)
            bad = record("second_test")
            bad["expected_scope"] = "region"
            write_review(review, [record(), bad])
            with self.assertRaises(ValueError):
                apply_review(review, True, labels)
            self.assertEqual(labels.read_text(), original)

    def test_evaluation_reports_unknown_coverage_and_does_not_use_owner_labels(self):
        rows = [record()]
        pred = [
            dict(
                subcategory="other",
                secondary_topics=[],
                geography_scope="unknown",
                geography_regions=[],
                geography_country="",
            )
        ]
        result = metrics(rows, pred)
        self.assertEqual(result["topic_coverage"], 0)
        self.assertIsNone(result["topic_selective_precision"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            write_review(path, rows)
            report = evaluate(path)
            self.assertFalse(report["overrides_used"])
            self.assertEqual(report["topic_accuracy"], 1)

    def test_export_for_review_is_formula_and_html_safe(self):
        row = record(name='=HYPERLINK("bad")')
        row["description"] = "</script><script>window.bad=true</script>"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            write_review(path, [row])
            data = list(
                csv.DictReader(
                    io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter=";"
                )
            )
            self.assertTrue(data[0]["name"].startswith("'="))
            self.assertEqual(read_review(path)[0]["name"], row["name"])
        self.assertNotIn("</script><script>window.bad", render_review([row]))
