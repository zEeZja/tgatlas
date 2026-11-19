import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from src.taxonomy_pilot import BoundedFetcher, PilotStopped, collect_pilot, select_pilot


class TaxonomyPilotTests(unittest.TestCase):
    def test_selection_is_bounded_deterministic_and_covers_topics(self):
        rows = [
            dict(username=f"channel_{i}", subcategory=f"topic_{i % 20}")
            for i in range(200)
        ]
        predictions = {
            r["username"]: dict(subcategory=r["subcategory"] if i % 2 else "other")
            for i, r in enumerate(rows)
        }
        selected = select_pilot(rows, predictions)
        self.assertEqual(len(selected), 120)
        self.assertEqual(len({r["username"] for r in selected}), 120)
        self.assertEqual(len({r["subcategory"] for r in selected}), 20)
        self.assertEqual(selected, select_pilot(rows[::-1], predictions))
        with self.assertRaises(ValueError):
            select_pilot(rows, predictions, 121)

    def test_request_is_reserved_before_sending_no_retries_or_redirects(self):
        state = dict(requests=0, request_budget=1)
        save = Mock()
        session = Mock()

        def get(*args, **kwargs):
            self.assertEqual(state["requests"], 1)
            save.assert_called_once()
            self.assertFalse(kwargs["allow_redirects"])
            return Mock(status_code=200, text="public html")

        session.get.side_effect = get
        fetcher = BoundedFetcher(state, save, session)
        self.assertEqual(fetcher("demo"), "public html")
        with self.assertRaises(PilotStopped):
            fetcher("second")
        self.assertEqual(session.get.call_count, 1)

    def test_rate_limit_stops_whole_pilot_and_cannot_resume_silently(self):
        for status in (403, 429):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as directory,
            ):
                session = Mock()
                session.get.return_value = Mock(status_code=status)
                plan = [dict(username="first"), dict(username="second")]
                state = collect_pilot(plan, directory, session)
                self.assertTrue(state["stopped"])
                self.assertEqual(state["requests"], 1)
                self.assertEqual(session.get.call_count, 1)
                saved = json.loads(
                    Path(directory, "collection.json").read_text(encoding="utf-8")
                )
                self.assertTrue(saved["stopped"])
                with self.assertRaises(ValueError):
                    collect_pilot(plan, directory, session)


if __name__ == "__main__":
    unittest.main()
