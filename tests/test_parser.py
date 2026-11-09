import unittest

from src.parser import parse_channel_page


class ParserTests(unittest.TestCase):
    def test_parse_channel_page_builds_complete_post_model(self):
        html = """
        <div class="tgme_widget_message" data-post="breakingmash/123">
          <div class="tgme_widget_message_text">Первая строка заголовка<br>Текст публикации</div>
          <time datetime="2026-11-03T10:15:00+00:00"></time>
          <span class="tgme_widget_message_views">1.2K</span>
        </div>
        """

        posts = parse_channel_page(html, "breakingmash", channel_id=7)

        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.channel_id, 7)
        self.assertEqual(post.tg_message_id, 123)
        self.assertEqual(post.url, "https://t.me/breakingmash/123")
        self.assertEqual(post.text, "Первая строка заголовка\nТекст публикации")
        self.assertEqual(post.title, "Первая строка заголовка")
        self.assertEqual(post.views, 1200)
        self.assertIsNotNone(post.posted_at)
        self.assertEqual(post.posted_at.isoformat(), "2026-11-03T10:15:00+00:00")


    def test_parse_channel_meta_extracts_channel_title(self):
        from src.parser import parse_channel_meta

        html = """
        <div class="tgme_channel_info_header_title"><span>Городские новости</span></div>
        <div class="tgme_channel_info_description">Новости города</div>
        <div class="tgme_channel_info_counter">
          <span class="counter_value">12.5K</span>
          <span class="counter_type">subscribers</span>
        </div>
        """
        meta = parse_channel_meta(html)
        self.assertEqual(meta["name"], "Городские новости")
        self.assertEqual(meta["description"], "Новости города")
        self.assertEqual(meta["subscribers"], 12500)


    def test_parse_channel_meta_handles_russian_thousand_suffix(self):
        from src.parser import parse_channel_meta

        html = """
        <div class="tgme_channel_info_counter">
          <span class="counter_value">1,2 тыс.</span>
          <span class="counter_type">подписчиков</span>
        </div>
        """
        meta = parse_channel_meta(html)
        self.assertEqual(meta["subscribers"], 1200)

    def test_parse_channel_page_accepts_missing_optional_metadata(self):
        html = """
        <div class="tgme_widget_message" data-post="breakingmash/124">
          <div class="tgme_widget_message_text">Только текст</div>
        </div>
        """

        posts = parse_channel_page(html, "breakingmash", channel_id=7)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].text, "Только текст")
        self.assertEqual(posts[0].title, "Только текст")
        self.assertIsNone(posts[0].posted_at)
        self.assertIsNone(posts[0].views)


if __name__ == "__main__":
    unittest.main()
