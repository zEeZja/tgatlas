from unittest.mock import patch

import psycopg2

from src import db, pipeline
from src.models import Channel, Post
from tests.integration.support import PostgresCase


def posts(*ids):
    return [Post(0, value, f"https://t.me/channel_test/{value}") for value in ids]


class CatalogueTests(PostgresCase):
    def setUp(self):
        super().setUp()
        db.init_schema(self.conn)
        self.channel = Channel(
            "channel_test", name="Original", telegram_id=123456, subscribers=5000
        )
        with db.transaction(self.conn):
            self.channel.id = db.upsert_channel(self.conn, self.channel)
            db.insert_posts(
                self.conn,
                [
                    Post(
                        self.channel.id,
                        100,
                        "https://t.me/channel_test/100",
                        text="Original post",
                    )
                ],
            )
            db.save_scrape_checkpoint(self.conn, self.channel.id, (90, 20))

    def test_real_sql_failure_rolls_back_channel_posts_and_cursor(self):
        bad = Post(0, 202, None)
        changed = Channel("channel_test", name="Should roll back", telegram_id=123456)
        with self.assertRaises(psycopg2.errors.NotNullViolation):
            pipeline._store_channel(
                self.conn, changed, "test", None, [posts(201), [bad]], (203, 100)
            )
        self.assertEqual(self.query("SELECT name FROM channels"), [("Original",)])
        self.assertEqual(
            self.query("SELECT tg_message_id,text FROM posts"), [(100, "Original post")]
        )
        self.assertEqual(
            db.get_scrape_checkpoints(self.conn), {self.channel.id: (90, 20)}
        )
        self.assertEqual(self.query("SELECT last_parsed_at FROM channels"), [(None,)])

    def test_metadata_constraint_error_is_not_swallowed(self):
        self.query(
            "ALTER TABLE channels ADD CONSTRAINT subscribers_nonnegative CHECK(subscribers>=0)"
        )
        self.conn.commit()
        with patch.object(
            pipeline,
            "parse_channel_meta",
            return_value={"name": "Bad", "description": "changed", "subscribers": -1},
        ):
            with self.assertRaises(psycopg2.errors.CheckViolation):
                pipeline._store_channel(
                    self.conn, self.channel, "test", "profile", [posts(201)], None
                )
        self.assertEqual(
            self.query("SELECT name,subscribers FROM channels"), [("Original", 5000)]
        )
        self.assertEqual(
            db.get_scrape_checkpoints(self.conn), {self.channel.id: (90, 20)}
        )
        self.assertEqual(self.query("SELECT COUNT(*) FROM posts"), [(1,)])

    def test_bulk_post_deduplication_spans_multiple_batches(self):
        batch = [
            Post(self.channel.id, value, f"https://t.me/channel_test/{value}")
            for value in range(1000, 2200)
        ]
        with db.transaction(self.conn):
            self.assertEqual(db.insert_posts(self.conn, batch), 1200)
            self.assertEqual(db.insert_posts(self.conn, batch), 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM posts"), [(1201,)])
        self.assertEqual(
            db.get_post_watermarks(self.conn, [self.channel.id]),
            {self.channel.id: 2199},
        )

    def test_username_change_keeps_telegram_identity_posts_and_sources(self):
        with db.transaction(self.conn):
            renamed = db.upsert_channel(
                self.conn, Channel("channel_renamed", telegram_id=123456)
            )
        self.assertEqual(renamed, self.channel.id)
        self.assertEqual(
            self.query("SELECT username,telegram_id FROM channels"),
            [("channel_renamed", 123456)],
        )
        self.assertEqual(
            self.query("SELECT channel_id FROM posts"), [(self.channel.id,)]
        )
        self.assertEqual(
            self.query("SELECT DISTINCT channel_id FROM channel_sources"),
            [(self.channel.id,)],
        )

    def test_conflicting_identity_does_not_merge_or_overwrite_channels(self):
        with db.transaction(self.conn):
            db.upsert_channel(self.conn, Channel("another_channel", telegram_id=999))
        with self.assertRaises(ValueError):
            with db.transaction(self.conn):
                db.upsert_channel(
                    self.conn, Channel("another_channel", telegram_id=123456)
                )
        self.assertEqual(
            self.query("SELECT username,telegram_id FROM channels ORDER BY username"),
            [("another_channel", 999), ("channel_test", 123456)],
        )
        with self.assertRaises(ValueError):
            with db.transaction(self.conn):
                db.upsert_channel(self.conn, Channel("channel_test", telegram_id=777))
        self.assertEqual(
            self.query(
                "SELECT telegram_id FROM channels WHERE id=%s", (self.channel.id,)
            ),
            [(123456,)],
        )

    def test_cursor_survives_reconnection_and_resumes_without_losing_delta(self):
        with db.transaction(self.conn):
            db.save_scrape_checkpoint(self.conn, self.channel.id, None)
        with (
            patch.object(pipeline, "fetch_channel_html", return_value="html"),
            patch.object(pipeline, "parse_channel_page", return_value=posts(202, 201)),
        ):
            html, batches, cursor = pipeline._fetch_pages(
                self.channel, last_seen=100, max_pages=1
            )
        # Metadata is irrelevant to this network simulation; SQL persistence is real.
        pipeline._store_channel(self.conn, self.channel, "test", None, batches, cursor)
        self.conn.close()
        self.conn = self.connect()
        self.assertEqual(
            db.get_scrape_checkpoints(self.conn), {self.channel.id: (201, 100)}
        )
        watermark = db.get_post_watermarks(self.conn, [self.channel.id])[
            self.channel.id
        ]
        with (
            patch.object(
                pipeline, "fetch_channel_html", return_value="html"
            ) as network,
            patch.object(
                pipeline,
                "parse_channel_page",
                side_effect=[
                    posts(205, 204),
                    posts(203, 202, 201),
                    posts(200, 199, 100),
                ],
            ),
        ):
            _, batches, cursor = pipeline._fetch_pages(
                self.channel,
                last_seen=watermark,
                max_pages=3,
                checkpoint=db.get_scrape_checkpoints(self.conn)[self.channel.id],
            )
        pipeline._store_channel(self.conn, self.channel, "test", None, batches, cursor)
        self.assertEqual(
            [call.kwargs["before"] for call in network.call_args_list], [None, 204, 201]
        )
        self.assertEqual(db.get_scrape_checkpoints(self.conn), {})
        self.assertEqual(
            self.query("SELECT tg_message_id FROM posts ORDER BY tg_message_id"),
            [(value,) for value in (100, 199, 200, 201, 202, 203, 204, 205)],
        )

    def test_foreign_keys_and_cascade_cleanup(self):
        with db.transaction(self.conn):
            self.query("DELETE FROM channels WHERE id=%s", (self.channel.id,))
        for table in ("posts", "channel_sources", "scrape_checkpoints"):
            self.assertEqual(self.query("SELECT COUNT(*) FROM " + table), [(0,)])
