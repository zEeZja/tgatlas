from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.catalog_collection import (
    CatalogFetcher,
    collect,
    suitable_posts,
    extend_time_window,
)
from src.classification_runtime import taxonomy, merge_result, profile, validate_results
from src.geography_v11 import mentions, reference, GeographyResolver, manual_geography
from src.taxonomy_pilot import PilotStopped


def sample(username="demo", count=8):
    now = datetime.now(timezone.utc)
    return dict(
        username=username,
        purpose="classification_only",
        fetched_at=now.isoformat(),
        posts=[
            dict(
                text=f"Самостоятельная содержательная публикация о городской жизни номер {i}.",
                date=(now - timedelta(days=i % 3, hours=1)).isoformat(),
                message_id=i + 1,
            )
            for i in range(count)
        ],
    )


class PrimaryV11Tests(unittest.TestCase):
    def test_approved_taxonomy_is_namespaced_and_does_not_remove_legacy(self):
        tx = taxonomy()
        self.assertEqual(len(tx["topics"]), 67)
        self.assertTrue(all(t["id"].startswith("v11_") for t in tx["topics"]))
        self.assertNotIn("government", tx["legacy_migration"])
        self.assertNotIn("business", tx["legacy_migration"])

    def test_browser_contract_is_generated_from_primary_taxonomy(self):
        from scripts.sync_classification_v11 import render
        from src.classification_runtime import ROOT

        self.assertEqual(
            (ROOT / "web/classification-v11-taxonomy.js").read_text(encoding="utf-8"),
            render(),
        )

    def test_all_project_regions_and_world_country_reference(self):
        from src.region_aliases import REGION_ALIASES

        entities, _ = reference()
        self.assertEqual(
            {x["region"] for x in entities if x["kind"] == "region"},
            set(REGION_ALIASES),
        )
        self.assertGreaterEqual(
            len([x for x in entities if x["kind"] == "country"]), 249
        )
        self.assertNotIn("country:EU", {x["id"] for x in entities})

    def test_country_and_region_name_linking(self):
        cases = [
            ("Новости Владивостока", "region:Приморский край"),
            ("Новости Московской области", "region:Московская область"),
            ("Жизнь в Китае", "country:CN"),
            ("Новости Швейцарии", "country:CH"),
            ("Афиша Белграда", "country:RS"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertIn(expected, mentions(text))
        self.assertNotIn("region:Москва", mentions("Новости Московской области"))
        self.assertNotIn("country:AT", mentions("Новости Австралии"))
        self.assertIn("country:AU", mentions("Новости Австралии"))
        self.assertNotIn("country:NE", mentions("Новости Нигерии"))
        self.assertIn("country:NG", mentions("Новости Нигерии"))

    def test_eight_distinct_recent_posts_and_no_adverts(self):
        data = sample(count=12)
        data["posts"].append(data["posts"][0])
        data["posts"].append(
            dict(
                text="#реклама " + "Очень длинная реклама " * 10,
                date=datetime.now(timezone.utc).isoformat(),
            )
        )
        result = suitable_posts(data)
        self.assertEqual(len(result), 8)
        self.assertGreater(len({p["date"][:10] for p in result}), 1)
        data["fetched_at"] = (
            datetime.now(timezone.utc) - timedelta(days=9)
        ).isoformat()
        self.assertEqual(suitable_posts(data), [])

    def test_collector_is_offline_by_default_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "cache"
            cache.mkdir()
            (cache / "demo.json").write_text(json.dumps(sample()), encoding="utf-8")
            session = Mock(side_effect=AssertionError("network prohibited"))
            rows = [dict(username="demo", name="Канал")]
            state = collect(
                rows,
                root / "run",
                database=False,
                cache_directories=[cache],
                session=session,
            )
            self.assertEqual(state["requests"], 0)
            self.assertEqual(state["channels"]["demo"]["status"], "usable")
            session.get.assert_not_called()
            self.assertTrue((root / "run/posts/demo.json").exists())
            with self.assertRaises(ValueError):
                collect([dict(username="second")], root / "run", database=False)

    def test_http_reserves_budget_before_dispatch_and_has_no_second_page(self):
        state = dict(
            deadline=datetime.now(timezone.utc).timestamp() + 600,
            requests=0,
            request_budget=1,
            requests_by_channel={},
        )
        save = Mock()
        session = Mock()

        def get(*a, **kw):
            self.assertEqual(state["requests"], 1)
            save.assert_called_once()
            self.assertFalse(kw["allow_redirects"])
            return Mock(status_code=200, text="html")

        session.get.side_effect = get
        fetcher = CatalogFetcher(state, save, session)
        self.assertEqual(fetcher("demo"), "html")
        with self.assertRaises(ValueError):
            fetcher("demo")
        with self.assertRaises(PilotStopped):
            fetcher("other")
        self.assertEqual(session.get.call_count, 1)

    def test_rate_limit_stops_and_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Mock()
            session.get.return_value = Mock(status_code=429)
            rows = [dict(username="demo"), dict(username="second")]
            state = collect(rows, folder, http=True, database=False, session=session)
            self.assertTrue(state["stopped"])
            self.assertEqual(state["requests"], 1)
            with self.assertRaises(ValueError):
                collect(rows, folder, http=True, database=False, session=session)
            self.assertEqual(session.get.call_count, 1)

    def test_time_extension_preserves_request_budget_and_progress(self):
        state = dict(
            deadline=100,
            requests=516,
            request_budget=3000,
            requests_by_channel={"demo": 1},
            channels={"demo": {"status": "usable"}},
            stopped=True,
            stop_reason="Истёк лимит времени",
        )
        extend_time_window(state, 8, now=200)
        self.assertEqual(state["deadline"], 200 + 8 * 3600)
        self.assertEqual(state["requests"], 516)
        self.assertEqual(state["request_budget"], 3000)
        self.assertEqual(state["requests_by_channel"], {"demo": 1})
        self.assertEqual(state["channels"], {"demo": {"status": "usable"}})
        self.assertFalse(state["stopped"])
        self.assertEqual(state["time_extensions"][0]["previous_deadline"], 100)
        with self.assertRaises(ValueError):
            extend_time_window(state, 8, now=201)

    def test_time_extension_cannot_bypass_rate_limits_or_exhausted_budget(self):
        for reason in (
            "Ограничение Telegram: HTTP 429",
            "Ограничение Telegram: HTTP 403",
            "Исчерпан лимит запросов",
        ):
            state = dict(
                deadline=100,
                requests=1,
                request_budget=3000,
                stopped=True,
                stop_reason=reason,
            )
            before = dict(state)
            with self.assertRaises(ValueError):
                extend_time_window(state, 8, now=200)
            self.assertEqual(state, before)
        state = dict(
            deadline=100,
            requests=3000,
            request_budget=3000,
            stopped=True,
            stop_reason="Истёк лимит времени",
        )
        with self.assertRaises(ValueError):
            extend_time_window(state, 8, now=200)

    def test_manual_old_answer_is_preserved_even_if_new_topic_is_ambiguous(self):
        row = dict(
            username="demo",
            name="Канал",
            description="Описание",
            subcategory="business",
            topic_source="manual",
            topic_group="finance",
            geography_source="manual",
            geography_scope="country",
            geography_country="CN",
            geography_regions=[],
        )
        tx = taxonomy()
        topic = tx["topics"][0]
        result = dict(
            classification_engine="semantic-v11",
            classification_profile=profile(row),
            subcategory=topic["id"],
            topic_group=topic["group"],
            topic_source="semantic-v11-proposal",
            topic_review_required=True,
        )
        merged = merge_result(row, {"demo": result})
        self.assertEqual(merged["subcategory"], "business")
        self.assertEqual(merged["geography_country"], "CN")
        self.assertIn("semantic_proposal", merged)

    def test_validation_rejects_unknown_topic_before_publication(self):
        result = dict(
            format="atlas-primary-results-v11",
            taxonomy_sha256=taxonomy()["fingerprint"],
            channels={
                "demo": dict(
                    classification_engine="semantic-v11", subcategory="missing"
                )
            },
        )
        with self.assertRaises(ValueError):
            validate_results(result)

    def test_foreign_regions_are_not_manual_russian_regions(self):
        with self.assertRaises(ValueError):
            manual_geography(dict(scope="region", country="CN", regions=["Москва"]))

    def test_static_export_preserves_extra_channels_and_backs_up_user_js(self):
        from src.primary_export import export_current

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "web/data").mkdir(parents=True)
            rows = [
                dict(username="first", name="One", description="", subcategory="other"),
                dict(
                    username="later",
                    name="Added later",
                    description="",
                    subcategory="other",
                ),
            ]
            source = root / "web/data/channels.json"
            source.write_text(json.dumps(dict(channels=rows)), encoding="utf-8")
            js = root / "web/data/channels.js"
            js.write_text("USER CHANGES", encoding="utf-8")
            tx = taxonomy()
            topic = tx["topics"][0]
            proposal = dict(
                classification_profile=profile(rows[0]),
                classification_engine="semantic-v11",
                subcategory=topic["id"],
                topic_group=topic["group"],
            )
            with (
                patch("src.primary_export.ROOT", root),
                patch(
                    "src.primary_export.load_results", return_value={"first": proposal}
                ),
            ):
                result = export_current(apply=True)
            exported = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(len(exported["channels"]), 2)
            self.assertEqual(exported["channels"][1]["username"], "later")
            self.assertEqual(
                (Path(result["backup"]) / "channels.js").read_text(encoding="utf-8"),
                "USER CHANGES",
            )

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "Нужен numpy")
    def test_full_classifier_reads_eight_posts_without_network_or_model_download(self):
        import numpy as np
        from src.classifier_v11 import classify
        from src.classification_runtime import policy

        class Encoder:
            settings = policy()["encoder"]
            stats = {}

            def encode(self, texts):
                return np.tile(np.array([1.0, 0.0], dtype=np.float32), (len(texts), 1))

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "posts").mkdir()
            data = sample(count=12)
            for post in data["posts"]:
                post["text"] += " События Владивостока и новости для жителей."
            (root / "posts/demo.json").write_text(json.dumps(data), encoding="utf-8")
            result = classify(
                [
                    dict(
                        username="demo",
                        name="Новости Владивостока",
                        description="Жизнь города",
                    )
                ],
                root,
                Encoder(),
            )
            validate_results(result)
            from src.primary_review import render_view
            from src.classification_runtime import publish_results

            rows = [
                dict(
                    username="demo",
                    name="Новости Владивостока",
                    description="Жизнь города",
                )
            ]
            self.assertIn("atlas-v11-view-v1", render_view(rows, result))
            catalog = root / "catalog.json"
            publish_results(result, catalog)
            before = catalog.read_bytes()
            broken = json.loads(json.dumps(result))
            broken["channels"]["demo"]["subcategory"] = "missing"
            with self.assertRaises(ValueError):
                publish_results(broken, catalog)
            self.assertEqual(catalog.read_bytes(), before)
            exported = merge_result(rows[0], result["channels"])
            self.assertEqual(exported["post_routing"]["usable"], 8)
            self.assertEqual(
                len([p for p in exported["topic_evidence"] if p["source"] == "post"]), 2
            )
            self.assertEqual(
                len(
                    [
                        p
                        for p in result["channels"]["demo"]["topic_evidence"]
                        if p["source"] == "post"
                    ]
                ),
                8,
            )
            row = result["channels"]["demo"]
            self.assertEqual(row["post_routing"]["usable"], 8)
            self.assertEqual(row["geography_regions"], ["Приморский край"])
            self.assertTrue(row["topic_review_required"])
            self.assertTrue(row["geography_review_required"])

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "Нужен numpy")
    def test_incidental_world_mentions_do_not_define_region(self):
        import numpy as np

        class Encoder:
            def encode(self, texts):
                return np.tile(np.array([1.0, 0.0], dtype=np.float32), (len(texts), 1))

        resolver = GeographyResolver(Encoder())
        result = resolver.resolve(
            dict(name="Мировые новости", description="События со всего мира"),
            [dict(text="В Москве прошло международное совещание.")],
            np.array([1.0, 0.0]),
            np.array([[1.0, 0.0]]),
        )
        self.assertEqual(result["geography_scope"], "unknown")
        self.assertTrue(result["geography_review_required"])


if __name__ == "__main__":
    unittest.main()
