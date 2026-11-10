import json
import shutil
import subprocess
import unittest
from pathlib import Path

from src.topic_keywords import classify_topic, resolve_topic
from src.exporter import _channel_to_row
from scripts.sync_topics import render

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = json.loads((ROOT / 'tests/fixtures/topics.json').read_text(encoding='utf-8'))

class TopicTests(unittest.TestCase):
    def test_obvious_topics_and_false_positives(self):
        for case in FIXTURES:
            with self.subTest(case=case['name']):
                overrides = {case['username']:case['override']} if 'override' in case else {}
                result = resolve_topic(case, overrides)
                self.assertEqual(result['subcategory'], case['expected'])

    def test_ambiguous_results_are_explained(self):
        result = classify_topic('Инвестиции в новостройки')
        self.assertTrue(result['review_required'])
        self.assertGreater(len(result['candidates']), 1)
        self.assertTrue(result['evidence'])

    def test_invalid_manual_topic_is_reported(self):
        with self.assertRaises(ValueError):
            resolve_topic({'username':'test'}, {'test':'invalid'})

    def test_export_enriches_without_modifying_input_or_region(self):
        row = dict(username='test',name='Политические новости',description='Парламент и выборы',
                   category='news',subcategory='general',country='RU',region='Москва',subscribers=100)
        result = _channel_to_row(row)
        self.assertEqual(result['subcategory'], 'politics')
        self.assertEqual(result['topic_category'], 'society')
        self.assertEqual(result['region'], 'Москва')
        self.assertEqual(row['subcategory'], 'general')
        self.assertEqual(result['original_subcategory'], 'general')
        self.assertEqual(result['topic_source'], 'heuristic')

    def test_browser_taxonomy_is_in_sync(self):
        self.assertEqual((ROOT/'web/taxonomy.js').read_text(encoding='utf-8'), render())

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for Python/browser parity')
    def test_browser_and_python_agree(self):
        script = """
        const fs=require('fs'),vm=require('vm');
        const c={window:{},localStorage:{getItem:()=>null}};vm.createContext(c);
        for(const f of ['taxonomy','topics'])vm.runInContext(fs.readFileSync('web/'+f+'.js','utf8'),c);
        const rows=JSON.parse(fs.readFileSync('tests/fixtures/topics.json','utf8'));
        console.log(JSON.stringify(rows.map(row=>{
          if(row.override)c.window.TG_TAXONOMY.overrides[row.username]=row.override;
          return c.window.Topics.enrich(row);
        })));
        """
        results = json.loads(subprocess.check_output(['node','-e',script],cwd=ROOT,text=True))
        for case, browser in zip(FIXTURES, results):
            overrides = {case['username']:case['override']} if 'override' in case else {}
            python = resolve_topic(case, overrides)
            for key in python:
                self.assertEqual(browser[key],python[key],(case['name'],key))

if __name__ == '__main__':
    unittest.main()
