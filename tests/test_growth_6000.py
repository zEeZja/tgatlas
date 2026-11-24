"""Focused public discovery, safe map merge and bounded-memory geometry."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from src import catalog_growth as growth
from src.catalog_growth_merge import prepare, publish
from src.scraper import ScrapeError, fetch_channel_html
from src.topic_keywords import load_classification_rules
from src.public_catalogues import PublicHTMLClient, SourceNotFound

ROOT = Path(__file__).resolve().parent.parent


class Growth6000Tests(unittest.TestCase):
    def test_source_404_and_410_are_typed_without_blocking_host(self):
        for status in (404, 410):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                client = PublicHTMLClient({"tgramsearch.com"}, folder)
                with patch.object(
                    client, "_request", side_effect=[(404, {}, ""), (status, {}, "")]
                ):
                    with self.assertRaises(SourceNotFound):
                        client.get("https://tgramsearch.com/join/123")
                self.assertEqual(client.blocked, set())

    def test_missing_source_card_is_not_retried_or_checked_on_telegram(self):
        client = Mock()
        client.get.side_effect = SourceNotFound("tgramsearch.com: HTTP 404")
        card = {
            "detail_url": "https://tgramsearch.com/join/123",
            "source": "tgramsearch",
        }
        claims = growth.ProfileClaims(set(), {}, 10, stop_on_block=True)
        with patch.object(growth, "fetch_channel_html") as telegram:
            result = growth._verify(card, client, claims, "priority")
            self.assertEqual(result["outcome"], "source_not_found")
            telegram.assert_not_called()
        self.assertFalse(claims.stop_event.is_set())

    def setUp(self):
        self.plan = json.loads(
            (ROOT / "config/catalog_growth_news_military.json").read_text(
                encoding="utf-8"
            )
        )
        self.rules = load_classification_rules()

    def test_focused_plan_does_not_export_or_treat_search_as_classification(self):
        self.assertFalse(self.plan["auto_export"])
        self.assertTrue(self.plan["stop_on_telegram_block"])
        self.assertEqual(growth.minimum_audience(self.plan), 50000)
        meta = dict(
            username="unseen_reporter",
            name="Военно-аналитический центр",
            description="Военная аналитика и фронтовые сводки.",
            subscribers=70000,
        )
        self.assertEqual(
            growth.decision(meta, self.plan, "priority", self.rules)[0], "eligible"
        )
        low = dict(meta, subscribers=49999)
        self.assertEqual(
            growth.decision(low, self.plan, "priority", self.rules)[0], "below_minimum"
        )
        unrelated = dict(
            meta,
            name="Кулинарные рецепты",
            description="Новости нашей кухни. Рецепты блюд, выпечка и десерты.",
        )
        self.assertEqual(
            growth.decision(unrelated, self.plan, "other", self.rules)[0],
            "deferred_topic",
        )
        unrelated = dict(
            meta, name="Заметки", description="Личный дневник без определённой темы"
        )
        self.assertNotEqual(
            growth.decision(unrelated, self.plan, "priority", self.rules)[0], "eligible"
        )

    def test_telegram_block_stops_other_worker_requests(self):
        claims = growth.ProfileClaims(set(), {}, 10, stop_on_block=True)
        card = dict(
            username="public_news",
            source="tlgrm",
            source_url="https://tlgrm.ru/channels/economics",
        )
        with patch(
            "src.catalog_growth.fetch_channel_html",
            side_effect=ScrapeError("HTTP 429", status_code=429, retry_after=120),
        ) as fetch:
            result = growth._verify(card, Mock(), claims, "priority")
        self.assertTrue(result["telegram_blocked"])
        self.assertTrue(claims.stop_event.is_set())
        self.assertEqual(result["retry_after_seconds"], 120)
        self.assertEqual(fetch.call_args.kwargs["attempts"], 1)
        with patch("src.scraper._get_session") as session:
            with self.assertRaises(ScrapeError):
                fetch_channel_html(
                    "public_news",
                    profile=True,
                    attempts=1,
                    stop_event=claims.stop_event,
                )
            session.assert_not_called()

    def test_rate_limit_is_one_attempt_in_focused_mode(self):
        response = Mock(status_code=429, headers={"Retry-After": "120"})
        session = Mock()
        session.get.return_value = response
        with (
            patch("src.scraper._get_session", return_value=session),
            patch("src.scraper._gate"),
        ):
            with self.assertRaises(ScrapeError) as raised:
                fetch_channel_html("public_news", profile=True, attempts=1)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.retry_after, 120)

    def test_blocked_checkpoint_requires_operator_and_retry_after(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            plan = ROOT / "config/catalog_growth_news_military.json"
            state = dict(
                version=2,
                plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),
                minimum_subscribers=50000,
                status="telegram_blocked",
                telegram_retry_not_before=time.time() + 3600,
            )
            path.write_text(json.dumps(state), encoding="utf-8")
            conn = Mock()
            conn.cursor.return_value.__enter__ = Mock(
                return_value=Mock(fetchone=Mock(return_value=(True,)))
            )
            conn.cursor.return_value.__exit__ = Mock(return_value=False)
            for resume in (False, True):
                with (
                    patch.object(growth.db, "connect", return_value=conn),
                    patch.object(growth, "_channel_count", return_value=3000),
                    patch.object(growth, "GrowthRun") as runner,
                ):
                    with self.assertRaises(ValueError):
                        growth.run(
                            apply=True,
                            plan_path=plan,
                            state_path=path,
                            resume_after_block=resume,
                        )
                    runner.assert_not_called()

            state.update(
                telegram_retry_not_before=time.time() - 1,
                stop_reason="HTTP 429",
                retry_cards={"public_news": {"phase": "priority"}},
            )
            path.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(growth.db, "connect", return_value=conn),
                patch.object(growth, "_channel_count", return_value=3000),
                patch.object(growth, "GrowthRun") as runner,
            ):
                growth.run(
                    apply=True, plan_path=plan, state_path=path, resume_after_block=True
                )
                resumed = runner.call_args.args[3]
                self.assertEqual(resumed["retry_cards"], state["retry_cards"])
                self.assertNotIn("stop_reason", resumed)
                self.assertEqual(len(resumed["block_resumptions"]), 1)

    def test_disabled_autoexport_at_finish(self):
        runner = object.__new__(growth.GrowthRun)
        runner.plan = self.plan
        runner.jobs = []
        runner.state = {"retry_cards": {}, "job_errors": {}}
        runner.claims = Mock()
        runner.claims.stop_event.is_set.return_value = False
        runner.current = runner.target = 6000
        runner.save = Mock(
            side_effect=lambda status: runner.state.update(
                {
                    k: status
                    for k in (
                        "status",
                        "initial_count",
                        "current_count",
                        "target",
                        "total_imported",
                        "this_run",
                        "imported_topics",
                        "imported_sources",
                        "updated_at",
                    )
                }
            )
        )
        with patch.object(growth.exporter, "export_channels") as export:
            runner.execute()
            export.assert_not_called()


class GrowthMapMergeTests(unittest.TestCase):
    def test_preserves_existing_model_labels_and_backs_up_both_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            web = root / "web/data"
            web.mkdir(parents=True)
            old = dict(
                username="existing_channel",
                name="Owner title",
                subcategory="v11_medicine",
                topic_source="manual",
                custom_owner_data={"x": 1},
            )
            (web / "channels.json").write_text(
                json.dumps(dict(channels=[old])), encoding="utf-8"
            )
            (web / "channels.js").write_text("INDEPENDENT USER JS", encoding="utf-8")
            additions = [
                dict(old, name="DB title", subcategory="other"),
                dict(username="new_channel", name="New news", subscribers=90000),
            ]
            before = copy.deepcopy(additions)
            preview = root / "preview"
            manifest = prepare(additions, preview, root=root, run_ids=["run-1"])
            self.assertEqual(manifest["added"], 1)
            self.assertEqual(additions, before)
            self.assertFalse(publish(preview, root=root)["applied"])
            result = publish(preview, root=root, apply=True)
            merged = json.loads((web / "channels.json").read_text(encoding="utf-8"))[
                "channels"
            ]
            self.assertEqual(merged[0], old)
            self.assertEqual(len(merged), 2)
            self.assertEqual(
                (Path(result["backup"]) / "channels.js").read_text(encoding="utf-8"),
                "INDEPENDENT USER JS",
            )

    def test_changed_js_or_json_prevents_apply(self):
        for name in ("channels.js", "channels.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                web = root / "web/data"
                web.mkdir(parents=True)
                (web / "channels.json").write_text(
                    json.dumps([dict(username="existing_channel", name="Owner")]),
                    encoding="utf-8",
                )
                (web / "channels.js").write_text("original", encoding="utf-8")
                preview = root / "preview"
                prepare([], preview, root=root)
                (web / name).write_text("USER CHANGE", encoding="utf-8")
                with patch("src.catalog_growth_merge.atomic_write_text") as writer:
                    with self.assertRaises(ValueError):
                        publish(preview, root=root, apply=True)
                    writer.assert_not_called()

    def test_oversize_merge_never_drops_old_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            web = root / "web/data"
            web.mkdir(parents=True)
            source = web / "channels.json"
            source.write_text(
                json.dumps([dict(username="existing_channel", name="Owner")]),
                encoding="utf-8",
            )
            before = source.read_bytes()
            with self.assertRaises(ValueError):
                prepare(
                    [dict(username="new_channel", name="New")],
                    root / "preview",
                    root=root,
                    maximum=1,
                )
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((root / "preview").exists())


class Projection6000Tests(unittest.TestCase):
    def test_block_neighbours_match_dense_reference_and_do_not_retain_square_arrays(
        self,
    ):
        import numpy as np
        from src.atlas_projection import nearest, nearest_points

        points = np.random.default_rng(42).normal(size=(230, 2))
        distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
        expected = nearest(distances, 15)
        actual = nearest_points(points, 15, block_size=17)
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(expected.flags.owndata)
        self.assertEqual(expected.nbytes, len(points) * 15 * 4)

    def test_6000_snapshot_is_accepted(self):
        from src.atlas_projection import snapshot_rows

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "channels.json"
            path.write_text(
                json.dumps([dict(username="channel_" + str(i)) for i in range(6000)]),
                encoding="utf-8",
            )
            self.assertEqual(len(snapshot_rows(path)), 6000)
