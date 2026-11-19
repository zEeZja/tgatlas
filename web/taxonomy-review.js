(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("report-data").textContent);
  const el = (id) => document.getElementById(id);
  const taxonomy = data.taxonomy, analysis = data.analysis;
  const topics = {}, groups = {};
  taxonomy.groups.forEach((g) => {groups[g.id] = g; g.topics.forEach((t) => topics[t.id] = {...t, group:g.id}); el("group").append(new Option(g.label, g.id));});
  const rows = analysis?.channels || [], byUser = Object.fromEntries(rows.map((r) => [r.username, r]));
  let tab = "taxonomy", page = 0;
  const n = (tag, text, css) => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(css)e.className=css;return e;};
  const label = (id) => topics[id]?.label || "Не определена";
  const best = (r, kind="combined_candidates") => r[kind]?.[0]?.id;
  const matches = (text) => text.toLocaleLowerCase("ru").includes(el("search").value.toLocaleLowerCase("ru").trim().replace(/^@/, ""));
  function show(name) {
    tab=name; page=0;
    ["taxonomy","channels","clusters","migration"].forEach((id) => {el(id).hidden=id!==name;document.querySelector(`[data-tab="${id}"]`).setAttribute("aria-pressed",String(id===name));});
    el("mode").hidden=name!=="channels";
    render();
  }
  function example(username) {
    const b=n("button","@"+username,"example");b.onclick=()=>{el("search").value=username;el("group").value="";el("mode").value="all";show("channels");};return b;
  }
  function renderTaxonomy() {
    el("taxonomy").replaceChildren(); let count=0;
    taxonomy.groups.forEach((g) => {
      if(el("group").value && el("group").value!==g.id)return;
      const groupMatch=matches(g.label+" "+g.definition);
      const visible=g.topics.filter((t)=>groupMatch||matches(t.label+" "+t.definition+" "+t.boundary));
      if(!visible.length)return;count++;
      const card=n("article",undefined,"card");card.append(n("h2",g.label),n("p",g.definition));
      const grid=n("div",undefined,"topics");
      visible.forEach((t)=>{
        const box=n("div",undefined,"topic");box.append(n("h3",t.label),n("p",t.definition),n("p",t.boundary,"boundary"));
        const examples=rows.filter((r)=>best(r)===t.id);
        box.append(n("p",analysis?`Примеров-кандидатов с постами: ${examples.length}`:"Примеры появятся после анализа пилота","counts"));
        examples.slice(0,5).forEach((r)=>box.append(example(r.username)));grid.append(box);
      });card.append(grid);el("taxonomy").append(card);
    });el("status").textContent=`Разделов по фильтру: ${count}. Новые узкие ниши утверждаются после просмотра примеров.`;
  }
  function channelCard(row) {
    const card=n("article",undefined,"card");card.dataset.username=row.username;
    card.append(n("h2",row.name));const link=n("a","@"+row.username);link.href="https://t.me/"+encodeURIComponent(row.username);link.target="_blank";link.rel="noopener noreferrer";
    card.append(link,n("p",row.description,"description"));
    const compare=n("div",undefined,"compare");
    [["Текущая тема",data.old_labels[row.current]||row.current],["V10 (без постов)",data.old_labels[row.v10]||row.v10],["Новая сетка: только профиль",label(best(row,"profile_candidates"))],["Новая сетка: профиль + посты",label(best(row))]].forEach(([caption,value])=>{const box=n("div",caption);box.append(n("strong",value));compare.append(box);});
    card.append(compare);
    card.append(n("p",`Название и описание взяты из прежнего снимка${row.profile_metadata_at?" (метаданные: "+row.profile_metadata_at+")":""}. Посты собраны отдельно; расхождение может отражать и изменение канала со временем.`,"muted"));
    card.append(n("p",`Сохранено подходящих постов: ${row.posts.length}. ${row.fetched_at?"Снимок: "+row.fetched_at:"Снимка нет"}`,"muted"));
    if(row.status==="insufficient_posts")card.append(n("p","Недостаточно постов: содержательное предложение по новой сетке не сделано.","notice"));
    if(row.low_diversity)card.append(n("p","Узкое временное окно: публикации не представляют устойчивую историю канала.","notice"));
    if(row.post_candidates.length){card.append(n("p","Только посты: "+label(best(row,"post_candidates"))));
      const alternatives=n("p");row.combined_candidates.forEach((c)=>alternatives.append(n("span",`${label(c.id)} · ${c.score.toFixed(3)}`,"badge")));card.append(alternatives);}
    const details=n("details");details.append(n("summary",`Прочитать публикации (${row.posts.length})`));
    details.append(n("p","В анализ каждого поста переданы первые 1000 символов, encoder ограничен 256 токенами. Ниже сохранённые тексты (до 4000 символов), а не доказательства правильности предложения.","muted"));
    row.posts.forEach((p)=>{const article=n("div",undefined,"post");article.append(n("small",p.date||"Дата неизвестна"),n("p",p.text));if(Number.isSafeInteger(p.message_id)&&p.message_id>0){const a=n("a","Открыть публикацию");a.href=`https://t.me/${encodeURIComponent(row.username)}/${p.message_id}`;a.target="_blank";a.rel="noopener noreferrer";article.append(a);}details.append(article);});card.append(details);return card;
  }
  function renderChannels() {
    const mode=el("mode").value;
    const filtered=rows.filter((r)=>(!el("group").value||topics[best(r)]?.group===el("group").value)&&matches(`${r.username} ${r.name} ${r.description} ${r.posts.map((p)=>p.text).join(" ")}`)&&(mode==="all"||(mode==="posts"&&r.post_candidates.length)||(mode==="missing"&&!r.post_candidates.length)||(mode==="changed"&&r.post_candidates.length&&best(r,"profile_candidates")!==best(r,"post_candidates"))));
    const pages=Math.max(1,Math.ceil(filtered.length/15));page=Math.min(page,pages-1);
    el("pages").textContent=`${filtered.length} каналов · Страница ${page+1} из ${pages}`;el("prev").disabled=page===0;el("next").disabled=page===pages-1;
    el("channel-list").replaceChildren(...filtered.slice(page*15,(page+1)*15).map(channelCard));
    if(!filtered.length)el("channel-list").append(n("p",analysis?"По фильтру каналов нет":"Сбор или анализ пилота ещё не завершён","empty"));
    el("status").textContent="Сравнивайте профиль и посты внутри новой сетки: разница с V10 также связана с изменением самих тем, а не только с добавлением текстов.";
  }
  function renderClusters(){
    el("clusters").replaceChildren(n("p","Это группы близких по постам каналов, найденные без старых меток. Названия и границы ещё нужно обсудить. Близость не доказывает, что группа образует отдельную полезную нишу.","notice"));
    const clusters=(analysis?.clusters||[]).filter((c)=>(!el("group").value||c.nearest_draft_topics[0]?.group===el("group").value)&&matches(c.channels.map((u)=>u+" "+byUser[u]?.name).join(" ")+" "+c.nearest_draft_topics.map((t)=>label(t.id)).join(" ")));
    clusters.forEach((c)=>{const card=n("article",undefined,"card");card.append(n("h2",`Группа ${c.id+1} · каналов: ${c.channels.length}`),n("p","Ближайшие названия из черновика: "+c.nearest_draft_topics.slice(0,3).map((t)=>label(t.id)).join(", ")));
      const members=n("div",undefined,"cluster-members");c.channels.forEach((u)=>members.append(example(u)));card.append(members);el("clusters").append(card);});
    if(!clusters.length)el("clusters").append(n("p","Подходящих групп пока нет. Не создаём искусственные ниши ради заполнения сетки."));
    el("status").textContent=analysis?`Групп по фильтру: ${clusters.length}. Каналов вне плотных групп: ${analysis.noise_channels}. Это не ошибка классификации и не тема «Разное».`:"Группы появятся после анализа пилота.";
  }
  function renderMigration(){
    el("migration").replaceChildren(n("p","Это варианты интерпретации прежних тем, а не автоматические правила миграции. Ручные ответы сохранены в старой версии и требуют отдельного пересмотра при изменении смысла меток.","notice"));
    const table=n("table"), header=n("tr");["Старая тема","Кандидаты новой сетки"].forEach((text)=>header.append(n("th",text)));table.append(header);
    data.legacy_mapping.filter((r)=>matches(r.label+" "+(r.note||"")+" "+r.candidates.map((c)=>c.label).join(" "))&&(!el("group").value||r.candidates.some((c)=>c.group===el("group").value))).forEach((r)=>{const tr=n("tr");tr.append(n("td",r.label),n("td",(r.candidates.length?r.candidates.map((c)=>c.label+(c.topic?"":" (только раздел)")).join(" / "):"Не переносится как содержательная тема")+(r.note?". "+r.note:"")));table.append(tr);});el("migration").append(table);
    el("status").textContent="Тип автора (например, госорган), формат (например, учебный пост) и география больше не подменяют предмет содержания.";
  }
  function render(){if(tab==="taxonomy")renderTaxonomy();else if(tab==="channels")renderChannels();else if(tab==="clusters")renderClusters();else renderMigration();}
  document.querySelectorAll("[data-tab]").forEach((b)=>b.onclick=()=>show(b.dataset.tab));
  ["search","group","mode"].forEach((id)=>el(id).addEventListener(id==="search"?"input":"change",()=>{page=0;render();}));
  el("prev").onclick=()=>{page--;render();};el("next").onclick=()=>{page++;render();window.scrollTo(0,0);};
  const stats=[`Разделов: ${taxonomy.groups.length}`,`Подтем: ${Object.keys(topics).length}`,`Каналов пилота: ${rows.length}`,`С достаточными постами: ${analysis?.summary.with_posts||0}`];el("stats").replaceChildren(...stats.map((s)=>n("div",s,"stat")));
  el("identity").textContent=`Версия: ${taxonomy.version} · Сетка неактивна. ${analysis?"Вес профиля 25%, постов 75% — предварительная гипотеза, не калиброванная настройка. SHA-256 сетки: "+analysis.taxonomy_sha256:"Отчёт пока без анализа публикаций."}`;
  show("taxonomy");
})();
