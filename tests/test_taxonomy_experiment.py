import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from src.taxonomy_experiment import (
    load_taxonomy,
    legacy_mapping,
    render_review,
    analyze_pilot,
)


class TaxonomyDraftTests(unittest.TestCase):
    def test_taxonomy_inactive_defined_and_no_duplicate_leaf_ids(self):
        draft = load_taxonomy()
        self.assertFalse(draft["active"])
        topics = [t for g in draft["groups"] for t in g["topics"]]
        self.assertEqual(len(topics), len({t["id"] for t in topics}))
        self.assertTrue(all(t["definition"] and t["boundary"] for t in topics))
        self.assertNotIn("government", {t["id"] for t in topics})
        self.assertNotIn("air_transport", {t["id"] for t in topics})

    def test_legacy_mapping_is_not_authorization_to_migrate(self):
        mapping = {r["id"]: r for r in legacy_mapping(load_taxonomy())}
        self.assertTrue(all(not r["automatic"] for r in mapping.values()))
        self.assertEqual(mapping["other"]["candidates"], [])
        self.assertEqual(
            mapping["aviation"]["candidates"], mapping["air_transport"]["candidates"]
        )
        self.assertEqual(len(mapping["design_fashion"]["candidates"]), 2)

    def test_standalone_safe_html_can_be_rendered_without_any_ml(self):
        draft = copy.deepcopy(load_taxonomy())
        draft["groups"][0]["definition"] = "</script><script>alert(1)</script>"
        html = render_review(draft)
        self.assertNotIn("/*__DATA__*/", html)
        self.assertNotIn("/*__SCRIPT__*/", html)
        payload = html.split('<script id="report-data" type="application/json">', 1)[
            1
        ].split("</script>", 1)[0]
        self.assertNotIn("<script>", payload)
        self.assertEqual(
            json.loads(payload)["taxonomy"]["groups"][0]["definition"],
            draft["groups"][0]["definition"],
        )

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "Нужен sklearn")
    def test_analysis_uses_posts_removes_shared_reposts_and_does_not_publish(self):
        import numpy as np

        class Encoder:
            def encode(self, texts):
                return np.asarray(
                    [[1, len(text) % 11 + 1, len(text) % 5 + 1] for text in texts],
                    dtype=np.float32,
                ) / np.linalg.norm(
                    np.asarray(
                        [[1, len(text) % 11 + 1, len(text) % 5 + 1] for text in texts],
                        dtype=np.float32,
                    ),
                    axis=1,
                    keepdims=True,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "posts").mkdir()
            plan = [
                dict(username=u, name=u, subcategory="science", v10_proposal="tech")
                for u in ("first", "second")
            ]
            (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            state = dict(channels={r["username"]: dict(status="ok") for r in plan})
            (root / "collection.json").write_text(json.dumps(state), encoding="utf-8")
            for row in plan:
                u = row["username"]
                sample = dict(
                    username=u,
                    purpose="classification_only",
                    posts=[
                        dict(text="Общий текст репоста", message_id=1),
                        *[
                            dict(
                                text=f"{u} исследование науки и технологий номер {i}",
                                message_id=i + 2,
                            )
                            for i in range(3)
                        ],
                    ],
                )
                (root / "posts" / (u + ".json")).write_text(
                    json.dumps(sample), encoding="utf-8"
                )
            before = {str(p): p.read_bytes() for p in root.rglob("*.json")}
            result = analyze_pilot(root, load_taxonomy(), Encoder(), {})
            self.assertEqual(result["summary"]["with_posts"], 2)
            self.assertEqual(result["shared_post_occurrences_removed"], 2)
            self.assertFalse(result["trained_classifier"])
            self.assertTrue(all(r["post_candidates"] for r in result["channels"]))
            self.assertEqual(
                before, {str(p): p.read_bytes() for p in root.rglob("*.json")}
            )


if __name__ == "__main__":
    unittest.main()
