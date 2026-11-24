/* Self-contained local viewer: no fetch, model execution or catalog writes. */
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("view-data").textContent);
  const engine = data.engine_label || "V10";
  const reviewFormat = engine === "11v2" ? "atlas-v11v2-view-reviews-v1" : engine === "V11" ? "atlas-v11-view-reviews-v1" : "atlas-v10-view-reviews-v1";
  const el = (id) => document.getElementById(id);
  el("viewer-title").textContent = "Как классифицирует " + engine;
  el("predicted-label").textContent = "Предложение " + engine;
  document.title = engine + " — просмотр классификации";
  for (const option of el("mode").options) option.textContent = option.textContent.replaceAll("V10",engine);
  const labels = Object.fromEntries(data.topics.map((t) => [t.id, t.label]));
  const key = (engine === "11v2" ? "atlas-v11v2-view:" : "atlas-v10-view:") + data.fingerprint;
  const usernames = new Set(data.rows.map((r) => r.username));
  const reasons = {
    insufficient_calibration: "Недостаточно данных для надёжного автоматического назначения",
    definition_only_or_rare_class: "Для этой темы мало подтверждённых примеров",
    uncertain_semantic_match: "Неоднозначное смысловое соответствие",
    insufficient_content: "Недостаточно содержимого профиля",
    mixed_post_topics: "Публикации относятся к разным темам",
    reviewed_profile_changed: "Профиль изменился после ручной проверки",
    not_independently_calibrated: "Точность новой сетки пока не проверена независимо",
    insufficient_posts: "Нет трёх подходящих свежих публикаций",
    narrow_post_time_window: "Публикации охватывают узкий период",
    profile_post_disagreement: "Направленность профиля и темы публикаций расходятся",
    profile_identity_not_supported_by_posts: "Явная тема описания не подтверждается устойчивым содержанием постов",
    insufficient_informative_posts: "Меньше трёх содержательных неповторяющихся публикаций",
    ambiguous_topic_keep_broad_or_abstain: "Узкая тема неоднозначна: оставлен раздел или отказ от выбора",
    broad_editorial_identity_keep_group: "Деловая редакционная повестка: оставлен раздел без навязывания предпринимательства",
    supported_professional_group_keep_group: "Профессиональная направленность подтверждается: сохранён раздел без спорной узкой темы",
  };
  let reviews = {}, page = 0;
  const size = 25;
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function validReview(username, value) {
    return usernames.has(username) && value && typeof value === "object" &&
      ["", "correct", "wrong", "unsure"].includes(value.verdict) &&
      typeof value.topic === "string" && (!value.topic || Object.hasOwn(labels, value.topic)) &&
      typeof value.note === "string" && value.note.length <= 4000 &&
      (engine !== "11v2" ||
        ["", "correct", "wrong", "unsure"].includes(value.geography_verdict || "") &&
        ["", "unknown", "nonlocal", "global", "country", "region", "multi_region"].includes(value.geography_scope || "") &&
        (value.geography_note === undefined || typeof value.geography_note === "string" && value.geography_note.length <= 4000));
  }
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "{}");
    reviews = Object.fromEntries(Object.entries(saved).filter(([u, r]) => validReview(u, r)));
  } catch { el("status").textContent = "Браузер не разрешил восстановить оценки. Используйте скачивание JSON."; }
  const sortedTopics = [...data.topics].sort((a, b) => a.label.localeCompare(b.label, "ru"));
  function topicOptions(select, blank) {
    select.append(new Option(blank, ""));
    sortedTopics.forEach((t) => select.append(new Option(t.label, t.id)));
  }
  ["current", "predicted"].forEach((id) => {
    sortedTopics.forEach((t) => el(id).append(new Option(t.label, t.id)));
  });
  function statistics() {
    const changed = data.rows.filter((r) => r.current !== r.predicted).length;
    const rated = Object.values(reviews).filter((r) => r.verdict).length;
    const values = [`Каналов: ${data.rows.length}`, `Расхождений: ${changed}`, `Совпадений: ${data.rows.length - changed}`, `Моих оценок: ${rated}`];
    el("stats").replaceChildren(...values.map((text) => node("div", text, "stat")));
  }
  function save(username, review) {
    reviews[username] = review;
    try { localStorage.setItem(key, JSON.stringify(reviews)); el("status").textContent = "Оценка сохранена в браузере. Скачайте JSON перед переносом файла или сменой браузера."; }
    catch { el("status").textContent = "Не удалось сохранить в браузере. Скачайте JSON, иначе оценки потеряются при закрытии."; }
    statistics();
  }
  function card(row) {
    const article = node("article", undefined, "card" + (row.current !== row.predicted ? " changed" : ""));
    article.dataset.username = row.username;
    article.append(node("h2", row.name));
    const link = node("a", "@" + row.username);
    link.href = "https://t.me/" + encodeURIComponent(row.username);
    link.target = "_blank"; link.rel = "noopener noreferrer";
    article.append(link, node("p", row.description || "Описание отсутствует", "description"));
    const comparison = node("div", undefined, "comparison");
    for (const [caption, topic, css] of [["Сейчас на карте", row.current, "topic"], ["Предложение "+engine, row.predicted, "topic predicted"]]) {
      const box = node("div", caption, css); box.append(node("strong", labels[topic] || topic)); comparison.append(box);
    }
    article.append(comparison);
    if (row.geography_proposal) {
      const names = data.country_labels || {};
      const scopes = {unknown:"Не определено",nonlocal:"Без территориальной привязки",global:"Международная направленность",country:"Страна",region:"Регион",multi_region:"Несколько регионов"};
      const caption = (g) => [scopes[g.geography_scope] || "Не определено",names[g.geography_country] || g.geography_country,...(g.geography_regions||[])].filter(Boolean).join(" · ");
      article.append(node("h3","Территориальная направленность (не география аудитории)"));
      article.append(node("p","Сейчас: "+caption(row.geography_current)));
      article.append(node("p","Предложение: "+caption(row.geography_proposal)));
      article.append(node("p",row.geography_proposal.geography_explanation,"muted"));
      const items = (row.geography_proposal.geography_candidates||[]).map((c)=>typeof c==="string"?c:`${c.label}: упоминаний в постах ${c.post_support||0}`);
      if(items.length)article.append(node("p","Кандидаты: "+items.join(" · "),"reasons"));
    }
    article.append(node("small", "Источник текущей темы: " + ({manual: "ручная разметка", heuristic: "правила", model: "модель"}[row.current_source] || row.current_source)));
    article.append(node("p", row.review_required ? "Требуется проверка" : "Проверка моделью не запрошена", "reasons"));
    if (row.reasons.length) article.append(node("p", row.reasons.map((r) => reasons[r] || r).join(" · "), "reasons"));
    article.append(node("p", "Лучшие кандидаты (оценка модели, не вероятность):", "muted"));
    const candidates = node("ol", undefined, "candidates");
    row.candidates.forEach((c, i) => candidates.append(node("li", `${i + 1}. ${labels[c.id]} · ${c.score.toFixed(3)}`)));
    article.append(candidates);
    const details = node("details");
    const posts = row.evidence.filter((e) => e.source === "post");
    details.append(node("summary", `Что видела модель · публикаций: ${posts.length}`));
    row.evidence.forEach((e) => details.append(node("p", (e.source === "post" ? "Публикация: " : "Профиль: ") + (e.text || ""), "evidence")));
    if (!posts.length) details.append(node("p", "В этом прогоне использованы только название и описание. Telegram по ссылке можно проверить самостоятельно.", "muted"));
    article.append(details);
    if (row.diagnostics) {
      const diagnostics=node("details");
      diagnostics.append(node("summary","Почему 11v2 приняла такое решение"));
      diagnostics.append(node("p",`Вес профиля: ${row.diagnostics.profile_weight.toFixed(2)} · Явные предметные сигналы: ${(row.diagnostics.identity_topics||[]).map(id=>labels[id]||id).join(", ")||"нет"}`));
      diagnostics.append(node("p","Профиль: "+(row.diagnostics.profile_candidates||[]).map(id=>labels[id]||id).join(" · ")));
      diagnostics.append(node("p","Темы отдельных постов: "+(row.diagnostics.post_winners||[]).map(id=>labels[id]||id).join(" · ")));
      if(row.diagnostics.group_candidates)diagnostics.append(node("p","Разделы (отдельный анализ): "+row.diagnostics.group_candidates.map(g=>`${labels['group:'+g.id]||g.id}: ${g.score.toFixed(3)}`).join(" · ")));
      if(row.diagnostics.group_identity_evidence?.length)diagnostics.append(node("p","Подтверждение профессионального раздела: "+row.diagnostics.group_identity_evidence.map(id=>({corporate_finance:"корпоративные финансы, финансовые директора и учёт"}[id]||id)).join(" · ")));
      const excluded=row.routing?.excluded||[];
      if(excluded.length)diagnostics.append(node("p","Исключено из входа: "+excluded.map(p=>`${p.message_id||"без ID"}: ${{service_message:"служебное сообщение",low_information:"мало содержания",near_duplicate:"почти повтор"}[p.reason]||p.reason}`).join(" · ")));
      article.append(diagnostics);
    }
    const current = reviews[row.username] || {verdict: "", topic: "", note: ""};
    const review = node("div", undefined, "review");
    const verdict = node("select"); verdict.setAttribute("aria-label", "Моя оценка @" + row.username);
    [["", "Не оценено"], ["correct", "Согласен с "+engine], ["wrong", engine+" ошибся"], ["unsure", "Нужны публикации / не уверен"]].forEach(([v, t]) => verdict.append(new Option(t, v)));
    verdict.value = current.verdict;
    const topic = node("select"); topicOptions(topic, "Пока не указана"); topic.value = current.topic;
    topic.setAttribute("aria-label", "Правильная тема @" + row.username);
    const note = node("textarea"); note.value = current.note; note.maxLength = 4000;
    note.setAttribute("aria-label", "Комментарий @" + row.username);
    for (const [text, control] of [["Моя оценка", verdict], ["Правильная тема (если ошибся)", topic], ["Комментарий", note]]) {
      const label = node("label", text); label.append(control); review.append(label);
    }
    let geoVerdict,geoScope,geoNote;
    const update = () => save(row.username, {verdict: verdict.value, topic: topic.value, note: note.value,
      ...(geoVerdict?{geography_verdict:geoVerdict.value,geography_scope:geoScope.value,geography_note:geoNote.value}:{})});
    verdict.addEventListener("change", update); topic.addEventListener("change", update); note.addEventListener("input", update);
    article.append(review);
    if(engine === "11v2") {
      const geoReview=node("div",undefined,"review");
      geoVerdict=node("select");geoVerdict.setAttribute("aria-label","Оценка географии @"+row.username);
      for(const [value,text] of [["","Не оценено"],["correct","География верна"],["wrong","Ошибка географии"],["unsure","Нужно больше данных"]])geoVerdict.append(new Option(text,value));
      geoScope=node("select");geoScope.setAttribute("aria-label","Правильная направленность @"+row.username);
      for(const [value,text] of [["","Пока не указана"],["region","Регион России"],["multi_region","Несколько регионов"],["country","Страна"],["global","Международная"],["nonlocal","Без территориальной привязки"],["unknown","Не определено"]])geoScope.append(new Option(text,value));
      geoNote=node("textarea");geoNote.maxLength=4000;geoNote.setAttribute("aria-label","Комментарий к географии @"+row.username);
      geoNote.placeholder="При ошибке укажите страну/регион и основание";
      geoVerdict.value=current.geography_verdict||"";geoScope.value=current.geography_scope||"";geoNote.value=current.geography_note||"";
      for(const [text,control] of [["Моя оценка географии",geoVerdict],["Направленность (если неверно)",geoScope],["Страна, регион и пояснение",geoNote]]) {
        const label=node("label",text);label.append(control);geoReview.append(label);
      }
      geoVerdict.onchange=update;geoScope.onchange=update;geoNote.oninput=update;
      article.append(geoReview);
    }
    return article;
  }
  function render() {
    const query = el("search").value.toLocaleLowerCase("ru").trim().replace(/^@/, "");
    const mode = el("mode").value;
    const filtered = data.rows.filter((r) => {
      const verdict = reviews[r.username]?.verdict || "";
      return (!query || `${r.username} ${r.name} ${r.description}`.toLocaleLowerCase("ru").includes(query)) &&
        (!el("current").value || r.current === el("current").value) &&
        (!el("predicted").value || r.predicted === el("predicted").value) &&
        (mode === "all" || (mode === "changed" && r.current !== r.predicted) ||
          (mode === "same" && r.current === r.predicted) || (mode === "unreviewed" && !verdict) || mode === verdict);
    });
    const pages = Math.max(1, Math.ceil(filtered.length / size)); page = Math.min(page, pages - 1);
    el("page-info").textContent = `Найдено: ${filtered.length} · Страница ${page + 1} из ${pages}`;
    el("prev").disabled = page === 0; el("next").disabled = page >= pages - 1;
    el("cards").replaceChildren(...filtered.slice(page * size, (page + 1) * size).map(card));
    if (!filtered.length) el("cards").append(node("p", "По этим фильтрам каналов нет", "empty"));
    statistics();
  }
  ["search", "current", "predicted", "mode"].forEach((id) => el(id).addEventListener(id === "search" ? "input" : "change", () => { page = 0; render(); }));
  el("prev").onclick = () => { page--; render(); };
  el("next").onclick = () => { page++; render(); window.scrollTo(0, 0); };
  el("export").onclick = () => {
    const payload = {format: reviewFormat, fingerprint: data.fingerprint, model_sha256: data.model_sha256, exported_at: new Date().toISOString(), reviews};
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"}));
    const a = node("a"); a.href = url; a.download = engine.toLowerCase()+"-my-reviews.json"; a.click(); URL.revokeObjectURL(url);
    el("status").textContent = "Оценки выгружены в JSON. Рабочая карта не менялась.";
  };
  el("import").onchange = async () => {
    try {
      const file = el("import").files[0]; if (!file) return;
      if (file.size > 10000000) throw new Error("Слишком большой файл оценок");
      const payload = JSON.parse(await file.text());
      if (payload.format !== reviewFormat || payload.fingerprint !== data.fingerprint ||
          !payload.reviews || typeof payload.reviews !== "object" || Array.isArray(payload.reviews) ||
          !Object.entries(payload.reviews).every(([u, r]) => validReview(u, r))) throw new Error("Оценки относятся к другому снимку или повреждены");
      reviews = {...reviews, ...payload.reviews};
      try { localStorage.setItem(key, JSON.stringify(reviews)); el("status").textContent = "Оценки загружены. Совпадающие записи заменены записями из JSON."; }
      catch { el("status").textContent = "Оценки загружены только в память. Скачайте JSON перед закрытием."; }
      render();
    } catch (error) { el("status").textContent = "Не удалось загрузить: " + error.message; }
    el("import").value = "";
  };
  el("identity").textContent = `Снимок: ${data.fingerprint} · Модель: ${data.model_sha256 || "не указана"} · Профилей в исходнике: ${data.snapshot_channels}`;
  render();
})();
