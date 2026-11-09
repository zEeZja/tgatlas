import unittest

from src.build_geo_map import build_payload, project_lcc


class GeoMapTests(unittest.TestCase):
    def test_projection_is_finite(self):
        x, y = project_lcc(55.7558, 37.6173)
        self.assertTrue(abs(x) < 10)
        self.assertTrue(abs(y) < 10)

    def test_only_regional_channels_are_plotted(self):
        payload = build_payload([
            {"username": "mos_a", "name": "Москва A", "region": "Москва", "channel_type": "regional"},
            {"username": "fed_a", "name": "Федеральный", "region": None, "channel_type": "federal"},
            {"username": "unknown_a", "name": "Без региона", "region": None, "channel_type": "unknown"},
        ])
        self.assertEqual(len(payload["points"]), 1)
        self.assertEqual(payload["meta"]["federal"], 1)
        self.assertEqual(payload["meta"]["unknown"], 1)
        self.assertEqual(payload["points"][0]["username"], "mos_a")

    def test_layout_has_geographic_aspect_ratio(self):
        payload = build_payload([
            {"username": "msk_a", "region": "Москва", "channel_type": "regional"},
            {"username": "vlad_a", "region": "Приморский край", "channel_type": "regional"},
        ])
        ratio = payload["height"] / payload["width"]
        self.assertGreater(ratio, 0.25)
        self.assertLess(ratio, 0.65)


if __name__ == "__main__":
    unittest.main()
