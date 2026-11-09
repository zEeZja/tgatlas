import unittest

from src.audience import meets_minimum_subscribers, normalize_candidate_state


class AudienceFilterTests(unittest.TestCase):
    def test_exact_threshold_is_accepted(self):
        self.assertTrue(meets_minimum_subscribers(1000, 1000))
        self.assertEqual(normalize_candidate_state("validated", 1000, 1000), ("validated", None))

    def test_below_threshold_is_rejected(self):
        self.assertFalse(meets_minimum_subscribers(999, 1000))
        self.assertEqual(normalize_candidate_state("pending", 999, 1000),
                         ("rejected", "below_min_subscribers:999<1000"))

    def test_unknown_audience_never_passes(self):
        self.assertFalse(meets_minimum_subscribers(None, 1000))
        self.assertEqual(normalize_candidate_state("validated", None, 1000),
                         ("pending", "subscriber_count_unknown"))

    def test_parses_human_readable_counts(self):
        self.assertTrue(meets_minimum_subscribers("1,2 тыс.", 1000))
        self.assertFalse(meets_minimum_subscribers("850", 1000))

    def test_invalid_states_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_candidate_state("imported", 1000, 1000)


if __name__ == "__main__":
    unittest.main()
