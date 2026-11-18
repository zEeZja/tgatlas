"""Safety and contracts of profile→posts semantic classification."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.semantic_pipeline import (
    cleaned_profile,
    geography,
    needs_posts,
    usable_posts,
    PostProvider,
    merge_result,
    publish_results,
    load_results,
)
from src.classification_review import write_review
from src.semantic_model import train_pilot, load_model, head_predict
from tests.test_classification_quality import record

ROOT = Path(__file__).resolve().parent.parent
HAS_ML = importlib.util.find_spec("sklearn") is not None


def sample(username="demo", same_date=False):
    return dict(
        username=username,
        purpose="classification_only",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        posts=[
            dict(
                text="Разные подробные новости города Пензы и события жителей "
                + str(i),
                date=f"2026-11-0{1 if same_date else 1 + i % 2}T12:00:00+00:00",
                message_id=i,
                url=f"https://t.me/{username}/{i}",
            )
            for i in range(4)
        ],
    )


class SemanticEvidenceTests(unittest.TestCase):
    def test_founder_is_not_subject_and_boilerplate_requires_posts(self):
        row = dict(
            name="Агентство мастерства",
            description="Учредители организации — Правительство РФ.\nНаш сайт:",
            note="Нужно посмотреть посты",
        )
        clean = cleaned_profile(row)
        self.assertNotIn("Правительство", clean["description"])
        self.assertIn("sparse_profile", needs_posts(row))
        self.assertIn("owner_requested_posts", needs_posts(row))
        self.assertIn("Правительство", row["description"])

    def test_explicit_region_survives_business_subject(self):
        output = geography(
            dict(
                name="Мой бизнес | Пермский край",
                description="Поддержка предпринимательства",
            )
        )
        self.assertEqual(output["geography_scope"], "region")
        self.assertEqual(output["geography_regions"], ["Пермский край"])
        self.assertEqual(output["geography_country"], "RU")

    def test_country_focus_does_not_inherit_audience_country(self):
        result = geography(
            dict(
                name="Китай Инвест",
                description="Инвестиции в Китай для российских инвесторов",
                country="RU",
            )
        )
        self.assertEqual(result["geography_country"], "CN")
        self.assertEqual(result["geography_scope"], "country")
        vague = geography(dict(name="Блог", description="Заметки автора", country="RU"))
        self.assertEqual(vague["geography_scope"], "unknown")

    def test_post_mentions_are_candidates_not_automatic_scope(self):
        result = geography(dict(name="Блог", description="Заметки"), sample()["posts"])
        self.assertEqual(result["geography_scope"], "unknown")
        self.assertEqual(result["geography_candidates"], ["Пензенская область"])
        self.assertTrue(result["geography_review_required"])
        self.assertTrue(
            all(x["source"] == "post" for x in result["geography_evidence"])
        )

    def test_diversity_is_recomputed_and_ads_duplicates_removed(self):
        self.assertEqual(usable_posts(sample(same_date=True))[0], [])
        data = sample()
        data["posts"] = [data["posts"][0]] * 5
        self.assertEqual(usable_posts(data)[0], [])
        data = sample()
        data["posts"][0]["text"] = "#реклама " + data["posts"][0]["text"]
        self.assertEqual(len(usable_posts(data)[0]), 3)

    def test_http_budget_cached_samples_and_no_news_writes(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "src.semantic_pipeline.sample_channel",
                side_effect=lambda username, *a, **kw: sample(username),
            ) as fetch,
        ):
            provider = PostProvider(directory, allow_http=True, max_http_channels=1)
            self.assertEqual(len(provider(dict(username="demo"))[0]), 4)
            self.assertEqual(
                provider(dict(username="second"))[1]["status"], "http_budget_exhausted"
            )
            self.assertEqual(fetch.call_count, 1)
            # A fresh cache is read without another HTTP request.
            Path(directory, "second.json").write_text(json.dumps(sample("second")))
            self.assertEqual(provider(dict(username="second"))[1]["source"], "cache")
            self.assertEqual(fetch.call_count, 1)

    def test_cache_cannot_substitute_another_channel(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "demo.json").write_text(json.dumps(sample("other")))
            posts, detail = PostProvider(d)(dict(username="demo"))
            self.assertEqual(posts, [])
            self.assertIn("invalid_cache", detail["errors"])

    def test_manual_labels_and_profile_freshness_have_priority(self):
        row = dict(
            username="demo",
            name="Тест",
            description="Описание",
            subcategory="politics",
            topic_source="manual",
            topic_group="politics_group",
            geography_source="manual",
            geography_scope="country",
            geography_country="RU",
        )
        candidate = dict(
            classification_engine="semantic-v9",
            classification_profile=dict(name="Тест", description="Описание"),
            subcategory="science",
            topic_group="knowledge_group",
            topic_source="semantic-v9",
            geography_source="profile",
            geography_scope="unknown",
        )
        output = merge_result(row, {"demo": candidate})
        self.assertEqual(output["subcategory"], "politics")
        self.assertEqual(output["geography_scope"], "country")
        stale = merge_result({**row, "description": "Изменено"}, {"demo": candidate})
        self.assertTrue(stale["semantic_result_stale"])
        self.assertEqual(stale["subcategory"], "politics")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.json"
            publish_results({"demo": candidate}, path)
            self.assertEqual(load_results(path)["demo"], candidate)

    def test_pilot_cannot_silently_replace_current_map_topic(self):
        row = dict(
            username="demo",
            name="Канал",
            description="Описание",
            subcategory="science",
            topic_group="knowledge_group",
            topic_source="heuristic",
        )
        candidate = dict(
            classification_engine="semantic-v9",
            classification_profile=dict(name="Канал", description="Описание"),
            subcategory="programming",
            topic_group="technology",
            topic_source="semantic-v9",
            topic_review_required=True,
        )
        merged = merge_result(row, {"demo": candidate})
        self.assertEqual(merged["subcategory"], "science")
        self.assertEqual(merged["semantic_proposal"]["subcategory"], "programming")
        self.assertEqual(merged["topic_source"], "legacy_until_review")
        self.assertTrue(merged["topic_review_required"])

    def test_expired_post_evidence_does_not_reuse_a_published_answer(self):
        row = dict(
            username="demo", name="Тест", description="Описание", subcategory="science"
        )
        result = dict(
            subcategory="tech",
            topic_group="technology",
            classification_profile=dict(name="Тест", description="Описание"),
            post_routing=dict(status="usable", fetched_at="2000-01-01T00:00:00+00:00"),
        )
        merged = merge_result(row, {"demo": result})
        self.assertEqual(merged["subcategory"], "science")
        self.assertTrue(merged["semantic_result_stale"])

    def test_scope_model_cannot_erase_explicit_place(self):
        scores = [dict(id="nonlocal", score=0.95), dict(id="region", score=0.05)]
        result = geography(
            dict(name="Новости Пензы", description="Городские новости"),
            scope_prediction=scores,
            scope_support={"nonlocal": 10},
        )
        self.assertEqual(result["geography_scope"], "region")
        vague = geography(
            dict(name="Блог", description="Заметки"),
            scope_prediction=scores,
            scope_support={"nonlocal": 10},
        )
        self.assertEqual(vague["geography_scope"], "nonlocal")
        self.assertTrue(vague["geography_review_required"])

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_browser_preserves_semantic_result_and_local_correction(self):
        script = """const fs=require('fs'),vm=require('vm');const c={window:{},localStorage:{getItem:()=>null,setItem:()=>{}}};vm.createContext(c);
        for(const f of ['taxonomy','topics'])vm.runInContext(fs.readFileSync('web/'+f+'.js','utf8'),c);
        const row={username:'v9_test',name:'Криптовалюты',description:'Рынок криптовалют',classification_engine:'semantic-v9',classification_profile:{name:'Криптовалюты',description:'Рынок криптовалют'},subcategory:'programming',original_subcategory:'crypto',topic_group:'technology',topic_group_label:'Технологии',topic_source:'semantic-v9',classification_version:9,geography_scope:'region',geography_regions:['Пермский край'],geography_country:'RU',topic_review_required:true};
        const one=c.window.Topics.enrich(row);c.window.Topics.setLocal('v9_test','gaming');const two=c.window.Topics.enrich(row);
        console.log(JSON.stringify([one.subcategory,one.geography_scope,two.subcategory]));"""
        output = json.loads(
            subprocess.check_output(["node", "-e", script], cwd=ROOT, text=True)
        )
        self.assertEqual(output, ["crypto", "region", "gaming"])


@unittest.skipUnless(HAS_ML, "Optional ML dependencies tested in training CI")
class SemanticTrainingTests(unittest.TestCase):
    class Encoder:
        name = "test-encoder"
        revision = "test"
        dimension = 4

        def encode(self, texts):
            import numpy as np

            # Deterministic input features; no model downloads in CI.
            return np.asarray(
                [[len(t) % 7 + 1, len(t) % 3 + 1, 1, 2] for t in texts], dtype=float
            )

    def test_training_uses_only_confirmed_train_and_reports_unseen_classes(self):
        with tempfile.TemporaryDirectory() as d:
            rows = []
            for i in range(12):
                r = record(
                    username="train_" + str(i),
                    name="Обучающий " + str(i),
                    split="train",
                )
                r.update(
                    expected_topic="science" if i % 2 else "tech",
                    expected_group="knowledge_group" if i % 2 else "technology",
                )
                rows.append(r)
            for i in range(3):
                r = record(
                    username="test_" + str(i),
                    name="Контрольный " + str(i),
                    split="test",
                )
                r.update(expected_topic="military", expected_group="military_group")
                rows.append(r)
            # This unconfirmed answer must never become a training label.
            r = record(username="not_reviewed", name="Непроверенный", split="train")
            r.update(reviewed="no", expected_topic="investing")
            rows.append(r)
            review = Path(d) / "review.csv"
            out = Path(d) / "model.json"
            write_review(review, rows)
            model = train_pilot(review, out, self.Encoder())
            self.assertEqual(model["training_examples"], 12)
            self.assertEqual(model["test_examples"], 3)
            self.assertNotIn("military", model["heads"]["topic"]["classes"])
            self.assertNotIn("investing", model["heads"]["topic"]["classes"])
            self.assertEqual(
                model["evaluation"]["topic"]["unseen_test_classes"], ["military"]
            )
            self.assertEqual(model["evaluation"]["topic"]["top1_accuracy"], 0)
            self.assertTrue(model["pilot_only"])
            self.assertFalse(model["active"])
            self.assertEqual(load_model(out)["format"], "atlas-semantic-v9")
            broken = json.loads(out.read_text())
            broken["heads"]["topic"]["coef"][0] = [1]
            out.write_text(json.dumps(broken))
            with self.assertRaises(ValueError):
                load_model(out)

    def test_softmax_and_binary_heads_produce_normalized_rankings(self):
        result = head_predict(
            dict(classes=["ai", "tech"], coef=[[1, 0]], intercept=[0]), [[1, 0]]
        )
        self.assertAlmostEqual(sum(x["score"] for x in result[0]), 1)
        self.assertEqual(result[0][0]["id"], "tech")
