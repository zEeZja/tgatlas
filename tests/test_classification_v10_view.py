"""Автономный отчёт не запускает классификацию и проверяет связность снимка."""

import copy
import json
import unittest

from src.classification_v10_view import build_view


def fixture():
    snapshot = [
        dict(
            username="demo",
            name="Канал <script>alert(1)</script>",
            description="Новости науки",
            subcategory="tech",
            topic_source="heuristic",
        )
    ]
    results = dict(
        format="atlas-semantic-results-v10",
        model_sha256="abc",
        channels={
            "demo": dict(
                classification_engine="semantic-v10",
                classification_profile=dict(
                    name=snapshot[0]["name"], description="Новости науки"
                ),
                subcategory="science",
                topic_review_required=True,
                topic_candidates=[dict(id="science", score=-0.2)],
                classification_review_reasons=["insufficient_calibration"],
                topic_evidence=[
                    dict(source="profile", text="</script><script>alert(2)</script>")
                ],
            )
        },
    )
    return snapshot, results


class ClassificationV10ViewTests(unittest.TestCase):
    def test_embedded_data_is_safe_and_preserves_scores(self):
        snapshot, results = fixture()
        html = build_view(snapshot, results)
        self.assertNotIn("/*__DATA__*/", html)
        self.assertNotIn("/*__SCRIPT__*/", html)
        payload = html.split('<script id="view-data" type="application/json">', 1)[
            1
        ].split("</script>", 1)[0]
        self.assertNotIn("<script>", payload)
        data = json.loads(payload)
        self.assertEqual(data["rows"][0]["candidates"][0]["score"], -0.2)
        self.assertEqual(data["rows"][0]["current"], "tech")
        self.assertEqual(data["rows"][0]["predicted"], "science")
        self.assertIn("<script>", data["rows"][0]["name"])

    def test_input_is_not_mutated_and_fingerprint_is_stable(self):
        snapshot, results = fixture()
        before = copy.deepcopy([snapshot, results])
        self.assertEqual(build_view(snapshot, results), build_view(snapshot, results))
        self.assertEqual([snapshot, results], before)

    def test_rejects_mismatched_profiles_unknown_topics_and_nonfinite_scores(self):
        for change in [
            dict(classification_profile={}),
            dict(subcategory="missing"),
            dict(topic_candidates=[dict(id="science", score=float("nan"))]),
        ]:
            snapshot, results = fixture()
            results["channels"]["demo"].update(change)
            with self.assertRaises(ValueError):
                build_view(snapshot, results)

    def test_rejects_foreign_and_duplicate_usernames(self):
        snapshot, results = fixture()
        with self.assertRaises(ValueError):
            build_view(snapshot * 2, results)
        results["channels"]["foreign"] = results["channels"]["demo"]
        with self.assertRaises(ValueError):
            build_view(snapshot, results)


if __name__ == "__main__":
    unittest.main()
