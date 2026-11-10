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
CASES += [
    ({'name':'Новости Перми','region_review_required':True},'region'),
    ({'name':'Читаем книги'},'unknown'),
    ({'name':'Москва и Санкт-Петербург'},'unknown'),
    ({'name':'Нижний Новгород'},'region'),
]
CASES += [
    ({'name':'Подслушано Химки','region_review_required':True},'region'),
    ({'name':'Pushkino_official | Пушкино'},'region'),
    ({'name':'Zelenograd.ru — Новости'},'region'),
    ({'name':'Развлечения в Ташкенте'},'region'),
    ({'name':'Карьера в Португалии','country':'RU'},'country'),
    ({'name':'Москва и Ташкент'},'unknown'),
    ({'name':'Новости Москвы','username':'rian_ru','country':'RU'},'country'),
]
class GeographyTests(unittest.TestCase):
    def test_scopes_and_topic_independence(self):
        for data, expected in CASES:
            with self.subTest(data=data):
                result=resolve_topic(dict(data,name=data.get('name','Политика'),subcategory='politics'),{})
                self.assertEqual(result['primary_category'],'politics')
                self.assertEqual(result['geography_scope'],expected)
        self.assertEqual(resolve_geography(CASES[1][0])['geography_regions'],['Москва','Татарстан'])
        self.assertEqual(resolve_geography({'name':'Подслушано Химки'})['geography_regions'],['Московская область'])
        self.assertEqual(resolve_geography({'name':'Развлечения в Ташкенте'})['geography_country'],'UZ')
        self.assertEqual(resolve_geography({'name':'Карьера в Португалии','country':'RU'})['geography_country'],'PT')

    def test_clear_titles_recover_stale_unknown_geography(self):
        examples={'Новости Перми':'Пермский край','Омск сегодня':'Омская область','Новости Твери':'Тверская область','Сочи сейчас':'Краснодарский край','Чита новости':'Забайкальский край'}
        for name,region in examples.items():
            result=resolve_geography({'name':name,'region_review_required':True})
            self.assertEqual(result['geography_regions'],[region])
        for name in ['Читаем книги','Москва и Санкт-Петербург']:
            self.assertEqual(resolve_geography({'name':name})['geography_scope'],'unknown')
        self.assertEqual(resolve_geography({'username':'rian_ru','name':'Новости Москвы','channel_type':'federal','country':'RU'})['geography_scope'],'country')

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
