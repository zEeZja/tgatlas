"""Offline classification audit: exported public metadata in, Excel-friendly CSV out."""
import csv
import json
from pathlib import Path
from .topic_keywords import resolve_topic, TOPIC_LABELS


def run(input_path, report_path):
    source=json.loads(Path(input_path).read_text(encoding='utf-8-sig'))
    channels=source.get('channels') if isinstance(source,dict) else source
    if not isinstance(channels,list):
        raise ValueError('Ожидается channels.json с массивом channels')
    report=Path(report_path)
    if report.resolve()==Path(input_path).resolve():
        raise ValueError('Отчёт не должен заменять исходную выгрузку')
    report.parent.mkdir(parents=True,exist_ok=True)
    fields=['username','name','description','old_topic','new_topic','old_region','new_regions','country','scope','reason','evidence']
    counts=dict(total=len(channels),topic_unknown=0,geography_unknown=0,review_rows=0)
    with report.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fields,delimiter=';');writer.writeheader()
        for channel in channels:
            result=resolve_topic(channel)
            reasons=[]
            if result['subcategory']=='other':
                counts['topic_unknown']+=1;reasons.append('Недостаточно признаков темы')
            elif result['topic_source']=='fallback' and result['subcategory']=='general':
                reasons.append('Общая рубрика каталога без уточнения темы')
            elif result['topic_review_required']:
                reasons.append('Неоднозначная тема')
            if result['geography_scope']=='unknown':
                counts['geography_unknown']+=1;reasons.append('География не уточнена')
            if channel.get('subcategory') != result['subcategory'] or (channel.get('region') or '') != ', '.join(result['geography_regions']):
                reasons.append('Классификация изменена новыми правилами')
            if not reasons:continue
            counts['review_rows']+=1
            writer.writerow(dict(username=channel.get('username'),name=channel.get('name'),description=channel.get('description') or '',old_topic=channel.get('subcategory'),new_topic=TOPIC_LABELS[result['subcategory']],old_region=channel.get('region'),new_regions=', '.join(result['geography_regions']),country=result['geography_country'],scope=result['geography_scope'],reason='; '.join(reasons),evidence='; '.join(result['topic_evidence'])))
    print(f"Каналов: {counts['total']}; тема не определена: {counts['topic_unknown']}; география не уточнена: {counts['geography_unknown']}; строк отчёта: {counts['review_rows']}")
    print(f'Отчёт: {report}')
    return counts
