import tempfile
import unittest
from pathlib import Path

from src.file_discovery import _records_from_file, _to_candidate


class FileDiscoveryTests(unittest.TestCase):
    def test_txt_accepts_usernames_and_public_channel_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "channels.txt"
            path.write_text(
                "# source list\n@BreakingMash\nhttps://t.me/local_news\n\n; comment\n",
                encoding="utf-8",
            )
            rows = _records_from_file(path)
        candidates = [_to_candidate(row) for row in rows]
        self.assertEqual(
            [item["username"] for item in candidates if item],
            ["breakingmash", "local_news"],
        )

    def test_rejects_post_links_and_invite_links(self):
        rows = [
            {"username": "https://t.me/local_news/123"},
            {"username": "https://t.me/+inviteHash"},
        ]
        self.assertEqual([_to_candidate(row) for row in rows], [None, None])

    def test_accepts_metadata_from_csv_style_row(self):
        candidate = _to_candidate({
            "username": "@local_news",
            "title": "Новости города",
            "description": "Региональные новости",
            "subscribers": "12,5K",
            "category": "news",
        })
        self.assertEqual(candidate["username"], "local_news")
        self.assertEqual(candidate["name"], "Новости города")
        self.assertEqual(candidate["subscribers"], 12500)
        self.assertEqual(candidate["category"], "news")


if __name__ == "__main__":
    unittest.main()
