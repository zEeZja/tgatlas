import unittest
from unittest.mock import patch

from src.tgstat_search import (
    TGStatAPIError,
    _extract_channels,
    _fetch,
    _response_items,
    run,
)
from src import config


class TGStatResponseTests(unittest.TestCase):
    def test_extracts_channel_and_metadata(self):
        payload = {
            "status": "ok",
            "response": {
                "count": 2,
                "items": [
                    {
                        "id": 53248,
                        "tg_id": 1095068011,
                        "link": "t.me/tg_analytics",
                        "peer_type": "channel",
                        "username": "@tg_analytics",
                        "title": "Telegram Analytics",
                        "about": "Новости и аналитика",
                        "participants_count": 21905,
                        "ci_index": 2704.48,
                        "country": "Россия",
                    },
                    {
                        "id": 123,
                        "tg_id": 123456,
                        "username": "@a_valid_chat",
                        "peer_type": "chat",
                        "title": "Не канал",
                    },
                ],
            },
        }
        items = _extract_channels(payload, query="аналитика", category=None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["username"], "tg_analytics")
        self.assertEqual(items[0]["telegram_id"], 1095068011)
        self.assertEqual(items[0]["subscribers"], 21905)
        self.assertEqual(items[0]["name"], "Telegram Analytics")
        self.assertEqual(items[0]["catalog_query"], "аналитика")

    def test_rejects_bad_username_and_non_positive_telegram_id(self):
        payload = {
            "status": "ok",
            "response": {"items": [
                {"peer_type": "channel", "username": "@valid_channel", "tg_id": 0},
                {"peer_type": "channel", "link": "https://example.com/channel", "tg_id": 42},
            ]},
        }
        items = _extract_channels(payload, query="news", category=None)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["telegram_id"])

    def test_raises_clear_error_for_api_error_envelope(self):
        with self.assertRaisesRegex(TGStatAPIError, "bad token"):
            _response_items({"status": "error", "error": "bad token"})

    def test_requires_response_items_list(self):
        with self.assertRaisesRegex(TGStatAPIError, "response.items"):
            _response_items({"status": "ok", "response": {"count": 0}})

    def test_fetch_sends_documented_search_parameters(self):
        from types import SimpleNamespace

        fake_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "response": {"items": []}},
        )
        with patch("src.tgstat_search.requests.get", return_value=fake_response) as get:
            payload = _fetch(
                token="test-token", query="новости Казани", category=None,
                country="ru", language="russian", limit=25,
                search_by_description=True,
            )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(get.call_args.args[0], "https://api.tgstat.ru/channels/search")
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["q"], "новости Казани")
        self.assertEqual(params["country"], "ru")
        self.assertEqual(params["language"], "russian")
        self.assertEqual(params["peer_type"], "channel")
        self.assertEqual(params["search_by_description"], 1)

    def test_run_requires_tgstat_token_before_connecting_to_database(self):
        old_token = config.TGSTAT_API_TOKEN
        try:
            config.TGSTAT_API_TOKEN = ""
            with self.assertRaisesRegex(TGStatAPIError, "TGSTAT_API_TOKEN"):
                run(queries=["новости Казани"])
        finally:
            config.TGSTAT_API_TOKEN = old_token


if __name__ == "__main__":
    unittest.main()
