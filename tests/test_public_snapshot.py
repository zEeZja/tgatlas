"""Публичный снимок не меняет рабочую карту и не содержит тексты постов."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.atlas_projection import make_artifact
from src.layout_policy import geography_role, load_policy
from src.public_snapshot import prepare, public_row, publish


class PublicSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [dict(id=str(i), username=f'channel_{i}', name='Новости',
                          subcategory='v11_general_news', topic_group='v11_news_society',
                          geography_scope='unknown', description='Профиль',
                          topic_evidence=[{'text': 'PRIVATE_POST'}],
                          topic_diagnostics={'secret': 'PRIVATE_POST'},
                          sources=['PRIVATE_POST'],
                          post_routing={'usable': 1, 'displayed_posts': ['PRIVATE_POST']})
                     for i in range(3)]
        policy = load_policy()
        self.artifact = make_artifact(self.rows,
            [geography_role(r, policy) for r in self.rows],
            np.array([[120., 120.], [180., 180.], [240., 240.]]), policy, 'tsne', {})
        self.channels = self.root/'channels.json'
        self.layout = self.root/'layout.json'
        self.channels.write_text(json.dumps({'channels': self.rows}), encoding='utf-8')
        self.write_layout()

    def write_layout(self):
        self.layout.write_text(json.dumps(self.artifact), encoding='utf-8')

    def test_scrub_and_exact_geometry(self):
        original = copy.deepcopy(self.rows[0])
        public_row(self.rows[0])
        self.assertEqual(self.rows[0], original)
        files, manifest = prepare(self.channels, self.layout)
        self.assertNotIn('PRIVATE_POST', ''.join(files.values()))
        self.assertNotIn('displayed_posts', files['channels.json'])
        self.assertEqual(json.loads(files['layout.json'])['layout'], self.artifact['layout'])
        self.assertEqual(json.loads(files['layout.json'])['bindings'], self.artifact['bindings'])
        for name, info in manifest['files'].items():
            self.assertEqual(info['sha256'], hashlib.sha256(files[name].encode()).hexdigest())

    def test_dry_run_and_apply_preserve_sources(self):
        before = [p.read_bytes() for p in (self.channels, self.layout)]
        output = self.root/'public'
        publish(self.channels, self.layout, output)
        self.assertFalse(output.exists())
        publish(self.channels, self.layout, output, apply=True)
        self.assertTrue((output/'manifest.json').exists())
        self.assertEqual(before, [p.read_bytes() for p in (self.channels, self.layout)])
        with self.assertRaises(ValueError):
            publish(self.channels, self.layout, self.root, apply=True)

    def test_reject_stale_mismatch_and_corrupt_geometry(self):
        original = copy.deepcopy(self.artifact)
        for damage in ('date', 'binding', 'geometry'):
            self.artifact = copy.deepcopy(original)
            if damage == 'date':
                self.artifact['created_at'] = (datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
            elif damage == 'binding':
                self.artifact['bindings']['0'] = 'bad'
            else:
                self.artifact['layout']['nodes'][0]['x'] = float('nan')
            self.write_layout()
            with self.assertRaises(ValueError):
                prepare(self.channels, self.layout)
