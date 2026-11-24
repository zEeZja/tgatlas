"""RSSHub adapter and shared collection ledger; no external network or working DB."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape

from src.catalog_collection import (
    RSSHubFetcher,
    collect,
    suitable_posts,
    resume_rsshub_run,
    change_rsshub_url,
)
from src.rsshub_posts import MAX_FEED_BYTES, feed_url, normalize_base_url, parse_feed
from src.taxonomy_pilot import PilotStopped


def rss(username="demo", *, date=None, count=4):
    date = date or datetime.now(timezone.utc).isoformat()
    items = []
    for index in range(count):
        items.append(f"""<item><link>https://t.me/{username}/{index + 1}</link>
          <pubDate>{date}</pubDate><title>Заголовок не должен дублироваться</title>
          <description><![CDATA[<p>Содержательный текст новости номер {index}: подробности события и комментарии участников.</p><img src='https://untrusted.invalid/image.jpg'/>]]></description></item>""")
    return (
        "<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>"
    ).encode()


def response(payload=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.iter_content.return_value = [rss() if payload is None else payload]
    return result


class RSSParserTests(unittest.TestCase):
    def test_rss_is_compatible_with_existing_selection(self):
        sample = parse_feed(rss(), "demo")
        self.assertEqual(sample["purpose"], "classification_only")
        self.assertEqual(len(suitable_posts(sample)), 4)
        self.assertEqual(sample["posts"][0]["message_id"], 4)
        self.assertEqual(sample["posts"][0]["url"], "https://t.me/demo/4")
        self.assertNotIn("дублироваться", sample["posts"][0]["text"])
        self.assertNotIn("untrusted", sample["posts"][0]["text"])

    def test_content_encoded_precedes_excerpt_and_rfc822_date_is_supported(self):
        text = "Полное содержание новости и важные подробности события, а не короткий заголовок."
        payload = f"""<rss xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel><item>
        <link>https://t.me/demo/9?single</link><pubDate>Mon, 23 Nov 2026 10:00:00 +0300</pubDate>
        <description>Отрывок</description><content:encoded>{escape("<p>" + text + "</p>")}</content:encoded>
        </item></channel></rss>""".encode()
        post = parse_feed(payload, "demo")["posts"][0]
        self.assertEqual(post["text"], text)
        self.assertEqual(post["date"], "2026-11-23T07:00:00+00:00")

    def test_atom_and_xhtml_content(self):
        payload = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry>
          <link rel='alternate' href='https://t.me/s/demo/45'/><published>2026-11-23T10:00:00Z</published>
          <content type='xhtml'><div xmlns='http://www.w3.org/1999/xhtml'><p>Actual post text with sufficient detail for a classification sample.</p></div></content>
          </entry></feed>"""
        post = parse_feed(payload, "demo")["posts"][0]
        self.assertEqual(post["message_id"], 45)
        self.assertIn("Actual post text", post["text"])
        self.assertEqual(
            parse_feed(payload.replace(b"published", b"updated"), "demo")["posts"], []
        )
        plain = payload.replace(b"type='xhtml'", b"type='text'")
        plain = plain.replace(
            b"<div xmlns='http://www.w3.org/1999/xhtml'><p>Actual post text with sufficient detail for a classification sample.</p></div>",
            b"Literal &lt;angle brackets&gt; must remain in plain text content.",
        )
        self.assertIn("<angle brackets>", parse_feed(plain, "demo")["posts"][0]["text"])

    def test_wrong_channels_private_links_invalid_dates_and_duplicate_ids(self):
        now = datetime.now(timezone.utc).isoformat()
        items = []
        for link, date in [
            ("https://t.me/other/1", now),
            ("https://t.me/+private", now),
            ("https://t.me/demo/2", "invalid"),
            ("https://t.me/demo/3", "2026-11-23T10:00:00"),
            ("https://t.me/demo/4", now),
            ("https://t.me/demo/4", now),
            ("https://t.me@evil.invalid/demo/5", now),
        ]:
            items.append(
                f"<item><link>{escape(link)}</link><pubDate>{date}</pubDate><description>Post content</description></item>"
            )
        posts = parse_feed(
            ("<rss><channel>" + "".join(items) + "</channel></rss>").encode(), "demo"
        )["posts"]
        self.assertEqual([p["message_id"] for p in posts], [4])

    def test_no_invented_freshness_ads_and_text_deduplication(self):
        stale = parse_feed(
            rss(date=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat()),
            "demo",
        )
        self.assertEqual(suitable_posts(stale), [])
        sample = parse_feed(rss(), "demo")
        sample["posts"][1]["text"] = sample["posts"][0]["text"]
        sample["posts"][2]["text"] = "#реклама " + "Оплаченная публикация " * 5
        self.assertEqual(len(suitable_posts(sample)), 2)

    def test_script_and_quotes_removed_and_text_bounded(self):
        payload = rss(count=1).replace(
            b"<img ",
            b"<script>alert(1)</script><div class='rsshub-quote'>QUOTED MATERIAL</div><img ",
        )
        text = parse_feed(payload, "demo")["posts"][0]["text"]
        self.assertNotIn("alert", text)
        self.assertNotIn("QUOTED", text)
        post = parse_feed(
            rss(count=1).replace(b"</p>", ("x" * 5000 + "</p>").encode()), "demo"
        )["posts"][0]
        self.assertTrue(post["truncated"])
        self.assertEqual(len(post["text"]), 4000)

    def test_malformed_html_dtd_and_oversized_feeds_rejected(self):
        for payload in [
            b"<html>Error</html>",
            b"<rss>",
            b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss><channel>&x;</channel></rss>',
            b"x" * (MAX_FEED_BYTES + 1),
        ]:
            with self.subTest(payload=payload[:40]), self.assertRaises(ValueError):
                parse_feed(payload, "demo")

    def test_base_url_and_route_are_explicit_and_cannot_inject_requests(self):
        self.assertEqual(
            normalize_base_url("http://127.0.0.1:1200/"), "http://127.0.0.1:1200"
        )
        self.assertTrue(
            feed_url("https://rss.example.org/prefix", "demo").startswith(
                "https://rss.example.org/prefix/telegram/channel/demo/"
            )
        )
        self.assertIn("includeTelegraph=0", feed_url("http://localhost:1200", "demo"))
        for base in [
            "http://remote.example",
            "file:///tmp/feed",
            "https://user:password@host",
            "https://host?key=secret",
            "https://host/#x",
            "https://host/../secret",
        ]:
            with self.subTest(base=base), self.assertRaises(ValueError):
                normalize_base_url(base)
        with self.assertRaises(ValueError):
            feed_url("http://localhost:1200", "demo/../../private")


class RSSHubFetcherTests(unittest.TestCase):
    def setUp(self):
        sleep = patch("src.catalog_collection.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.state = dict(
            deadline=datetime.now(timezone.utc).timestamp() + 600,
            requests=0,
            request_budget=1,
            requests_by_channel={},
        )
        self.session = Mock()
        self.fetcher = RSSHubFetcher(
            self.state, Mock(), self.session, "http://localhost:1200"
        )

    def test_budget_reserved_before_request_no_redirects_or_media(self):
        result = response()

        def get(url, **kw):
            self.assertEqual(self.state["requests"], 1)
            self.assertFalse(kw["allow_redirects"])
            self.assertTrue(kw["stream"])
            return result

        self.session.get.side_effect = get
        self.assertEqual(len(self.fetcher("demo")["posts"]), 4)
        result.close.assert_called_once()
        with self.assertRaises(ValueError):
            self.fetcher("demo")
        with self.assertRaises(PilotStopped):
            self.fetcher("other")
        self.assertEqual(self.session.get.call_count, 1)

    def test_restrictions_stop_and_preserve_retry_after(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                self.state["requests"] = 0
                self.state["requests_by_channel"] = {}
                self.session.get.return_value = response(
                    status=status, headers={"Retry-After": "120"}
                )
                with self.assertRaises(PilotStopped):
                    self.fetcher("demo")
                self.assertGreater(
                    self.state["retry_not_before"],
                    datetime.now(timezone.utc).timestamp() + 110,
                )

    def test_upstream_restriction_is_not_retried_via_direct_telegram(self):
        self.session.get.return_value = response(
            b"FetchError: upstream: 403 Forbidden", status=500
        )
        with self.assertRaises(PilotStopped):
            self.fetcher("demo")
        self.session.get.assert_called_once()

    def test_oversize_redirect_and_invalid_xml_are_not_samples(self):
        for result in [
            response(b"x" * (MAX_FEED_BYTES + 1)),
            response(status=302),
            response(b"<html>failed</html>"),
        ]:
            self.state["requests"] = 0
            self.state["requests_by_channel"] = {}
            self.session.get.return_value = result
            with self.assertRaises(ValueError):
                self.fetcher("demo")
            result.close.assert_called_once()

    def test_instance_connection_failure_stops_before_spending_more_budget(self):
        import requests

        self.session.get.side_effect = requests.ConnectionError("server offline")
        with self.assertRaises(PilotStopped):
            self.fetcher("demo")
        self.assertEqual(self.state["requests"], 1)
        self.assertEqual(self.state["stop_kind"], "rsshub_unavailable")

    def test_resume_is_explicit_and_keeps_ledger(self):
        state = dict(
            transport="rsshub",
            rsshub_url="http://localhost:1200",
            stopped=True,
            stop_kind="rsshub_access",
            stop_reason="Ограничение RSSHub: HTTP 429",
            requests=17,
            request_budget=3000,
            requests_by_channel={"demo": 1},
            retry_not_before=200,
            deadline=500,
        )
        with self.assertRaises(ValueError):
            resume_rsshub_run(state, "http://localhost:1300", now=300)
        with self.assertRaises(ValueError):
            resume_rsshub_run(state, "http://localhost:1200", now=100)
        resume_rsshub_run(state, "http://localhost:1200", now=300)
        self.assertEqual(state["requests"], 17)
        self.assertEqual(state["requests_by_channel"], {"demo": 1})
        self.assertEqual(state["deadline"], 500)
        self.assertFalse(state["stopped"])

    def test_real_http_roundtrip_against_local_feed_fixture(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import requests

        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                self.end_headers()
                self.wfile.write(rss())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with requests.Session() as session:
                fetcher = RSSHubFetcher(
                    self.state,
                    Mock(),
                    session,
                    f"http://127.0.0.1:{server.server_port}",
                )
                sample = fetcher("demo")
            self.assertEqual(len(suitable_posts(sample)), 4)
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].startswith("/telegram/channel/demo/"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class RSSCollectionTests(unittest.TestCase):
    def test_change_from_failed_localhost_preserves_budget_snapshot_and_posts(self):
        import requests

        with (
            tempfile.TemporaryDirectory() as folder,
            patch("src.catalog_collection.time.sleep"),
        ):
            root = Path(folder)
            cache = root / "cache"
            cache.mkdir()
            (cache / "demo.json").write_text(
                json.dumps(parse_feed(rss(), "demo")), encoding="utf-8"
            )
            rows = [dict(username=u) for u in ("demo", "failed", "second")]
            run = root / "run"
            unavailable = Mock()
            unavailable.get.side_effect = requests.ConnectionError(
                "localhost not running"
            )
            before = collect(
                rows,
                run,
                database=False,
                cache_directories=[cache],
                rsshub_url="http://127.0.0.1:1200",
                session=unavailable,
            )
            self.assertEqual(before["stop_kind"], "rsshub_unavailable")
            self.assertEqual(before["requests"], 1)
            sample_bytes = (run / "posts/demo.json").read_bytes()
            snapshot_bytes = (run / "snapshot.json").read_bytes()
            remote = Mock()
            remote.get.return_value = response(rss("second"))
            after = collect(
                rows,
                run,
                database=False,
                rsshub_url="https://rss.example.org",
                resume_rsshub=True,
                change_rsshub=True,
                session=remote,
            )
            self.assertEqual(after["rsshub_url"], "https://rss.example.org")
            self.assertEqual(after["requests"], 2)
            self.assertEqual(after["requests_by_channel"], {"failed": 1, "second": 1})
            self.assertEqual(after["request_budget"], before["request_budget"])
            self.assertEqual(after["deadline"], before["deadline"])
            self.assertEqual(after["snapshot_sha256"], before["snapshot_sha256"])
            self.assertEqual(
                after["rsshub_url_changes"][0]["previous"], "http://127.0.0.1:1200"
            )
            self.assertEqual(after["rsshub_url_changes"][0]["requests_used"], 1)
            self.assertFalse(after["stopped"])
            self.assertEqual((run / "posts/demo.json").read_bytes(), sample_bytes)
            self.assertEqual((run / "snapshot.json").read_bytes(), snapshot_bytes)
            self.assertEqual(remote.get.call_count, 1)
            self.assertTrue(
                remote.get.call_args.args[0].startswith(
                    "https://rss.example.org/telegram/channel/second/"
                )
            )

    def test_url_change_cannot_clear_access_blocks_and_does_not_write_on_rejection(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            rows = [dict(username="demo")]
            collect(rows, folder, database=False)
            path = Path(folder) / "collection.json"
            for reason in (
                "Ограничение RSSHub: HTTP 403",
                "Ограничение RSSHub: HTTP 429",
                "RSSHub сообщил об ограничении upstream Telegram",
            ):
                state = json.loads(path.read_text(encoding="utf-8"))
                state.update(
                    transport="rsshub",
                    rsshub_url="http://127.0.0.1:1200",
                    stopped=True,
                    stop_kind="rsshub_access",
                    stop_reason=reason,
                )
                path.write_text(json.dumps(state), encoding="utf-8")
                original = path.read_bytes()
                session = Mock()
                with self.subTest(reason=reason), self.assertRaises(ValueError):
                    collect(
                        rows,
                        folder,
                        database=False,
                        rsshub_url="https://rss.example.org",
                        resume_rsshub=True,
                        change_rsshub=True,
                        session=session,
                    )
                self.assertEqual(path.read_bytes(), original)
                session.get.assert_not_called()

    def test_url_change_checks_pause_budget_and_never_extends_deadline(self):
        base = dict(
            transport="rsshub",
            rsshub_url="http://127.0.0.1:1200",
            stopped=True,
            stop_kind="rsshub_unavailable",
            requests=1,
            request_budget=3000,
            deadline=50,
        )
        for altered in (
            {"retry_not_before": 200},
            {"requests": 3000},
            {"stopped": False},
        ):
            state = {**base, **altered}
            original = json.loads(json.dumps(state))
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                change_rsshub_url(state, "https://rss.example.org", now=100)
            self.assertEqual(state, original)
        state = dict(base)
        change_rsshub_url(state, "https://rss.example.org/", now=100)
        self.assertEqual(state["deadline"], 50)
        self.assertTrue(state["stopped"])
        self.assertEqual(state["rsshub_url"], "https://rss.example.org")

    def test_url_change_requires_explicit_flag_and_existing_run(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [dict(username="demo")]
            for options in (
                {"change_rsshub": True},
                {"change_rsshub": True, "resume_rsshub": True},
            ):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    collect(
                        rows,
                        folder,
                        database=False,
                        rsshub_url="https://rss.example.org",
                        **options,
                    )
            self.assertFalse((Path(folder) / "collection.json").exists())

    def test_new_run_uses_rss_and_keeps_existing_cache_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Mock()
            session.get.return_value = response()
            state = collect(
                [dict(username="demo")],
                folder,
                database=False,
                rsshub_url="http://localhost:1200",
                session=session,
            )
            self.assertEqual(state["transport"], "rsshub")
            self.assertEqual(state["channels"]["demo"]["status"], "usable")
            self.assertEqual(state["channels"]["demo"]["source"], "rsshub")
            self.assertTrue((Path(folder) / "posts/demo.json").exists())
            again = collect(
                [dict(username="demo")],
                folder,
                database=False,
                rsshub_url="http://localhost:1200",
                session=session,
            )
            self.assertEqual(again["requests"], 1)
            self.assertEqual(session.get.call_count, 1)
            with self.assertRaises(ValueError):
                collect(
                    [dict(username="demo")],
                    folder,
                    database=False,
                    rsshub_url="http://localhost:1300",
                    session=session,
                )

    def test_switch_preserves_snapshot_cache_budget_and_channel_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "cache"
            cache.mkdir()
            (cache / "demo.json").write_text(
                json.dumps(parse_feed(rss(), "demo")), encoding="utf-8"
            )
            rows = [dict(username="demo"), dict(username="second")]
            run = root / "run"
            state = collect(rows, run, database=False, cache_directories=[cache])
            state.update(requests=17, requests_by_channel={"demo": 1})
            (run / "collection.json").write_text(json.dumps(state), encoding="utf-8")
            original = (run / "posts/demo.json").read_bytes()
            snapshot = (run / "snapshot.json").read_bytes()
            session = Mock()
            session.get.return_value = response(rss("second"))
            with self.assertRaises(ValueError):
                collect(
                    rows,
                    run,
                    database=False,
                    rsshub_url="http://localhost:1200",
                    session=session,
                )
            session.get.assert_not_called()
            result = collect(
                rows,
                run,
                database=False,
                rsshub_url="http://localhost:1200",
                switch_to_rsshub=True,
                session=session,
            )
            self.assertEqual(result["requests"], 18)
            self.assertEqual(result["deadline"], state["deadline"])
            self.assertEqual(result["request_budget"], state["request_budget"])
            self.assertEqual(result["transport_changes"][0]["requests_used"], 17)
            self.assertEqual((run / "snapshot.json").read_bytes(), snapshot)
            self.assertEqual((run / "posts/demo.json").read_bytes(), original)

    def test_switch_cannot_clear_previous_block_or_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [dict(username="demo")]
            collect(rows, folder, database=False)
            path = Path(folder) / "collection.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state.update(stopped=True, stop_reason="Ограничение Telegram: HTTP 429")
            path.write_text(json.dumps(state), encoding="utf-8")
            before = path.read_bytes()
            session = Mock()
            with self.assertRaises(ValueError):
                collect(
                    rows,
                    folder,
                    database=False,
                    rsshub_url="http://localhost:1200",
                    switch_to_rsshub=True,
                    session=session,
                )
            self.assertEqual(path.read_bytes(), before)
            session.get.assert_not_called()

    def test_rss_limit_stops_whole_run_and_does_not_reset_on_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Mock()
            session.get.return_value = response(status=429)
            rows = [dict(username="demo"), dict(username="second")]
            state = collect(
                rows,
                folder,
                database=False,
                rsshub_url="http://localhost:1200",
                session=session,
            )
            self.assertTrue(state["stopped"])
            self.assertEqual(state["requests"], 1)
            with self.assertRaises(ValueError):
                collect(
                    rows,
                    folder,
                    database=False,
                    rsshub_url="http://localhost:1200",
                    session=session,
                )
            self.assertEqual(session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
