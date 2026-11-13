from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from unittest.mock import Mock, patch

from src.catalog_growth import (
    PLAN,
    ProfileClaims,
    _verify,
    build_jobs,
    decision,
    revisit,
)
from src.scraper import ScrapeError
from src.topic_keywords import load_classification_rules


class GrowthPolicyTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(PLAN.read_text(encoding="utf-8"))
        self.rules = load_classification_rules()

    def test_priority_is_always_before_other_jobs(self):
        jobs = build_jobs(self.plan, allow_other=True)
        phases = [j["phase"] for j in jobs]
        self.assertEqual(phases, sorted(phases, key=lambda p: p != "priority"))
        self.assertNotIn("other", [j["phase"] for j in build_jobs(self.plan)])
        self.assertEqual(len(jobs), len({j["url"] for j in jobs}))

    def test_unknown_audience_and_below_floor_never_qualify(self):
        for count, expected in [(None, "unverified"), (999, "below_minimum")]:
            outcome, _ = decision(
                dict(name="Банк", description="Кредиты и депозиты", subscribers=count),
                self.plan,
                "priority",
                self.rules,
            )
            self.assertEqual(outcome, expected)

    def test_source_labels_cannot_make_an_unknown_profile_a_priority(self):
        outcome, _ = decision(
            dict(name="Заметки", description="Личный дневник", subscribers=5000),
            self.plan,
            "priority",
            self.rules,
        )
        self.assertNotEqual(outcome, "eligible")

    def test_relevant_financial_profile_qualifies(self):
        outcome, prediction = decision(
            dict(
                username="new_banking_channel",
                name="Банковский сектор",
                description="Новости банковского сектора: банки, банковские услуги, банковская система",
                subscribers=5000,
            ),
            self.plan,
            "priority",
            self.rules,
        )
        self.assertEqual(outcome, "eligible")
        self.assertEqual(prediction["subcategory"], "banking")

    def test_other_topics_are_deferred_only_during_priority_phase(self):
        meta = dict(
            username="new_books_channel",
            name="Книги и литература",
            description="Книги, писатели и литературные рецензии",
            subscribers=5000,
        )
        first, _ = decision(meta, self.plan, "priority", self.rules)
        second, _ = decision(meta, self.plan, "other", self.rules)
        self.assertEqual(first, "deferred_topic")
        self.assertEqual(second, "eligible")
        self.assertTrue(revisit(dict(outcome=first, phase="priority"), "other"))
        self.assertFalse(
            revisit(dict(outcome="deferred_review", phase="priority"), "other")
        )

    def test_risky_sales_are_not_imported(self):
        outcome, _ = decision(
            dict(
                name="Продажа банковских карт",
                description="Банковские карты",
                subscribers=9000,
            ),
            self.plan,
            "priority",
            self.rules,
        )
        self.assertEqual(outcome, "deferred_risk")


class ProfileDeduplicationTests(unittest.TestCase):
    def test_concurrent_claims_only_fetch_one_profile(self):
        claims = ProfileClaims(set(), {}, 100)
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(
                pool.map(lambda _: claims.claim("bank_channel", "priority"), range(30))
            )
        self.assertEqual(outcomes.count(None), 1)
        self.assertEqual(claims.used, 1)

    def test_profile_budget_and_existing_rows(self):
        claims = ProfileClaims({"existing_channel"}, {}, 1)
        self.assertEqual(claims.claim("existing_channel", "priority"), "existing")
        self.assertIsNone(claims.claim("first_channel", "priority"))
        self.assertEqual(claims.claim("second_channel", "priority"), "budget_deferred")

    def test_deferred_topic_can_be_verified_in_other_phase(self):
        claims = ProfileClaims(
            set(),
            {"books_channel": dict(outcome="deferred_topic", phase="priority")},
            10,
        )
        self.assertEqual(claims.claim("books_channel", "priority"), "duplicate")
        self.assertIsNone(claims.claim("books_channel", "other"))

    def test_eligible_tail_after_reaching_target_can_resume(self):
        claims = ProfileClaims(
            set(), {"bank_channel": dict(outcome="eligible", phase="priority")}, 10
        )
        self.assertIsNone(claims.claim("bank_channel", "priority"))

    def test_permanent_telegram_failure_is_not_retried(self):
        card = dict(
            username="gone_channel",
            source="tlgrm",
            source_url="https://tlgrm.ru/channels/finance",
        )
        with patch(
            "src.catalog_growth.fetch_channel_html",
            side_effect=ScrapeError("404", permanent=True),
        ):
            result = _verify(card, Mock(), ProfileClaims(set(), {}, 10), "priority")
        self.assertEqual(result["outcome"], "unavailable")

    def test_temporary_telegram_failure_is_retried(self):
        card = dict(
            username="slow_channel",
            source="tlgrm",
            source_url="https://tlgrm.ru/channels/finance",
        )
        with patch(
            "src.catalog_growth.fetch_channel_html",
            side_effect=ScrapeError("403", permanent=False),
        ):
            result = _verify(card, Mock(), ProfileClaims(set(), {}, 10), "priority")
        self.assertEqual(result["outcome"], "retry")

    def test_contact_and_group_pages_do_not_pass_channel_verification(self):
        card = dict(
            username="group_channel",
            source="tlgrm",
            source_url="https://tlgrm.ru/channels/finance",
        )
        with patch(
            "src.catalog_growth.fetch_channel_html",
            return_value='<div class="tgme_page_title">Group</div><div class="tgme_page_extra">50,000 members</div>',
        ):
            result = _verify(card, Mock(), ProfileClaims(set(), {}, 10), "priority")
        self.assertEqual(result["outcome"], "not_public_channel")


if __name__ == "__main__":
    unittest.main()
