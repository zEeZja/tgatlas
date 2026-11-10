import json
import subprocess
import unittest
from pathlib import Path
from src.geography import resolve_geography
from src.topic_keywords import resolve_topic

CASES = [
    ({'region':'Москва','country':'RU'}, 'region'),
    ({'regions':['Москва','Татарстан','Москва'],'country':'RU'}, 'multi_region'),
    ({'channel_type':'federal','country':'RU'}, 'country'),
    ({'country':'RU'}, 'unknown'),
    ({'region_candidates':[{'region':'Москва'}]}, 'unknown'),
    ({'region':'Москва','region_review_required':True}, 'unknown'),
    ({'geographic_scope':'global','region':'Москва'}, 'global'),
    ({'channel_type':'federal'}, 'unknown'),
]
class GeographyTests(unittest.TestCase):
    def test_scopes_and_topic_independence(self):
        for data, expected in CASES:
            with self.subTest(data=data):
                result=resolve_topic(dict(data,name='Политика',subcategory='general'),{})
                self.assertEqual(result['primary_category'],'politics')
                self.assertEqual(result['geography_scope'],expected)
        self.assertEqual(resolve_geography(CASES[1][0])['geography_regions'],['Москва','Татарстан'])

    def test_manual_coverage(self):
        result=resolve_geography({'username':'test','region':'Москва'}, {'test':{'scope':'global'}})
        self.assertEqual(result['geography_scope'],'global')
        self.assertEqual(result['geography_regions'],[])
        result=resolve_geography({'username':'test','region_review_required':True}, {'test':{'scope':'multi_region','regions':['Москва','Татарстан']}})
        self.assertEqual(result['geography_scope'],'multi_region')

    def test_js_matches_backend(self):
        root=Path(__file__).resolve().parent.parent
        script="""
        const fs=require('fs'),vm=require('vm');const c={window:{},localStorage:{getItem:()=>null}};vm.createContext(c);
        for(const n of ['taxonomy','topics'])vm.runInContext(fs.readFileSync('web/'+n+'.js','utf8'),c);
        const rows=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(rows.map(c.window.Topics.geography)));
        """
        rows=[c for c,_ in CASES]
        js=json.loads(subprocess.check_output(['node','-e',script],input=json.dumps(rows),cwd=root,text=True))
        self.assertEqual(js,[resolve_geography(c) for c in rows])
