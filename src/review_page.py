"""Self-contained owner review form: no network, explicit per-channel confirmation."""

import json
from .topic_keywords import TAXONOMY
from .classification_review import FIELDS


def render_review(records):
    payload = (
        json.dumps(
            dict(
                records=records,
                fields=FIELDS,
                topics=[dict(id=t["id"], label=t["label"]) for t in TAXONOMY["topics"]],
            ),
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return TEMPLATE.replace("/*__DATA__*/", payload)


TEMPLATE = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Проверка классификации TG Atlas</title>
<style>body{font:16px system-ui;margin:0;background:#f1f5f2;color:#18352b}main{max-width:920px;margin:auto;padding:24px}h1{font-size:26px}article{background:white;border:1px solid #cbd9cf;border-radius:16px;padding:24px}button,select,input,textarea{font:inherit;padding:10px;border:1px solid #a4b9aa;border-radius:7px;box-sizing:border-box}button{cursor:pointer;background:#d5eddf}button:disabled{opacity:.5}input,select,textarea{max-width:100%;width:100%}label{display:block;margin:14px 0 6px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}nav{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0}.tags{display:flex;gap:8px;flex-wrap:wrap}.tags label{margin:2px;padding:5px;border:1px solid #ddd;border-radius:6px;font-size:14px}.tags input,#reviewed{width:auto}#description{white-space:pre-wrap;line-height:1.5}small{color:#547263}#status{min-height:24px}a{color:#216a4c}@media(max-width:620px){.grid{grid-template-columns:1fr}main{padding:12px}article{padding:16px}}</style>
<main><h1>Проверка тем и географии</h1><p>Предложения заполнены автоматически. Исправьте основную тему, дополнительные теги и географию, затем отметьте «Проверено мной». Если данных недостаточно, оставьте неопределённую тему или географию.</p><p>Всё работает в вашем браузере. После проверки нажмите «Скачать разметку CSV» и передайте сохранённый файл для применения и обучения.</p>
<nav><button id="previous">← Назад</button><strong id="progress"></strong><button id="next">Далее →</button><button id="next-unreviewed">К непроверенному</button><button id="download">Скачать разметку CSV</button></nav>
<article><h2 id="name"></h2><a id="channel" target="_blank" rel="noopener noreferrer"></a><p id="description"></p><small>Служебные контакты и регистрационные строки убраны из описания.</small>
<div class="grid"><div><label for="topic">Основная тема</label><select id="topic"></select></div><div><label for="scope">Географический охват</label><select id="scope"><option value="region">Один регион / город</option><option value="multi_region">Несколько регионов</option><option value="country">Вся страна</option><option value="global">Международный охват</option><option value="unknown">Недостаточно данных</option></select></div></div>
<label>Дополнительные темы</label><div class="tags" id="tags"></div><div class="grid"><div><label for="regions">Регионы / города (несколько — через |)</label><input id="regions" placeholder="Москва | Московская область"></div><div><label for="country">Код страны (RU, RS, UZ…)</label><input id="country" maxlength="2"></div></div><label for="note">Примечание</label><textarea id="note" rows="2"></textarea><label><input id="reviewed" type="checkbox"> Проверено мной</label><p id="status" role="status"></p></article></main>
<script>const payload=/*__DATA__*/;
const rows=payload.records;let index=0;const storageKey='atlas-review-'+rows.map(r=>r.group_id).join('').slice(0,120);const el=id=>document.getElementById(id);
try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(Array.isArray(saved)&&saved.length===rows.length&&saved.every((r,i)=>r.username===rows[i].username&&r.group_id===rows[i].group_id))saved.forEach((r,i)=>{for(const key of payload.fields)if(key.startsWith('expected_')||key==='note'||key==='reviewed')rows[i][key]=r[key]??rows[i][key];});}catch(e){}
for(const topic of payload.topics){const opt=document.createElement('option');opt.value=topic.id;opt.textContent=topic.label;el('topic').appendChild(opt);}
function show(){const row=rows[index];if(!row){el('progress').textContent='Нет каналов';return;}el('name').textContent=row.name;el('channel').textContent='@'+row.username;el('channel').href='https://t.me/'+encodeURIComponent(row.username);el('description').textContent=row.description;el('topic').value=row.expected_topic;el('scope').value=row.expected_scope;el('regions').value=row.expected_regions;el('country').value=row.expected_country;el('note').value=row.note;el('reviewed').checked=row.reviewed==='yes';el('tags').replaceChildren();for(const topic of payload.topics.filter(t=>t.id!=='other'&&t.id!==row.expected_topic)){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=topic.id;input.checked=row.expected_secondary_topics.split(',').includes(topic.id);input.onchange=save;label.append(input,document.createTextNode(' '+topic.label));el('tags').appendChild(label);}el('progress').textContent=`${index+1} / ${rows.length} · проверено ${rows.filter(r=>r.reviewed==='yes').length}`;el('previous').disabled=index===0;el('next').disabled=index===rows.length-1;}
function save(){const row=rows[index];row.expected_topic=el('topic').value;row.expected_scope=el('scope').value;row.expected_regions=el('regions').value.trim();row.expected_country=el('country').value.trim().toUpperCase();row.expected_secondary_topics=[...el('tags').querySelectorAll('input:checked')].map(i=>i.value).filter(t=>t!==row.expected_topic).sort().join(',');row.note=el('note').value;row.reviewed=el('reviewed').checked?'yes':'no';if(['country','global','unknown'].includes(row.expected_scope))row.expected_regions='';try{localStorage.setItem(storageKey,JSON.stringify(rows));el('status').textContent='Изменения сохранены в этом браузере. Для передачи скачайте CSV.';}catch(e){el('status').textContent='Скачайте CSV: сохранение в браузере недоступно.';}show();}
for(const id of ['topic','scope','regions','country','note','reviewed'])el(id).onchange=save;
el('previous').onclick=()=>{if(index>0){index--;show();}};el('next').onclick=()=>{if(index<rows.length-1){index++;show();}};el('next-unreviewed').onclick=()=>{for(let n=1;n<=rows.length;n++){const next=(index+n)%rows.length;if(rows[next].reviewed!=='yes'){index=next;show();return;}}};
function cell(value){let s=String(value??'');if(/^\\s*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
el('download').onclick=()=>{save();const content='\\ufeff'+[payload.fields.map(cell).join(';'),...rows.map(row=>payload.fields.map(k=>cell(row[k])).join(';'))].join('\\r\\n');const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='classification-reviewed.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};show();
</script></html>"""
