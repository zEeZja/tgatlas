"""11v2 publication: complete snapshots, safe export and lightweight runtime."""

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src import classification_runtime as primary
from src import classification_v11v2_runtime as runtime
from src import primary_export

ROOT = Path(__file__).resolve().parent.parent


def fixture():
    rows = [
        dict(id=u, username=u, name=u, description="Проверочный профиль")
        for u in ("first", "second", "third")
    ]
    now = datetime.now(timezone.utc).isoformat()
    channels = {}
    for row, kind, topic, group in zip(
        rows,
        ("topic", "group", "abstain"),
        ("v11_crypto_markets", None, None),
        ("v11_crypto", "v11_business_work", None),
    ):
        channels[row["username"]] = dict(
            classification_engine="semantic-v11v2",
            classification_version="11v2",
            classification_profile=primary.profile(row),
            classification_created_at=now,
            classification_manual_dimensions=[],
            subcategory=topic,
            suggested_topic=topic or "v11_management",
            topic_group=group,
            topic_granularity=kind,
            topic_source="semantic-v11v2-proposal",
            topic_score=1.0,
            topic_candidates=[dict(id="v11_crypto_markets", score=1.0)],
            topic_review_required=True,
            topic_assignment_status="proposal_" + kind,
            classification_review_reasons=["not_independently_calibrated"],
            post_routing=dict(usable=0, fetched_at=None),
            topic_evidence=[],
            geography_scope="unknown",
            geography_country="",
            geography_regions=[],
            geography_source="semantic-v11v2-proposal",
            geography_review_required=True,
            geography_candidates=[],
            geography_review_reasons=["coverage_not_established"],
        )
    data = dict(
        format="atlas-results-v11v2",
        taxonomy_sha256=primary.taxonomy()["fingerprint"],
        input_sha256=primary.fingerprint(rows),
        created_at=now,
        remaining=0,
        channels=channels,
        policy_sha256=primary.fingerprint(
            json.loads(
                (ROOT / "config/classification_v11v2.json").read_text(encoding="utf-8")
            )
        ),
        geography_policy_sha256=primary.fingerprint(
            json.loads(
                (ROOT / "config/geography_v11v2.json").read_text(encoding="utf-8")
            )
        ),
    )
    return rows, data


@contextmanager
def sandbox(rows, data):
    primary.policy()  # cache actual policy before substituting filesystem roots
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "config").mkdir()
        for name in (
            "classification_v11v2.json",
            "geography_v11v2.json",
            "countries_v11.json",
        ):
            (root / "config" / name).write_bytes((ROOT / "config" / name).read_bytes())
        (root / "web/data").mkdir(parents=True)
        (root / "web/data/channels.json").write_text(
            json.dumps(dict(channels=rows)), encoding="utf-8"
        )
        (root / "web/data/channels.js").write_text("USER WORK", encoding="utf-8")
        run = root / "run"
        run.mkdir()
        (run / "snapshot.json").write_text(json.dumps(rows), encoding="utf-8")
        (run / "results.json").write_text(json.dumps(data), encoding="utf-8")
        with (
            patch.object(runtime, "ROOT", root),
            patch.object(primary, "ROOT", root),
            patch.object(primary_export, "ROOT", root),
        ):
            yield root, run


class V11V2PublicationTests(unittest.TestCase):
    def test_normalization_does_not_turn_group_or_abstention_into_topic(self):
        _, data = fixture()
        runtime.validate_results(data)
        group = runtime.public_result(data["channels"]["second"], data)
        abstain = runtime.public_result(data["channels"]["third"], data)
        self.assertEqual(group["subcategory"], "other")
        self.assertEqual(group["topic_group"], "v11_business_work")
        self.assertEqual(group["topic_tags"], [])
        self.assertEqual(abstain["topic_group"], "other_group")
        self.assertEqual(abstain["topic_tags"], [])
        self.assertIsNone(abstain["primary_category"])

    def test_pilot_cannot_silently_replace_full_catalog(self):
        rows, data = fixture()
        data["channels"] = {"first": data["channels"]["first"]}
        data["input_sha256"] = primary.fingerprint(rows[:1])
        with sandbox(rows, data) as (root, run):
            (run / "snapshot.json").write_text(json.dumps(rows[:1]), encoding="utf-8")
            with patch.object(runtime, "atomic_write_text") as write:
                with self.assertRaises(ValueError):
                    runtime.publish_run(run, apply=True)
                write.assert_not_called()
            self.assertFalse((root / runtime.CACHE).exists())

    def test_publish_and_export_preserve_all_channels_and_archive_user_js(self):
        rows, data = fixture()
        with sandbox(rows, data) as (root, run):
            legacy = root / "reports/classification-v11/catalog.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                json.dumps(
                    dict(
                        format="atlas-primary-results-v11",
                        taxonomy_sha256=primary.taxonomy()["fingerprint"],
                        channels={},
                    )
                ),
                encoding="utf-8",
            )
            original = legacy.read_bytes()
            self.assertFalse(runtime.publish_run(run)["applied"])
            self.assertFalse((root / runtime.CACHE).exists())
            runtime.publish_run(run, apply=True)
            loaded = primary.load_results()
            self.assertEqual(set(loaded), {"first", "second", "third"})
            exported = primary_export.export_current(
                apply=True, backup_root=root / "backups"
            )
            payload = json.loads(
                (root / "web/data/channels.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["channels"]), 3)
            self.assertEqual(payload["channels"][1]["topic_granularity"], "group")
            self.assertEqual(payload["channels"][2]["topic_granularity"], "abstain")
            self.assertEqual(
                (Path(exported["backup"]) / "channels.js").read_text(encoding="utf-8"),
                "USER WORK",
            )
            self.assertEqual(legacy.read_bytes(), original)
            before = (root / runtime.CACHE).read_bytes()
            runtime.publish_run(run, apply=True)
            backups = list(
                (root / runtime.CACHE).parent.glob("catalog-previous-*.json")
            )
            self.assertEqual(backups[0].read_bytes(), before)

    def test_profile_changes_and_corruption_fail_before_writes(self):
        for change in ("profile", "topic", "geography", "counter", "nan"):
            rows, data = fixture()
            with sandbox(rows, data) as (root, run):
                if change == "profile":
                    current = copy.deepcopy(rows)
                    current[0]["name"] = "Changed"
                    (root / "web/data/channels.json").write_text(
                        json.dumps(dict(channels=current)), encoding="utf-8"
                    )
                if change == "topic":
                    data["channels"]["first"]["subcategory"] = "missing"
                if change == "geography":
                    data["channels"]["first"]["geography_country"] = "ZZ"
                if change == "counter":
                    data["remaining"] = 1
                if change == "nan":
                    data["channels"]["first"]["topic_candidates"][0]["score"] = float(
                        "nan"
                    )
                (run / "results.json").write_text(json.dumps(data), encoding="utf-8")
                with (
                    self.subTest(change=change),
                    patch.object(runtime, "atomic_write_text") as write,
                ):
                    with self.assertRaises(ValueError):
                        runtime.publish_run(run, apply=True)
                    write.assert_not_called()

    def test_manual_answers_keep_priority_over_v2(self):
        rows, data = fixture()
        row = dict(
            rows[0],
            subcategory="v11_medicine",
            topic_group="v11_health",
            topic_source="manual",
            geography_source="manual",
            geography_scope="country",
            geography_country="CN",
            geography_regions=[],
        )
        proposal = runtime.public_result(data["channels"]["first"], data)
        merged = primary.merge_result(row, {"first": proposal})
        self.assertEqual(merged["subcategory"], "v11_medicine")
        self.assertEqual(merged["topic_tags"], ["v11_medicine"])
        self.assertEqual(merged["primary_category"], "v11_medicine")
        self.assertEqual(merged["geography_country"], "CN")

    def test_api_exporter_uses_published_v2_without_model_execution(self):
        from src import exporter

        rows, data = fixture()
        with sandbox(rows, data) as (_, run):
            runtime.publish_run(run, apply=True)
            dbrows = [
                dict(row, category="other", country="RU", subscribers=0) for row in rows
            ]
            with (
                patch.object(exporter, "load_results", return_value={}),
                patch.object(exporter, "load_v10_results", return_value={}),
            ):
                exported = exporter.channel_rows(dbrows)
            self.assertEqual(
                [r["topic_granularity"] for r in exported],
                ["topic", "group", "abstain"],
            )
            self.assertTrue(
                all(r["classification_engine"] == "semantic-v11v2" for r in exported)
            )

    def test_loading_does_not_import_ml_packages(self):
        script = "from src.classification_runtime import load_results; import sys; load_results(); assert not {'torch','numpy','sklearn'} & set(sys.modules)"
        subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )

    def test_browser_reads_same_normalized_group_and_abstention(self):
        rows, data = fixture()
        public = [
            runtime.public_result(data["channels"][r["username"]], data) | r
            for r in rows
        ]
        script = """
const fs=require('fs'),vm=require('vm');const c={window:{},localStorage:{getItem:()=>null},console};vm.createContext(c);
for(const name of ['taxonomy','topics','classification-v11-taxonomy','classification-v11-runtime'])vm.runInContext(fs.readFileSync('web/'+name+'.js','utf8'),c);
const rows=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(rows.map(r=>c.window.Topics.enrich(r))));
"""
        import shutil

        node = shutil.which("node") or "C:/ignatovia/NodeJs/node.exe"
        if not Path(node).exists() and not shutil.which(node):
            self.skipTest("Node unavailable")
        result = subprocess.run(
            [node, "-e", script],
            input=json.dumps(public, ensure_ascii=False),
            encoding="utf-8",
            text=True,
            capture_output=True,
            cwd=ROOT,
            check=True,
        )
        enriched = json.loads(result.stdout)
        for original, browser in zip(public, enriched):
            for key in (
                "subcategory",
                "topic_group",
                "topic_granularity",
                "topic_tags",
                "classification_engine",
                "classification_version",
                "geography_scope",
            ):
                self.assertEqual(original[key], browser[key], key)
