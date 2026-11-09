import unittest

from src.region_classifier import classify_channel


class RegionClassifierTests(unittest.TestCase):
    def test_federal_is_explicit_whitelist_only(self):
        result = classify_channel("@big_news", "Big News", "Новости по всей России", federal_channels={"big_news"})
        self.assertEqual(result.channel_type, "federal")
        self.assertIsNone(result.region)

    def test_nizhny_novgorod_phrase_beats_novgorod_substring(self):
        result = classify_channel("mash_nimash", "Mash Нижний Новгород")
        self.assertEqual(result.region, "Нижегородская область")

    def test_description_can_use_explicit_geographic_context(self):
        result = classify_channel("allnews47", "47news", "Все новости Ленобласти")
        self.assertEqual(result.region, "Ленинградская область")

    def test_short_word_does_not_match_longer_unrelated_word(self):
        result = classify_channel("tyumenreads", "Тюмень Читает", region_hint="Тюмень")
        self.assertEqual(result.region, "Тюменская область")
        self.assertNotEqual(result.region, "Забайкальский край")

    def test_two_regions_in_name_are_sent_for_review(self):
        result = classify_channel(
            "babr_mash", "Mash Иркутск, Бурятия", region_hint="Иркутск"
        )
        self.assertEqual(result.channel_type, "unknown")
        self.assertIsNone(result.region)
        self.assertGreaterEqual(len(result.candidates), 2)

    def test_ambiguous_primorsky_district_is_not_guessed(self):
        result = classify_channel("primarea", "Приморский район Live")
        self.assertEqual(result.channel_type, "unknown")
        self.assertIsNone(result.region)

    def test_lugansk_is_not_confused_with_luga(self):
        result = classify_channel("ak_lugansk", "Актуальный Луганск — новости ЛНР")
        self.assertEqual(result.region, "Луганская Народная Республика")

    def test_username_region_code_after_underscore_is_recognized(self):
        result = classify_channel("okidoki_spb", "ОКИ ДОКИ")
        self.assertEqual(result.region, "Санкт-Петербург")


if __name__ == "__main__":
    unittest.main()
