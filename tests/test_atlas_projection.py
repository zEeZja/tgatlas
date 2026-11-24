"""Офлайн-проекция: политика, паритет браузера и безопасная публикация."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from src.atlas_projection import distance_matrices, make_artifact, validate_artifact
from src.layout_policy import geography_role, load_policy, row_binding

ROOT = Path(__file__).resolve().parent.parent


class AtlasProjectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.rows = [
            dict(
                id=str(i),
                username="channel_" + str(i),
                name="Новости",
                subcategory=topic,
                geography_scope="region",
                geography_regions=[region],
                geography_country="RU",
            )
            for i, (topic, region) in enumerate(
                [
                    ("v11_general_news", "Москва"),
                    ("v11_general_news", "Приморский край"),
                    ("v11_crypto", "Москва"),
                    ("v11_crypto", "Приморский край"),
                ]
            )
        ]

    def test_geography_does_not_split_subject_channels(self):
        original = copy.deepcopy(self.rows)
        roles = [geography_role(r, self.policy) for r in self.rows]
        self.assertEqual(self.rows, original)
        self.assertEqual(
            [r["kind"] for r in roles], ["region", "region", "topic", "topic"]
        )
        self.rows[0]["geography_scope"] = "unknown"
        self.assertEqual(geography_role(self.rows[0], self.policy)["kind"], "topic")
        self.assertEqual(original[1:], self.rows[1:])

    def test_v2_group_and_local_clinic_keep_relevant_region_without_fake_topic(self):
        coarse = dict(
            self.rows[0],
            classification_engine="semantic-v11v2",
            subcategory="other",
            topic_group="v11_news_society",
            topic_granularity="group",
        )
        self.assertEqual(geography_role(coarse, self.policy)["kind"], "region")
        clinic = dict(
            self.rows[0],
            classification_engine="semantic-v11v2",
            subcategory="v11_medicine",
            geography_review_reasons=["local_service_profile_location"],
        )
        self.assertEqual(geography_role(clinic, self.policy)["kind"], "region")
        crypto = dict(clinic, subcategory="v11_crypto_markets")
        self.assertEqual(geography_role(crypto, self.policy)["kind"], "topic")
        import numpy as np

        rows = [coarse, dict(coarse, id="extra", username="extra")]
        roles = [geography_role(row, self.policy) for row in rows]
        artifact = make_artifact(
            rows,
            roles,
            np.array([[120.0, 120.0], [150.0, 150.0]]),
            self.policy,
            "tsne",
            {},
        )
        self.assertFalse(
            any(a["kind"] == "topic" for a in artifact["layout"]["annotations"])
        )

    def test_geographic_proof_must_support_selected_region(self):
        row = dict(
            self.rows[0],
            subcategory="v11_careers",
            name="Работа в городе",
            geography_review_required=True,
            geography_candidates=[dict(region="Приморский край", title_match=True)],
        )
        self.assertEqual(geography_role(row, self.policy)["kind"], "topic")
        row["geography_candidates"][0]["region"] = "Москва"
        role = geography_role(row, self.policy)
        self.assertEqual(role["kind"], "region")
        self.assertEqual(role["weight"], self.policy["profile_region_weight"])
        self.assertTrue(
            role["provisional"], "Profile evidence is not owner confirmation"
        )

    def test_distances_only_condition_regional_pairs(self):
        import numpy as np

        vectors = np.eye(4, dtype=np.float32)
        roles = [geography_role(r, self.policy) for r in self.rows]
        raw, conditioned = distance_matrices(vectors, roles, self.policy)
        self.assertGreater(conditioned[0, 1], raw[0, 1])
        self.assertEqual(conditioned[2, 3], raw[2, 3])
        np.testing.assert_array_equal(conditioned, conditioned.T)

    def test_dense_projection_has_no_overlaps(self):
        import numpy as np
        from src.atlas_projection import separate_points

        points = np.concatenate([np.zeros((80, 2)), np.ones((20, 2))])
        final, _ = separate_points(points, self.policy)
        distances = np.linalg.norm(final[:, None, :] - final[None, :, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        self.assertGreaterEqual(
            float(distances.min()), self.policy["geometry"]["minimum_separation"] - 1e-7
        )
        repeated, _ = separate_points(points, self.policy)
        np.testing.assert_array_equal(final, repeated)

    def test_reject_corrupt_geometry(self):
        import numpy as np

        roles = [geography_role(r, self.policy) for r in self.rows]
        artifact = make_artifact(
            self.rows,
            roles,
            np.array([[120.0, 120.0], [150.0, 150.0], [180.0, 180.0], [210.0, 210.0]]),
            self.policy,
            "umap",
            {},
        )
        validate_artifact(artifact, self.rows, self.policy)
        for change in ("duplicate", "nan", "missing", "topic", "cloud", "annotation"):
            damaged = copy.deepcopy(artifact)
            nodes = damaged["layout"]["nodes"]
            if change == "duplicate":
                nodes[1] = nodes[0]
            if change == "nan":
                nodes[0]["x"] = float("nan")
            if change == "missing":
                nodes.pop()
            if change == "topic":
                nodes[0]["topic"] = "other"
            if change == "cloud":
                damaged["layout"]["clouds"][0]["radius"] = float("inf")
            if change == "annotation":
                damaged["layout"]["annotations"][0]["x"] = float("nan")
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_artifact(damaged, self.rows, self.policy)

    def test_python_javascript_parity(self):
        node = shutil.which("node") or "C:/ignatovia/NodeJs/node.exe"
        if not Path(node).exists() and not shutil.which(node):
            self.skipTest("Node не установлен")
        rows = self.rows + [
            dict(
                self.rows[0],
                description="Афиша\nРеклама",
                geography_review_required=True,
            ),
            dict(
                self.rows[0],
                geography_candidates=[dict(region="Москва", title_match=True)],
                geography_review_required=True,
            ),
            dict(
                self.rows[0],
                subcategory="v11_careers",
                name="Работа в городе",
                geography_candidates=[dict(region="Приморский край", title_match=True)],
            ),
        ]
        rows.extend(
            [
                dict(
                    self.rows[0],
                    classification_engine="semantic-v11v2",
                    topic_granularity="group",
                    subcategory="other",
                    topic_group="v11_news_society",
                    classification_policy_sha256="policy",
                    classification_implementation_sha256="implementation",
                ),
                dict(
                    self.rows[0],
                    classification_engine="semantic-v11v2",
                    subcategory="v11_medicine",
                    geography_review_reasons=["local_service_profile_location"],
                ),
            ]
        )
        script = """
const fs=require('fs'),vm=require('vm');const c={window:{}};vm.createContext(c);
for(const f of ['layout-policy','layout'])vm.runInContext(fs.readFileSync('web/'+f+'.js','utf8'),c);
const rows=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(rows.map(r=>[c.window.Layout.geographyRole(r),c.window.Layout.rowBinding(r)])));
"""
        result = subprocess.run(
            [node, "-e", script],
            input=json.dumps(rows, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            check=True,
        )
        expected = [[geography_role(r, self.policy), row_binding(r)] for r in rows]
        self.assertEqual(json.loads(result.stdout), expected)

    def test_publish_rejects_before_writing(self):
        import numpy as np
        from src.atlas_projection import publish

        roles = [geography_role(r, self.policy) for r in self.rows]
        artifact = make_artifact(
            self.rows,
            roles,
            np.array([[120.0, 120.0], [150.0, 150.0], [180.0, 180.0], [210.0, 210.0]]),
            self.policy,
            "umap",
            {},
        )
        artifact["layout"]["nodes"].pop()
        with (
            patch(
                "src.atlas_projection.Path.read_text", return_value=json.dumps(artifact)
            ),
            patch("src.atlas_projection.load_policy", return_value=self.policy),
            patch("src.atlas_projection.snapshot_rows", return_value=self.rows),
            patch("src.atlas_projection.atomic_write_text") as writer,
        ):
            with self.assertRaises(ValueError):
                publish("bad.json", apply=True)
            writer.assert_not_called()
