import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.identity import normalize_username
from src.tgden_search import _extract_channels, _is_demo_payload, _fetch_suggest, load_default_queries
from src.cloudseeding import _candidate_from_row, _load_records, _parse_count
from src.metrics import parse_count
from src.telegram_account import (
    _public_broadcast_candidate, _extract_recommendation_candidates,
    _verify_expected_account_id,
)
from src import config as project_config
from src.mtproto_scrape import message_to_post


class UsernameNormalizationTests(unittest.TestCase):
    def test_accepts_public_username_and_channel_urls(self):
        self.assertEqual(normalize_username("@BreakingMash"), "breakingmash")
        self.assertEqual(normalize_username("https://t.me/BreakingMash"), "breakingmash")
        self.assertEqual(normalize_username("t.me/s/BreakingMash"), "breakingmash")

    def test_rejects_post_links_invites_and_private_links(self):
        self.assertEqual(normalize_username("https://t.me/BreakingMash/123"), "")
        self.assertEqual(normalize_username("https://t.me/+inviteHash"), "")
        self.assertEqual(normalize_username("https://example.com/channel"), "")
        self.assertEqual(normalize_username("https://t.me/c/123456/1"), "")
        self.assertEqual(normalize_username("abc"), "")


class TgdenTests(unittest.TestCase):
    def test_extracts_only_public_channel_items(self):
        payload = {
            "items": [
                {"type": "channel", "username": "news_channel", "title": "News",
                 "telegram_id": 123, "category": "news", "subscribers": 12345},
                {"type": "chat", "username": "group_chat"},
                {"type": "channel", "username": "private_channel", "is_private": True},
                {"type": "channel", "href": "/en/channel/tech_daily", "title": "Tech"},
                {"type": "channel", "href": "/en/channel/bad/123", "title": "Not a username"},
            ]
        }
        rows = _extract_channels(payload, "технологии")
        self.assertEqual([row["username"] for row in rows], ["news_channel", "tech_daily"])
        self.assertEqual(rows[0]["telegram_id"], 123)
        self.assertEqual(rows[0]["category"], "news")
        self.assertIsNone(rows[0]["region_hint"])

    def test_detects_tgden_demo_fixture(self):
        payload = {"items": [
            {"id": "demo-000001", "telegram_id": 0, "username": "telegram"},
            {"id": "demo-000002", "telegram_id": 0, "username": "durov"},
        ]}
        self.assertTrue(_is_demo_payload(payload))
        self.assertFalse(_is_demo_payload({"items": [
            {"id": "channel-123", "telegram_id": 123, "username": "local_news"},
        ]}))

    def test_uses_legacy_suggest_endpoint_and_rejects_demo_fixture(self):
        from unittest.mock import Mock, patch
        demo = {"items": [{"id": "demo-000001", "telegram_id": 0,
                           "username": "telegram"}]}
        response = Mock()
        response.json.return_value = demo
        response.raise_for_status.return_value = None
        with patch("src.tgden_search.requests.get", return_value=response) as request:
            with self.assertRaisesRegex(ValueError, r"демонстрационные demo-\*"):
                _fetch_suggest("новости Казани")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "https://tgden.com/api/suggest")
        self.assertEqual(request.call_args.kwargs["params"], {"q": "новости Казани"})

    def test_supports_legacy_suggest_groups(self):
        payload = {"groups": [
            {"key": "channels", "items": [
                {"kind": "channel", "username": "local_news", "label": "Local News"},
                {"kind": "channel", "sub": "@city_daily", "label": "City Daily"},
                {"kind": "chat", "username": "not_a_channel", "label": "Group"},
            ]},
            {"key": "users", "items": [
                {"kind": "user", "username": "person_user", "label": "Person"},
            ]},
        ]}
        rows = _extract_channels(payload, "городские новости")
        self.assertEqual([row["username"] for row in rows], ["local_news", "city_daily"])
        self.assertEqual(rows[0]["name"], "Local News")

    def test_exact_city_query_is_preserved_as_region_hint(self):
        payload = {"groups": [{"key": "channels", "items": [
            {"kind": "channel", "username": "kazan_news", "label": "Казань Новости"}
        ]}]}
        rows = _extract_channels(payload, "Казань")
        self.assertEqual(rows[0]["region_hint"], "Казань")

    def test_accepts_list_inside_data_envelope_and_normalizes_counters(self):
        rows = _extract_channels({"data": [{
            "type": "channel", "username": "science_feed",
            "subscribers": "12,4 тыс.",
        }]}, "science")
        self.assertEqual(rows[0]["subscribers"], 12_400)

    def test_default_query_file_is_available_and_deduplicated(self):
        queries = load_default_queries()
        self.assertGreater(len(queries), 40)
        self.assertEqual(len(queries), len({q.casefold() for q in queries}))


class CloudSeedingTests(unittest.TestCase):
    def test_parses_cloudseeding_export_fields(self):
        row = _candidate_from_row({
            "Ссылка": "https://t.me/local_news",
            "Название": "Local News",
            "Подписчики": "12,5K",
            "Регион": "Казань",
            "Страна": "Россия",
            "Категория": "Новости",
            "ER": "8.2%",
        })
        self.assertEqual(row["username"], "https://t.me/local_news")
        self.assertEqual(row["subscribers"], 12500)
        self.assertEqual(row["region_hint"], "Казань")
        self.assertEqual(row["country"], "RU")
        self.assertEqual(row["category"], "Новости")

    def test_count_suffixes(self):
        self.assertEqual(_parse_count("1.2M"), 1_200_000)
        self.assertEqual(_parse_count("4,5К"), 4_500)
        self.assertEqual(_parse_count("12 345"), 12_345)
        self.assertEqual(_parse_count("12,5 тыс. подписчиков"), 12_500)
        self.assertEqual(_parse_count("1,2 млн"), 1_200_000)
        self.assertEqual(_parse_count("2 млрд"), 2_000_000_000)
        self.assertEqual(_parse_count("12,345"), 12_345)
        self.assertEqual(_parse_count("1.234,5"), 1_234)

    def test_shared_metric_parser_handles_invalid_values(self):
        self.assertIsNone(parse_count("неизвестно"))
        self.assertIsNone(parse_count(None))

    def test_loads_nested_json_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "channels.json"
            path.write_text('{"data":{"channels":[{"username":"hello_world"}]}}', encoding="utf-8")
            rows = _load_records(path)
        self.assertEqual(rows[0]["username"], "hello_world")


class TelegramAccountIdentityTests(unittest.TestCase):
    def setUp(self):
        self.original_expected = project_config.TG_EXPECTED_USER_ID

    def tearDown(self):
        project_config.TG_EXPECTED_USER_ID = self.original_expected

    def test_requires_explicit_reserve_account_id(self):
        project_config.TG_EXPECTED_USER_ID = 0
        with self.assertRaisesRegex(RuntimeError, "TG_EXPECTED_USER_ID"):
            _verify_expected_account_id(12345)

    def test_accepts_only_pinned_account_id(self):
        project_config.TG_EXPECTED_USER_ID = 12345
        _verify_expected_account_id(12345)
        with self.assertRaisesRegex(RuntimeError, "операция остановлена"):
            _verify_expected_account_id(54321)


class TelegramAccountCandidateTests(unittest.TestCase):
    def test_only_public_broadcast_channels_are_included(self):
        public = SimpleNamespace(
            broadcast=True, megagroup=False, username="public_news",
            id=987, title="Public News", participants_count=5000,
        )
        private = SimpleNamespace(
            broadcast=True, megagroup=False, username=None,
            id=988, title="Private News", participants_count=100,
        )
        group = SimpleNamespace(
            broadcast=False, megagroup=True, username="a_group",
            id=989, title="A group", participants_count=200,
        )
        self.assertEqual(_public_broadcast_candidate(public)["telegram_id"], 987)
        self.assertIsNone(_public_broadcast_candidate(private))
        self.assertIsNone(_public_broadcast_candidate(group))

    def test_mtproto_message_maps_to_shared_post_model(self):
        from datetime import datetime, timezone
        message = SimpleNamespace(
            id=456, message="Заголовок новости\nТекст публикации", media=None,
            date=datetime(2026, 11, 3, tzinfo=timezone.utc), views=789,
        )
        post = message_to_post(message, "news_channel", 12)
        self.assertEqual(post.tg_message_id, 456)
        self.assertEqual(post.url, "https://t.me/news_channel/456")
        self.assertEqual(post.title, "Заголовок новости")
        self.assertEqual(post.views, 789)
        self.assertEqual(post.channel_id, 12)

    def test_empty_service_message_is_skipped_but_media_message_is_kept(self):
        service = SimpleNamespace(id=1, message=None, raw_text=None, media=None)
        media = SimpleNamespace(id=2, message=None, raw_text=None, media=object())
        self.assertIsNone(message_to_post(service, "news_channel", 12))
        self.assertEqual(message_to_post(media, "news_channel", 12).tg_message_id, 2)

    def test_recommendation_result_filters_groups(self):
        result = SimpleNamespace(chats=[
            SimpleNamespace(broadcast=True, megagroup=False, username="channel_one",
                            id=1, title="One", participants_count=10),
            SimpleNamespace(broadcast=False, megagroup=True, username="group_one",
                            id=2, title="Group", participants_count=10),
        ])
        rows = _extract_recommendation_candidates(result)
        self.assertEqual([row["username"] for row in rows], ["channel_one"])


if __name__ == "__main__":
    unittest.main()
