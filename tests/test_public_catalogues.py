import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.public_catalogues import (
    PublicHTMLClient,
    SourceBlocked,
    SourceBudgetExhausted,
    SourceError,
    page_url,
    parse_official_links,
    parse_tgramsearch_detail,
    parse_tgramsearch_listing,
    parse_tlgrm_listing,
    public_username,
)


class PublicCatalogueParsersTests(unittest.TestCase):
    def test_only_explicit_public_telegram_links(self):
        for href in (
            "contacts",
            "@username",
            "username",
            "mailto:username",
            "https://example.com/name",
            "https://t.me/+invite",
            "https://t.me/channel_name/123",
        ):
            self.assertEqual(public_username(href), "", href)
        for href in (
            "https://t.me/Channel_Name",
            "https://telegram.me/Channel_Name",
            "tg://resolve?domain=Channel_Name",
            "//t.me/Channel_Name",
        ):
            self.assertEqual(public_username(href), "channel_name", href)

    def test_tgramsearch_filters_and_keeps_pagination_query(self):
        def card(number, count, category="ekonomika"):
            return f"""<div class="tg-channel"><div class="tg-channel__link"><a href="/join/{number}">Банк</a></div>
                <span class="tg-stat__user-count">{count}</span><div class="tg-channel__categories"><a href="/categories/{category}">x</a></div></div>"""

        html = card(1, 2000) + card(2, 999) + card(3, 5000, "chats")
        html += '<a href="/join/999">Реклама вне карточки</a><a href="?query=банк&page=5">5</a><a href="?query=кот&page=99">99</a><a href="https://example.com/search?query=банк&page=100">100</a>'
        result = parse_tgramsearch_listing(
            html, "https://tgramsearch.com/search?query=банк"
        )
        self.assertEqual(result.last_page, 5)
        self.assertEqual(
            [r["detail_url"] for r in result.cards], ["https://tgramsearch.com/join/1"]
        )
        self.assertNotIn("category", result.cards[0])

    def test_detail_ignores_contacts_related_channels_and_invites(self):
        html = """<a href="https://t.me/advertiser">Ad</a><h1 class="tg-channel__header"><a href="tg://resolve?domain=main_channel">Main</a></h1><p>@contact_user</p><a href="tg://resolve?domain=related_channel">Related</a>"""
        self.assertEqual(
            parse_tgramsearch_detail(html, "https://tgramsearch.com/join/1")[
                "username"
            ],
            "main_channel",
        )
        self.assertIsNone(
            parse_tgramsearch_detail(
                '<h1 class="tg-channel__header"><a href="tg://join?invite=hash">Private</a></h1>',
                "https://tgramsearch.com/join/2",
            )
        )

    def test_unknown_markup_is_not_a_successful_empty_result(self):
        with self.assertRaises(SourceError):
            parse_tgramsearch_listing(
                "<h1>Service unavailable</h1>", "https://tgramsearch.com/search?query=x"
            )
        with self.assertRaises(SourceError):
            parse_tgramsearch_detail(
                "<h1>Service unavailable</h1>", "https://tgramsearch.com/join/1"
            )
        with self.assertRaises(SourceError):
            parse_tlgrm_listing("<h1>Error</h1>", "https://tlgrm.ru/channels/finance")
        self.assertEqual(
            parse_tgramsearch_listing(
                "<h1>Ничего не найдено</h1>", "https://tgramsearch.com/search?query=x"
            ).cards,
            [],
        )

    def test_tlgrm_ignores_promotions_external_links_and_small_channels(self):
        def card(username, count, href=None, promoted=False):
            return f'''<div class="channel-card {"channel-card--promoted" if promoted else ""}"><a class="channel-card__username" href="{href or "/channels/@" + username}">@{username}</a><span class="channel-card__subscribers">{count}</span></div>'''

        html = (
            card("valid_channel", "1,234")
            + card("small_channel", 20)
            + card("ad_channel", 9000, promoted=True)
            + card("wrong_channel", 9999, "https://example.com/channels/@wrong_channel")
        )
        rows = parse_tlgrm_listing(html, "https://tlgrm.ru/channels/finance").cards
        self.assertEqual([r["username"] for r in rows], ["valid_channel"])
        self.assertEqual(rows[0]["catalog_subscribers"], 1234)

    def test_official_links_do_not_treat_navigation_as_usernames(self):
        html = '<a href="contacts">Contacts</a><a href="https://t.me/bank_channel">Telegram</a><a href="https://t.me/bank_channel">Again</a><a href="https://t.me/support_bot">Bot</a>'
        rows = parse_official_links(html, "https://example.gov/").cards
        self.assertEqual([r["username"] for r in rows], ["bank_channel"])

    def test_page_url_replaces_page_without_dropping_search(self):
        self.assertEqual(
            page_url("https://tgramsearch.com/search?query=bank&page=8", 2),
            "https://tgramsearch.com/search?query=bank&page=2",
        )
        self.assertEqual(
            page_url("https://tgramsearch.com/search?query=bank&page=8", 1),
            "https://tgramsearch.com/search?query=bank",
        )


class PublicHTMLClientTests(unittest.TestCase):
    def test_robots_disallow_prevents_page_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PublicHTMLClient({"example.com"}, tmp)
            with patch.object(
                client,
                "_request",
                return_value=(200, {}, "User-agent: *\nDisallow: /private"),
            ) as request:
                with self.assertRaises(SourceBlocked):
                    client.get("https://example.com/private")
            self.assertEqual(request.call_count, 1)

    def test_cache_is_used_but_only_after_robots(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PublicHTMLClient({"example.com"}, tmp)
            with patch.object(
                client,
                "_request",
                side_effect=[
                    (200, {}, "User-agent: *\nAllow: /"),
                    (200, {}, "<html>OK</html>"),
                ],
            ) as request:
                self.assertEqual(client.get("https://example.com/"), "<html>OK</html>")
                self.assertEqual(client.get("https://example.com/"), "<html>OK</html>")
            self.assertEqual(request.call_count, 2)
            self.assertEqual(len(list(Path(tmp).glob("*.html"))), 1)

    def test_unapproved_redirect_cannot_be_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PublicHTMLClient({"example.com"}, tmp)
            with patch.object(
                client,
                "_request",
                side_effect=[
                    (200, {}, "User-agent: *\nAllow: /"),
                    (302, {"Location": "https://other.com/"}, ""),
                ],
            ) as request:
                with self.assertRaises(SourceBlocked):
                    client.get("https://example.com/")
            self.assertEqual(request.call_count, 2)

    def test_limits_and_private_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PublicHTMLClient(
                {"example.com", "localhost", "127.0.0.1"}, tmp, max_requests=0
            )
            with self.assertRaises(SourceBudgetExhausted):
                client.get("https://example.com/")
            for url in (
                "https://localhost/",
                "https://127.0.0.1/",
                "http://example.com/",
                "https://other.com/",
                "https://user:secret@example.com/",
            ):
                with self.assertRaises(SourceError):
                    client.get(url)

    def test_access_restriction_stops_host_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PublicHTMLClient({"example.com"}, tmp)
            response = Mock(status_code=403)
            client.local.session = Mock()
            client.local.session.get.return_value = response
            with self.assertRaises(SourceBlocked):
                client._request("https://example.com/")
            with self.assertRaises(SourceBlocked):
                client._request("https://example.com/second")
            client.local.session.get.assert_called_once()
            response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
