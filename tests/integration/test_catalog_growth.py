import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import Mock, patch

from src import catalog_growth as growth, db
from src.models import Channel
from src.public_catalogues import SourceError
from src.scraper import ScrapeError
from tests.integration.support import PostgresCase


META = dict(
    name="Банковский сектор",
    description="Банки, банковские услуги и банковская система",
    subscribers=5000,
)
SOURCE = "https://tlgrm.ru/channels/finance"


def cards_html(*usernames):
    return "".join(
        f'<div class="channel-card"><a class="channel-card__username" href="/channels/@{u}">@{u}</a><span class="channel-card__subscribers">5000</span></div>'
        for u in usernames
    )


def profile_html(username, *, profile=True):
    return f'<meta property="og:url" content="https://t.me/{username}"><div class="tgme_page_title">Банковский сектор</div><div class="tgme_page_description">Банки, банковские услуги и банковская система</div><div class="tgme_page_extra">5 000 subscribers</div>'


class GrowthSQLTests(PostgresCase):
    def setUp(self):
        super().setUp()
        db.init_schema(self.conn)
        self.card = dict(username="new_bank_channel", source="tlgrm", source_url=SOURCE)
        self.result = dict(username="new_bank_channel", meta={**META})
        self.prediction = dict(subcategory="banking")

    def store(self, outcome="eligible"):
        return growth.store_verified_candidate(
            self.conn,
            self.result,
            self.card,
            outcome,
            self.prediction,
            "test-run",
            "priority",
        )

    def test_fresh_insert_only_import_with_provenance(self):
        with db.transaction(self.conn):
            self.assertTrue(self.store())
            self.assertTrue(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )
            self.assertFalse(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )
        self.assertEqual(
            self.query(
                "SELECT name,subscribers,source,last_meta_at IS NOT NULL,category,subcategory FROM channels"
            ),
            [(META["name"], 5000, "catalog_growth", True, None, None)],
        )
        self.assertEqual(
            self.query("SELECT status FROM channel_candidates"), [("imported",)]
        )
        self.assertEqual(
            self.query("SELECT source_name,source_url FROM channel_sources"),
            [("tlgrm", SOURCE)],
        )

    def test_existing_channel_is_not_changed(self):
        with db.transaction(self.conn):
            db.upsert_channel(
                self.conn,
                Channel(
                    "new_bank_channel",
                    name="Owner name",
                    subcategory="industry",
                    subscribers=9999,
                ),
            )
        before = self.snapshot()
        with db.transaction(self.conn):
            self.assertFalse(self.store())
            self.assertFalse(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )
        self.assertEqual(self.snapshot(), before)

    def test_historical_import_link_is_never_followed_or_overwritten(self):
        with db.transaction(self.conn):
            target = db.upsert_channel(
                self.conn,
                Channel("renamed_bank_channel", name="Owner name", subscribers=9999),
            )
            self.store()
            self.query(
                "UPDATE channel_candidates SET status='imported',imported_channel_id=%s",
                (target,),
            )
        before = self.snapshot()
        with db.transaction(self.conn):
            self.assertFalse(self.store())
            self.assertFalse(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )
        self.assertEqual(self.snapshot(), before)

    def test_stale_or_deferred_candidates_cannot_be_imported(self):
        with db.transaction(self.conn):
            self.store()
            self.query(
                "UPDATE channel_candidates SET validated_at=NOW()-INTERVAL '2 hours'"
            )
            self.assertFalse(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )
            self.store("deferred_topic")
            self.assertEqual(
                self.query("SELECT status FROM channel_candidates"), [("pending",)]
            )
            self.assertFalse(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )
            self.result["meta"]["subscribers"] = 999
            self.store("below_minimum")
            self.assertEqual(
                self.query("SELECT status FROM channel_candidates"), [("rejected",)]
            )
            self.assertFalse(
                growth.import_verified_new(self.conn, self.result["username"], 1000)
            )


class GrowthResumeTests(PostgresCase):
    def setUp(self):
        super().setUp()
        db.init_schema(self.conn)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_path = Path(self.tmp.name) / "plan.json"
        self.state_path = Path(self.tmp.name) / "state.json"
        self.plan = json.loads(growth.PLAN.read_text(encoding="utf-8"))
        self.plan.update(
            official_pages=[],
            queries=[],
            catalogues=[dict(source="tlgrm", url=SOURCE, pages=1, phase="priority")],
        )
        self.client = Mock(requests=0, max_requests=100)
        self.client.get.return_value = cards_html(
            "first_bank_channel", "second_bank_channel"
        )
        self.write_plan()

    def write_plan(self):
        self.plan_path.write_text(
            json.dumps(self.plan, ensure_ascii=False), encoding="utf-8"
        )

    def run_growth(self, *, target=10, workers=1, **kwargs):
        with (
            patch.object(growth.db, "connect", side_effect=self.connect),
            patch.object(growth, "PublicHTMLClient", return_value=self.client),
            patch.object(growth.exporter, "export_channels", return_value=0),
            patch.object(growth, "fetch_channel_html", side_effect=profile_html),
        ):
            return growth.run(
                apply=True,
                target=target,
                workers=workers,
                plan_path=self.plan_path,
                state_path=self.state_path,
                **kwargs,
            )

    def read_state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_completed_job_is_not_probed_for_nonexistent_pages_on_resume(self):
        self.run_growth()
        self.assertEqual(self.client.get.call_count, 1)
        self.assertIn(SOURCE, self.read_state()["completed_jobs"])
        result = self.run_growth()
        self.assertEqual(self.client.get.call_count, 1)
        self.assertEqual(result["total_imported"], 2)
        self.assertEqual(result["this_run"], {})

    def test_budget_resume_does_not_lose_unprocessed_tail(self):
        first = self.run_growth(max_profiles=1)
        self.assertEqual(first["status"], "budget_exhausted")
        self.assertEqual(self.query("SELECT count(*) FROM channels"), [(1,)])
        self.assertEqual(self.read_state()["completed_pages"], {})
        second = self.run_growth()
        self.assertEqual(second["total_imported"], 2)
        self.assertEqual(self.query("SELECT count(*) FROM channels"), [(2,)])

    def test_inflight_profiles_cannot_exceed_exact_target_and_can_resume(self):
        self.client.get.return_value = cards_html(
            "first_bank_channel",
            "second_bank_channel",
            "third_bank_channel",
            "fourth_bank_channel",
        )
        barrier = threading.Barrier(4)

        def concurrent_profile(username, *, profile=True):
            barrier.wait(timeout=10)
            return profile_html(username)

        with (
            patch.object(growth.db, "connect", side_effect=self.connect),
            patch.object(growth, "PublicHTMLClient", return_value=self.client),
            patch.object(growth.exporter, "export_channels", return_value=0),
            patch.object(
                growth, "fetch_channel_html", side_effect=concurrent_profile
            ) as fetch,
        ):
            first = growth.run(
                apply=True,
                target=1,
                workers=4,
                plan_path=self.plan_path,
                state_path=self.state_path,
            )
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(first["status"], "target_reached")
        self.assertEqual(self.query("SELECT count(*) FROM channels"), [(1,)])
        self.assertEqual(
            self.query(
                "SELECT status,count(*) FROM channel_candidates GROUP BY status ORDER BY status"
            ),
            [("imported", 1), ("validated", 3)],
        )
        old_row = self.query("SELECT * FROM channels")[0]
        resumed = self.run_growth(target=4, workers=4)
        self.assertEqual(resumed["current_count"], 4)
        self.assertEqual(resumed["total_imported"], 4)
        self.assertEqual(
            self.query("SELECT * FROM channels WHERE id=%s", (old_row[0],)), [old_row]
        )

    def test_restarting_at_reached_target_does_not_fetch_profiles(self):
        self.run_growth(target=1)
        calls = self.client.get.call_count
        result = self.run_growth(target=1)
        self.assertEqual(result["status"], "target_reached")
        self.assertEqual(result["current_count"], 1)
        self.assertEqual(result["this_run"], {})
        self.assertEqual(self.client.get.call_count, calls)

    def test_plan_mismatch_does_not_overwrite_checkpoint(self):
        self.run_growth()
        original = self.state_path.read_bytes()
        self.plan["query_pages"] += 1
        self.write_plan()
        with self.assertRaisesRegex(ValueError, "новый --state"):
            self.run_growth()
        self.assertEqual(self.state_path.read_bytes(), original)

    def test_temporary_failure_remains_retry_even_when_seen_in_another_job(self):
        self.plan["catalogues"].append(
            dict(
                source="tlgrm",
                url="https://tlgrm.ru/channels/business",
                pages=1,
                phase="priority",
            )
        )
        self.write_plan()
        self.client.get.return_value = cards_html("first_bank_channel")
        with (
            patch.object(growth.db, "connect", side_effect=self.connect),
            patch.object(growth, "PublicHTMLClient", return_value=self.client),
            patch.object(growth.exporter, "export_channels", return_value=0),
            patch.object(
                growth, "fetch_channel_html", side_effect=ScrapeError("403")
            ) as fetch,
        ):
            first = growth.run(
                apply=True,
                target=10,
                workers=1,
                plan_path=self.plan_path,
                state_path=self.state_path,
            )
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["status"], "paused_for_retry")
        state = self.read_state()
        self.assertEqual(len(state["retry_cards"]), 1)
        # A search-page URL with an existing query must not strand the retry.
        state["retry_cards"]["first_bank_channel"]["source_url"] = (
            "https://tgramsearch.com/search?query=bank&page=2"
        )
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        second = self.run_growth()
        self.assertEqual(second["total_imported"], 1)
        self.assertEqual(self.read_state()["retry_cards"], {})

    def test_other_phase_is_not_started_with_priority_source_error(self):
        self.plan["catalogues"].append(
            dict(
                source="tlgrm",
                url="https://tlgrm.ru/channels/books",
                pages=1,
                phase="other",
            )
        )
        self.write_plan()
        self.client.get.side_effect = SourceError("offline")
        result = self.run_growth(allow_other=True)
        self.assertEqual(result["status"], "paused_for_retry")
        self.assertEqual(self.client.get.call_count, 1)

    def test_deferred_priority_discovery_can_be_reverified_in_other_phase(self):
        self.plan["catalogues"].append(
            dict(
                source="tlgrm",
                url="https://tlgrm.ru/channels/books",
                pages=1,
                phase="other",
            )
        )
        self.write_plan()
        self.client.get.return_value = cards_html("books_channel")
        html = '<div class="tgme_page_title">Книги и литература</div><div class="tgme_page_description">Книги, писатели и литературные рецензии</div><div class="tgme_page_extra">5 000 subscribers</div>'
        with (
            patch.object(growth.db, "connect", side_effect=self.connect),
            patch.object(growth, "PublicHTMLClient", return_value=self.client),
            patch.object(growth.exporter, "export_channels", return_value=0),
            patch.object(growth, "fetch_channel_html", return_value=html) as fetch,
        ):
            result = growth.run(
                apply=True,
                target=10,
                workers=1,
                plan_path=self.plan_path,
                state_path=self.state_path,
                allow_other=True,
            )
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["total_imported"], 1)
        self.assertEqual(
            self.read_state()["processed"]["books_channel"]["phase"], "other"
        )
