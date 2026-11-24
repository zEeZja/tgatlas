(async function () {
  const SUB_CATEGORIES = window.Topics.taxonomy.topics;
  const SUB_MAP = window.Topics.byId;
  const GROUP_MAP = window.Topics.groupsById;
  const CATEGORY_LABELS = Object.fromEntries(
    window.Topics.taxonomy.sections.map((s) => [s.id, s.label]),
  );
  const COUNTRY_LABELS = {
    ...window.TG_CLASSIFICATION_V11?.country_labels,
    PT: "Португалия",
    RS: "Сербия",
    ID: "Индонезия",
    UZ: "Узбекистан",
    RU: "Россия",
    UA: "Украина",
    BY: "Беларусь",
    KZ: "Казахстан",
    US: "США",
    GB: "Великобритания",
    DE: "Германия",
    FR: "Франция",
    IL: "Израиль",
    AE: "ОАЭ",
    TR: "Турция",
    LV: "Латвия",
    NL: "Нидерланды",
    CZ: "Чехия",
  };

  const el = {
    search: document.getElementById("search"),
    clear: document.getElementById("clear"),
    chips: document.getElementById("chips"),
    regionBlock: document.getElementById("region-block"),
    region: document.getElementById("region"),
    country: document.getElementById("country"),
    list: document.getElementById("list"),
    count: document.getElementById("count"),
    banner: document.getElementById("banner"),
    wrap: document.getElementById("map-wrap"),
    canvas: document.getElementById("map"),
    detail: document.getElementById("detail"),
  };

  const state = {
    channels: [],
    visible: [],
    listLimit: 80,
    sort: "popular",
    group: "topic",
    category: "",
    publicationFormat: "",
    includeSecondary: false,
    geoScope: "",
    query: "",
    subcategory: "",
    region: "",
    country: "",
    selectedId: null,
  };

  const territoryFilters = document.getElementById("territory-filters");
  territoryFilters.open = true;
  let activePanel = null;
  const navigationButtons=[...document.querySelectorAll('.header-actions button')];
  navigationButtons.forEach(button=>button.disabled=true);
  function closePanel(returnFocus=true) {
    const previous=activePanel;
    for(const id of ['search','filters']) {
      document.getElementById(id==='search'?'search-panel':'filter-panel').hidden=true;
      document.getElementById(id+'-toggle').setAttribute('aria-expanded','false');
    }
    activePanel=null;
    document.body.classList.remove('drawer-open');
    if(returnFocus&&previous)document.getElementById(previous+'-toggle').focus();
  }
  function openPanel(id) {
    if(activePanel===id){closePanel();return;}
    closePanel(false);
    if(!el.detail.hidden)closeDetail(false);
    activePanel=id;
    document.getElementById(id==='search'?'search-panel':'filter-panel').hidden=false;
    document.getElementById(id+'-toggle').setAttribute('aria-expanded','true');
    document.body.classList.add('drawer-open');
    document.getElementById('help').hidden=true;
    document.getElementById('help-toggle').setAttribute('aria-expanded','false');
    if(id==='search'){renderList();el.search.focus();}else document.getElementById('category').focus();
  }
  document.getElementById('search-toggle').onclick=()=>openPanel('search');
  document.getElementById('filters-toggle').onclick=()=>openPanel('filters');
  document.querySelectorAll('[data-close-panel]').forEach(button=>button.onclick=()=>closePanel());
  document.getElementById('apply-filters').onclick=()=>{closePanel();map.fitVisible();};

  let channels;
  const catalogChannels = new Map();
  try {
    channels = (await window.Api.getChannels()).map((ch) => {
      const enriched=window.Topics.enrich(ch);
      catalogChannels.set(enriched.id,{...ch});
      return {...enriched,layout_source_binding:window.Layout.rowBinding(ch)};
    });
  } catch (e) {
    showFatal(e.message);
    return;
  }
  state.channels = channels;

  if (
    window.Api.CONFIG.mode === "static" &&
    window.TG_DATA &&
    window.TG_DATA._sample
  ) {
    el.banner.hidden = false;
    el.banner.innerHTML =
      "Демо · каналы из конфигурации проекта. Аудитория и актуальность не проверены.";
  }
  if (channels.some((ch)=>["semantic-v11","semantic-v11v2"].includes(ch.classification_engine))) {
    el.banner.hidden=false;
    const engine=channels.some(ch=>ch.classification_engine==="semantic-v11v2")?"11v2":"V11";
    el.banner.textContent=engine+" · Темы и территории предложены моделью и требуют проверки. Ручные метки в приоритете.";
  }

  function colorOf(ch) {
    return (
      GROUP_MAP[ch.topic_group]?.color ||
      (SUB_MAP[ch.subcategory] || SUB_MAP.other).color
    );
  }
  const layoutArtifact = await window.Api.getLayout();
  const isDemo=Boolean(window.TG_DATA?._sample);
  if(isDemo)document.getElementById('mode-description').textContent='Демо-каталог · аудитория и актуальность не проверены';
  const layoutCache=new Map();
  let categoryCounts=null;
  function createLayout() {
    if(!layoutCache.has(state.group))layoutCache.set(state.group,state.group==='topic'
      ?window.AtlasView.topicView(state.channels,layoutArtifact,catalogChannels)
      :window.AtlasView.geographicView(state.channels));
    const diagnosis=layoutCache.get(state.group);
    const cached=diagnosis.layout;
    const title=document.getElementById("layout-method");
    title.dataset.layoutReason=diagnosis.code;
    title.title=diagnosis.reason;
    if(cached.method)title.textContent=`${cached.method.toUpperCase()} · с постами ${cached.evidence?.with_posts||0} из ${channels.length}`+(diagnosis.code==='local_overlay'?' · '+diagnosis.reason:'');
    else title.textContent=diagnosis.reason||'Тематическая схема; близость точек не является измеренным сходством';
    return cached;
  }
  function topicLabel(ch) {
    if(ch.classification_engine==="semantic-v11v2"&&ch.topic_granularity==="abstain")return "Тема не определена";
    if(ch.topic_granularity==="group")return (GROUP_MAP[ch.topic_group]?.label||"Раздел")+" · без уточнения подтемы";
    return (SUB_MAP[ch.subcategory]||SUB_MAP.other).label;
  }
  let layout = createLayout();
  const channelsById = new Map(channels.map((ch) => [ch.id, ch]));
  for (const node of layout.nodes) {
    node.color = colorOf(channelsById.get(node.id) || node);
  }

  const map = new window.CanvasMap(el.canvas, el.wrap, {
    onSelect: (id) => selectChannel(id),
  });
  map.setLayout(layout);
  document.getElementById("zoom-in").onclick = () => map.zoomBy(1.35);
  document.getElementById("zoom-out").onclick = () => map.zoomBy(1 / 1.35);
  document.getElementById("fit-map").onclick = () => map.fitVisible();
  const helpToggle = document.getElementById("help-toggle");
  helpToggle.onclick = () => {
    closePanel(false);
    if(!el.detail.hidden)closeDetail(false);
    const help = document.getElementById("help");
    help.hidden = !help.hidden;
    helpToggle.setAttribute("aria-expanded", String(!help.hidden));
  };
  document.getElementById("sort").onchange = (e) => {
    state.sort = e.target.value;
    refresh();
  };
  document.addEventListener("keydown", (e) => {
    if (
      e.key === "/" &&
      !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)
    ) {
      e.preventDefault();
      openPanel('search');
    }
    if (e.key === "Escape") {
      closePanel();
      closeDetail();
      document.getElementById("help").hidden = true;
      helpToggle.setAttribute("aria-expanded", "false");
    }
  });

  const statsBar = document.createElement("div");
  statsBar.className = "stats-bar";
  function renderStats() {
    const rows = state.channels;
    const freshness = window.Freshness.counts(rows);
    statsBar.innerHTML =
      `<span><b>${rows.length}</b> каналов · <b>${new Set(rows.map((c) => c.subcategory)).size}</b> тем · <b>${rows.filter((c) => c.topic_review_required).length}</b> для проверки темы</span>` +
      `<span class="freshness-summary" title="Свежесть названия, описания и аудитории, не дата экспорта">Данные: ${freshness.fresh} свежих · ${freshness.stale} старше 24 ч · ${freshness.unknown} без проверки</span>`;
  }
  renderStats();
  document.getElementById("catalog-stats").appendChild(statsBar);

  const resetBtn = document.createElement("button");
  resetBtn.className = "reset-btn";
  resetBtn.textContent = "Сбросить фильтры";
  resetBtn.hidden = true;
  resetBtn.addEventListener("click", () => {
    categoryCounts=null;
    state.query = "";
    state.publicationFormat = "";
    state.includeSecondary = false;
    document.getElementById("publication-format").value = "";
    document.getElementById("include-secondary").checked = false;
    state.subcategory = "";
    state.category = "";
    state.geoScope = "";
    document.getElementById("category").value = "";
    state.region = "";
    state.country = "";
    el.search.value = "";
    el.region.value = "";
    el.country.value = "";
    el.clear.hidden = true;
    renderRegions();
    renderChips();
    refresh();
    map.fit();
  });
  document.getElementById("reset-slot").appendChild(resetBtn);

  const categoryEl = document.getElementById("category");
  const priority = window.Topics.taxonomy.priority_groups || [];
  for (const group of [...window.Topics.taxonomy.groups].sort((a, b) => {
    const rank = (id) =>
      priority.includes(id) ? priority.indexOf(id) : priority.length;
    return rank(a.id) - rank(b.id);
  })) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group.label;
    const all = document.createElement("option");
    all.value = "group:" + group.id;
    all.textContent = group.label + " · все темы";
    optgroup.appendChild(all);
    for (const topic of SUB_CATEGORIES.filter((t) => t.group === group.id)) {
      const option = document.createElement("option");
      option.value = topic.id;
      option.textContent = topic.label;
      optgroup.appendChild(option);
    }
    categoryEl.appendChild(optgroup);
  }
  const formatLabel = document.createElement("label");
  formatLabel.className = "filter-label";
  formatLabel.textContent = "Формат публикаций";
  formatLabel.htmlFor = "publication-format";
  const formatSelect = document.createElement("select");
  formatSelect.id = "publication-format";
  for (const fmt of [
    { id: "", label: "Любой формат" },
    ...window.Topics.taxonomy.formats,
  ]) {
    const option = document.createElement("option");
    option.value = fmt.id;
    option.textContent = fmt.label;
    formatSelect.appendChild(option);
  }
  const advancedControls = document.getElementById("advanced-controls");
  advancedControls.append(formatLabel, formatSelect);
  const secondaryLabel = document.createElement("label");
  secondaryLabel.className = "secondary-toggle";
  const secondaryInput = document.createElement("input");
  secondaryInput.type = "checkbox";
  secondaryInput.id = "include-secondary";
  secondaryLabel.append(
    secondaryInput,
    document.createTextNode("Учитывать дополнительные темы"),
  );
  advancedControls.append(secondaryLabel);
  formatSelect.onchange = () => {
    state.publicationFormat = formatSelect.value;
    state.region = "";
    renderRegions();
    renderChips();
    refresh();
    map.fitVisible();
  };
  secondaryInput.onchange = () => {
    categoryCounts=null;
    state.includeSecondary = secondaryInput.checked;
    state.region = "";
    renderRegions();
    renderChips();
    refresh();
  };
  function matchesCategory(ch, value) {
    if (!value) return true;
    if (value.startsWith("group:"))
      return (
        ch.topic_group === value.slice(6) ||
        (state.includeSecondary &&
          (ch.secondary_topics || []).some(
            (t) => SUB_MAP[t]?.group === value.slice(6),
          ))
      );
    return (
      ch.primary_category === value ||
      (ch.broader_topics || []).includes(value) ||
      (state.includeSecondary && (ch.secondary_topics || []).includes(value))
    );
  }
  categoryEl.onchange = () => {
    state.category = categoryEl.value;
    state.subcategory = "";
    state.geoScope = "";
    state.region = "";
    renderRegions();
    renderChips();
    refresh();
    map.fitVisible();
  };
  document.getElementById("group-by").onchange = (e) => {
    state.group = e.target.value;
    categoryCounts=null;
    state.country=state.region=state.geoScope='';
    el.country.value=el.region.value='';
    document.querySelectorAll('[data-view]').forEach(button=>{
      const active=button.dataset.view===state.group;
      button.classList.toggle('is-active',active);
      button.setAttribute('aria-pressed',String(active));
    });
    document.getElementById('territory-section').hidden=state.group!=='region';
    document.getElementById('mode-description').textContent=isDemo?'Демо-каталог · аудитория и актуальность не проверены':state.group==='topic'?'Все каналы. Близкие темы — рядом.':'Местная повестка. Связи с территорией.';
    layout = createLayout();
    map.setLayout(layout);
    renderCountries();renderRegions();renderChips();
    refresh();
    map.fitVisible();
    document.getElementById("group-caption").textContent =
      state.group === "topic" ? " / По темам" : " / По географии";
  };
  document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{
    const select=document.getElementById('group-by');
    if(select.value===button.dataset.view)return;
    select.value=button.dataset.view;select.dispatchEvent(new Event('change'));
  });
  renderChips();
  renderRegions();
  renderCountries();

  let legendEl = null;
  document.getElementById('legend-toggle').onclick=e=>{
    const open=e.currentTarget.getAttribute('aria-expanded')!=='true';
    e.currentTarget.setAttribute('aria-expanded',String(open));
    renderLegend();
    legendEl?.classList.toggle('is-open',open);
  };
  let searchTimer = 0;
  el.search.addEventListener("input", () => {
    state.query = el.search.value;
    el.clear.hidden = !state.query;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refresh, 150);
  });
  el.clear.addEventListener("click", () => {
    el.search.value = "";
    state.query = "";
    el.clear.hidden = true;
    refresh();
  });
  el.region.addEventListener("change", () => {
    state.region = el.region.value;
    refresh();
    map.fitVisible();
  });
  el.country.addEventListener("change", () => {
    state.country = el.country.value;
    renderRegions();
    renderChips();
    refresh();
    map.fitVisible();
  });

  refresh();
  navigationButtons.forEach(button=>button.disabled=false);

  function categoryChannels() {
    return viewChannels().filter(
      (c) =>
        matchesCategory(c, state.category) &&
        (!state.publicationFormat ||
          c.publication_formats.includes(state.publicationFormat)) &&
        (!state.country || c.geography_country === state.country),
    );
  }
  function renderChips() {
    el.chips.replaceChildren();
    const rows = categoryChannels();
    if (rows.length && rows.every((c) => c.geography_scope === "nonlocal")) {
      const note = document.createElement("p");
      note.className = "muted small geo-not-applicable";
      note.textContent =
        "Для этой тематической подборки география не применяется. Каналы не относятся к неопределённому региону.";
      el.chips.appendChild(note);
      return;
    }
    for (const scope of [
      { id: "", label: "Любая география" },
      ...window.Topics.taxonomy.geography_scopes.filter(
        (g) => g.id !== "region",
      ),
    ]) {
      const count = rows.filter(
        (c) => !scope.id || c.geography_scope === scope.id,
      ).length;
      if(state.group==='region'&&scope.id&&count===0)continue;
      const button = document.createElement("button");
      button.className =
        "chip" + (state.geoScope === scope.id ? " chip-active" : "");
      button.setAttribute("aria-pressed", String(state.geoScope === scope.id));
      button.dataset.scope = scope.id;
      button.setAttribute("aria-label",scope.label+" · "+count);
      button.title = scope.label;
      const shortNames = {"":"Любая территория",multi_region:"Несколько регионов",country:"Страна",global:"Международная",nonlocal:"Без привязки",unknown:"Не уточнена"};
      const name = document.createElement("span");
      name.className = "chip-name";
      name.textContent = shortNames[scope.id] || scope.label;
      const counter = document.createElement("span");
      counter.className = "chip-count";
      counter.textContent = count;
      counter.setAttribute("aria-hidden","true");
      button.append(name,counter);
      button.disabled = count === 0 && scope.id !== "";
      button.onclick = () => {
        state.geoScope = scope.id;
        state.region = "";
        renderRegions();
        renderChips();
        refresh();
        map.fitVisible();
      };
      el.chips.appendChild(button);
    }
  }

  function renderRegions() {
    const regions = [
      ...new Set(
        categoryChannels()
          .filter((c) => (!state.country || c.geography_country === state.country) && (!c.geography_country || c.geography_country === "RU"))
          .filter(
            (c) => !state.geoScope || c.geography_scope === state.geoScope,
          )
          .flatMap((c) => c.geography_regions),
      ),
    ].sort((a, b) => a.localeCompare(b, "ru"));

    el.region.innerHTML = '<option value="">Все регионы</option>';
    if (!regions.includes(state.region)) state.region = "";
    if (regions.length === 0) {
      el.regionBlock.hidden = true;
      return;
    }
    el.regionBlock.hidden = false;
    el.region.innerHTML = '<option value="">Все регионы</option>';
    for (const r of regions) {
      const opt = document.createElement("option");
      opt.value = r;
      opt.textContent = r;
      el.region.appendChild(opt);
    }
    el.region.value = state.region;
  }

  function renderCountries() {
    const present = new Set(
      viewChannels().map((c) => c.geography_country).filter(Boolean),
    );
    el.country.innerHTML = '<option value="">Все страны</option>';
    for (const code of [...present].sort((a, b) =>
      (COUNTRY_LABELS[a] || a).localeCompare(COUNTRY_LABELS[b] || b, "ru"),
    )) {
      const label = COUNTRY_LABELS[code] || code;
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = label;
      el.country.appendChild(opt);
    }
  }

  function viewChannels() {
    return state.group==='region'?state.channels.filter(window.AtlasView.geographicChannel):state.channels;
  }
  function refresh() {
    let list = viewChannels();
    if (state.category)
      list = list.filter((c) => matchesCategory(c, state.category));
    if (state.publicationFormat)
      list = list.filter((c) =>
        c.publication_formats.includes(state.publicationFormat),
      );
    if (state.geoScope)
      list = list.filter((c) => c.geography_scope === state.geoScope);
    if (state.subcategory) {
      list = list.filter(
        (c) => (c.subcategory || "other") === state.subcategory,
      );
    }
    if (state.region)
      list = list.filter((c) => c.geography_regions.includes(state.region));
    if (state.country)
      list = list.filter((c) => c.geography_country === state.country);

    if (state.query.trim()) {
      list = window.Search.searchChannels(list, state.query);
    } else {
      list = [...list].sort(
        (a, b) => (Number(b.subscribers) || 0) - (Number(a.subscribers) || 0),
      );
    }

    if (state.sort === "name")
      list = [...list].sort((a, b) => a.name.localeCompare(b.name, "ru"));
    if (state.selectedId && !list.some((c) => c.id === state.selectedId))
      closeDetail(false);
    state.listLimit = 80;
    state.visible = list;
    map.setVisibleIds(new Set(list.map((c) => c.id)));
    renderList();
    renderLegend();
    if(!categoryCounts) {
      const rows=viewChannels();
      categoryCounts=new Map([...categoryEl.options].filter(o=>o.value).map(o=>[o.value,rows.filter(c=>matchesCategory(c,o.value)).length]));
    }
    for (const option of categoryEl.options)
      if (option.value) {
        const label = option.value.startsWith("group:")
          ? GROUP_MAP[option.value.slice(6)].label + " · все темы"
          : SUB_MAP[option.value].label;
        option.textContent =
          label +
          " · " +
           categoryCounts.get(option.value);
      }
    el.count.textContent = list.length;
    const visibleIds = new Set(list.map((c) => c.id));
    const mapped = layout.nodes.filter((n) => visibleIds.has(n.id)).length;
    document.getElementById("map-summary").innerHTML =
      `<strong>${mapped.toLocaleString("ru-RU")}</strong> ${state.group==='region'?'каналов с привязкой к территории':'каналов на карте'}`;
    document.getElementById('map-empty').hidden=mapped>0;

    const hasFilter =
      state.query ||
      state.category ||
      state.publicationFormat ||
      state.includeSecondary ||
      state.geoScope ||
      state.subcategory ||
      state.region ||
      state.country;
    resetBtn.hidden = !hasFilter;
    const filterCount = [state.query.trim(),state.category,state.publicationFormat,state.includeSecondary,state.geoScope,state.subcategory,state.region,state.country].filter(Boolean).length;
    const badge=document.getElementById('filters-badge');
    badge.hidden=!filterCount;badge.textContent=filterCount;
    document.getElementById('apply-filters').textContent='Показать '+list.length.toLocaleString('ru-RU')+' каналов';
    document.getElementById("filter-summary").textContent = filterCount ? "Активных фильтров: "+filterCount : "Все каналы · без ограничений";
    const advancedCount = Number(Boolean(state.publicationFormat)) + Number(state.includeSecondary);
    const advancedBadge = document.getElementById("advanced-count");
    advancedBadge.hidden = !advancedCount;
    advancedBadge.textContent = advancedCount;
    const geoCount = [state.geoScope,state.region,state.country].filter(Boolean).length;
    document.getElementById("territory-current").textContent = geoCount ? "Выбрано: "+geoCount : "Любая территория";
  }

  function renderList() {
    if(activePanel!=='search')return;
    el.list.innerHTML = "";
    if (state.visible.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent =
        "Каналы не найдены. Попробуйте другой запрос или сбросьте фильтры.";
      el.list.appendChild(li);
      return;
    }
    const frag = document.createDocumentFragment();
    const limit = state.listLimit;

    for (let i = 0; i < Math.min(limit, state.visible.length); i++) {
      const ch = state.visible[i];
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.className =
        "result" + (ch.id === state.selectedId ? " result-active" : "");
      btn.dataset.id = ch.id;

      const color = colorOf(ch);
      const subs = formatSubs(ch.subscribers);
      const meta = [topicLabel(ch)];
      if (state.group==='region' && ch.geography_regions.length)
        meta.push(ch.geography_regions.join(", "));
      else if (state.group==='region' && ch.geography_country)
        meta.push(COUNTRY_LABELS[ch.geography_country] || ch.geography_country);
      if (subs) meta.push(subs);

      btn.innerHTML =
        `<span class="result-bar" style="background:${color}40;color:${color}">${escapeHtml(Array.from(ch.name || ch.username || "?")[0].toUpperCase())}</span>` +
        `<span class="result-body">` +
        `<span class="result-row"><span class="result-name">${escapeHtml(ch.name)}</span></span>` +
        `<span class="result-sub">@${escapeHtml(ch.username)}${meta.length ? " · " + escapeHtml(meta.join(" · ")) : ""}</span>` +
        `</span>`;

      btn.setAttribute("aria-pressed", String(ch.id === state.selectedId));
      btn.addEventListener("click", () => selectChannel(ch.id));
      li.appendChild(btn);
      frag.appendChild(li);
    }

    if (state.visible.length > limit) {
      const li = document.createElement("li");
      li.className = "empty";
      const more = document.createElement("button");
      more.className = "load-more";
      more.textContent = `Показать ещё · осталось ${state.visible.length - limit}`;
      more.onclick = () => {
        state.listLimit += 100;
        renderList();
      };
      li.appendChild(more);
      frag.appendChild(li);
    }
    el.list.appendChild(frag);
  }

  function renderLegend() {
    if(document.getElementById('legend-toggle').getAttribute('aria-expanded')!=='true')return;
    if (!legendEl) {
      legendEl = document.createElement("div");
      legendEl.className = "legend";
      el.wrap.appendChild(legendEl);
    }
    const groups = new Map();
    const clusterIds=new Map(layout.nodes.map((n)=>[n.id,n.cluster]));
    const clustersById=new Map(layout.clusters.map(c=>[c.id,c]));
    for (const ch of state.visible) {
      const key = state.group==='topic'?'group:'+ch.topic_group:clusterIds.get(ch.id);
      if(!groups.has(key))groups.set(key,state.group==='topic'
        ?{label:GROUP_MAP[ch.topic_group]?.label||topicLabel(ch),color:colorOf(ch),ids:new Set()}
        :{...clustersById.get(key),ids:new Set()});
      groups.get(key).ids.add(ch.id);
    }
    legendEl.replaceChildren();
    for (const cluster of [...groups.values()].sort((a,b)=>b.ids.size-a.ids.size)) {
      const button = document.createElement("button");
      button.className = "legend-item";
      button.innerHTML = `<span class="legend-dot" style="background:${cluster.color}"></span><span class="legend-label">${escapeHtml(cluster.label)}</span><span class="legend-count">${cluster.ids.size}</span>`;
      button.setAttribute("aria-label", "Приблизить кластер: " + cluster.label);
      button.onclick = () => map.fitIds(cluster.ids);
      legendEl.appendChild(button);
    }
  }

  function selectChannel(id) {
    closePanel(false);
    state.selectedId = id;
    map.setSelected(id);
    renderList();
    renderDetail(id);
    el.detail.querySelector('.detail-close')?.focus({preventScroll:true});
  }

  function renderDetail(id) {
    const ch = state.channels.find((c) => c.id === id);
    if (!ch) {
      el.detail.hidden = true;
      return;
    }

    const sub =
      ch.subcategory === "other" && ch.topic_group !== "other_group"
        ? GROUP_MAP[ch.topic_group] || SUB_MAP.other
        : SUB_MAP[ch.subcategory] || SUB_MAP.other;
    const color = colorOf(ch);
    const subs = formatSubs(ch.subscribers);
    const countryLabel =
      COUNTRY_LABELS[ch.geography_country] || ch.geography_country || "";
    const initial = Array.from(ch.name || "?")[0].toUpperCase();

    const scopeLabel =
      window.Topics.taxonomy.geography_scopes.find(
        (g) => g.id === ch.geography_scope,
      )?.label || "География не уточнена";
    const badges = [
      `<span class="badge" style="background:${sub.color}25;color:color-mix(in srgb, ${sub.color}, white 45%)">${escapeHtml(topicLabel(ch))}</span>`,
    ];
    for (const tag of ch.secondary_topics || []) {
      const label = SUB_MAP[tag]?.label;
      if (label)
        badges.push(
          `<span class="badge badge-muted" title="Дополнительная тема">${escapeHtml(label)}</span>`,
        );
    }
    for (const fmt of ch.publication_formats || []) {
      const label = window.Topics.taxonomy.formats.find(
        (f) => f.id === fmt,
      )?.label;
      if (label)
        badges.push(
          `<span class="badge badge-muted">${escapeHtml(label)}</span>`,
        );
    }
    if (["country","global","nonlocal"].includes(ch.geography_scope))
      badges.push(
        `<span class="badge badge-muted">${escapeHtml(scopeLabel)}</span>`,
      );
    if (ch.geography_regions.length > 1) {
      badges.push(
        `<span class="badge badge-muted">${escapeHtml(ch.geography_regions.join(", "))}</span>`,
      );
    } else if (ch.channel_type === "federal") {
      badges.push(
        `<span class="badge" style="background:#a0b5a825;color:#bdcec2">Федеральный</span>`,
      );
    } else if (ch.geography_regions.length === 1) {
      badges.push(
        `<span class="badge badge-muted">${escapeHtml(ch.geography_regions[0])}</span>`,
      );
    }
    if (countryLabel)
      badges.push(
        `<span class="badge badge-muted">${escapeHtml(countryLabel)}</span>`,
      );
    if (subs) badges.push(`<span class="badge badge-muted">${subs}</span>`);

    el.detail.hidden = false;
    el.detail.setAttribute("role", "region");
    el.detail.setAttribute("aria-label", "Карточка канала");
    el.detail.innerHTML =
      `<button class="detail-close" aria-label="Закрыть">×</button>` +
      `<div class="detail-head">` +
      `<span class="detail-avatar" style="background:${color}">${escapeHtml(initial)}</span>` +
      `<div>` +
      `<h2>${escapeHtml(ch.name)}</h2>` +
      `<a class="detail-user" href="${escapeAttr(telegramUrl(ch.username))}" target="_blank" rel="noreferrer noopener">@${escapeHtml(ch.username)}</a>` +
      `</div>` +
      `</div>` +
      `<div class="detail-badges">${badges.join("")}</div>` +
      `<details class="detail-diagnostics"><summary>О данных канала</summary>`+metadataFreshness(ch) +
      `<p class="classification-path">${escapeHtml(sub.label)}${ch.geography_scope === "region" ? "" : " → " + escapeHtml(scopeLabel)}${ch.geography_regions.length ? " → " + escapeHtml(ch.geography_regions.join(", ")) : ch.geography_scope === "country" ? " → " + escapeHtml(countryLabel) : ""}</p>` +
      topicExplanation(ch) +
      `<p class="geography-explanation">${escapeHtml(ch.geography_explanation || "")}</p>` +
      (["semantic-v9", "semantic-v10", "semantic-v11"].includes(ch.classification_engine) &&
      ch.geography_candidates?.length
        ? `<p class="geography-explanation">Территории для проверки: ${escapeHtml(ch.geography_candidates.map((x)=>typeof x==="string"?x:x.label).join(", "))}</p>`
        : "") +
      (ch.classification_review_stale
        ? '<p class="topic-explanation">Профиль изменился после проверки владельцем. Сохранённая метка требует повторной проверки.</p>'
        : "") +
      `</details>`+(ch.description
        ? `<p class="detail-desc">${escapeHtml(ch.description)}</p>`
        : "") +
      (ch.classification_engine !== "semantic-v11" && ch.region_review_required &&
      ch.geography_review_required &&
      ch.region_candidates?.length
        ? `<p class="detail-desc">Кандидаты региона: ${ch.region_candidates.map((x) => `${escapeHtml(x.region)} (${Math.round(x.confidence * 100)}%)`).join(" · ")}</p>`
        : "") +
      `<details class="topic-edit"><summary>Исправить тему</summary><label for="topic-correction">Тема канала</label><select id="topic-correction"><option value="">Автоопределение / данные каталога</option>${SUB_CATEGORIES.map((t) => `<option value="${t.id}" ${window.Topics.hasLocal(ch.username) && ch.subcategory === t.id ? "selected" : ""}>${escapeHtml(t.label)}</option>`).join("")}</select><button id="save-topic">Сохранить в этом браузере</button><p id="topic-save-status" role="status"></p></details>` +
      `<a class="detail-open" href="${escapeAttr(telegramUrl(ch.username))}" target="_blank" rel="noreferrer noopener">Открыть в Telegram →</a>`;

    el.detail.querySelector("#save-topic").onclick = () => {
      try {
        window.Topics.setLocal(
          ch.username,
          document.getElementById("topic-correction").value,
        );
        state.channels = state.channels.map((c) =>
          c.id === ch.id ? {...window.Topics.enrich(catalogChannels.get(c.id)||c),layout_source_binding:c.layout_source_binding} : c,
        );
        layoutCache.clear();categoryCounts=null;
        layout = createLayout();
        map.setLayout(layout);
        renderRegions();
        renderChips();
        refresh();
        if (state.visible.some((c) => c.id === ch.id)) {
          selectChannel(ch.id);
          const status = document.getElementById("topic-save-status");
          status.closest("details").open = true;
          status.textContent =
            "Тема обновлена. Изменение действует в этом браузере.";
        }
      } catch (_) {
        document.getElementById("topic-save-status").textContent =
          "Не удалось сохранить: браузер ограничивает локальное хранилище.";
      }
    };
    el.detail.querySelector(".detail-close").addEventListener("click", () => {
      closeDetail();
    });
  }

  function metadataFreshness(ch) {
    const status = window.Freshness.status(ch);
    const labels = {
      fresh: "Данные проверены за последние 24 часа",
      stale: "Данные старше 24 часов",
      unknown: "Нет достоверной даты проверки данных",
    };
    const checked =
      status === "unknown" ? null : window.Freshness.date(ch.last_meta_at);
    const attempted = window.Freshness.date(ch.last_meta_attempt_at);
    return (
      `<div class="metadata-freshness" data-status="${status}"><b>${labels[status]}</b>` +
      (checked === null
        ? ""
        : `<p>Последняя успешная проверка: ${escapeHtml(new Date(checked).toLocaleString("ru-RU"))}</p>`) +
      (ch.last_meta_error
        ? `<p class="metadata-error">Последняя попытка${attempted === null ? "" : " " + escapeHtml(new Date(attempted).toLocaleString("ru-RU"))}: ${escapeHtml(window.Freshness.errorLabel(ch.last_meta_error))}. Прежние данные сохранены.</p>`
        : "") +
      "</div>"
    );
  }

  function topicExplanation(ch) {
    const labels = {
      local: "Выбрано вами в этом браузере",
      manual: "Правило владельца каталога",
      declared: "Тема из каталога",
      heuristic: "Определено по названию и описанию",
      "semantic-v9": "Предложение смысловой модели V9",
      "semantic-v10": "Смысловой классификатор V10",
      "semantic-v11-proposal": "V11 · предложение по новой сетке (не подтверждено)",
      "semantic-v11v2-proposal": "11v2 · предложение гибридного классификатора (не подтверждено)",
      legacy_until_review:
        "Текущая тема сохранена; предложение модели требует проверки",
      fallback: "Общая рубрика каталога",
      unknown: "Недостаточно данных для определения темы",
    };
    const matches = (ch.topic_evidence || []).map((x) =>
      typeof x === "string"
        ? x.split(":").slice(1).join(":")
        : (x.source === "post"
            ? "Пост " + (x.message_id || "") + ": "
            : "Профиль: ") + String(x.text || "").slice(0, 160),
    );
    const alternatives = (ch.topic_candidates || [])
      .filter(
        (x) =>
          x.id !== ch.subcategory &&
          (["semantic-v9", "semantic-v10", "semantic-v11", "semantic-v11v2"].includes(ch.classification_engine) || x.score >= 3),
      )
      .slice(0, 2);
    return (
      `<div class="topic-explanation"><b>${escapeHtml(labels[ch.topic_source] || "Тема каталога")}</b>` +
      (ch.classification_engine==="semantic-v11v2"&&ch.topic_granularity!=="topic"?`<p>${ch.topic_granularity==="group"?"Выбран только раздел. Подтема требует проверки.":"Данных недостаточно для назначения темы; кандидаты ниже не применены."}</p>`:"") +
      (ch.semantic_proposal
        ? `<p>Предложение ${ch.classification_engine === "semantic-v11v2" ? "11v2" : ch.classification_engine === "semantic-v11" ? "V11" : ch.classification_engine === "semantic-v10" ? "V10" : "V9"}: ${escapeHtml(SUB_MAP[ch.semantic_proposal.subcategory]?.label || ch.semantic_proposal.subcategory || "Не определено")}</p>`
        : "") +
      (ch.broader_topics?.length
        ? `<p>Более широкая тема: ${escapeHtml(ch.broader_topics.map((t) => SUB_MAP[t]?.label || t).join(" → "))}</p>`
        : "") +
      (matches.length
        ? `<p>Признаки: ${escapeHtml([...new Set(matches)].join(", "))}</p>`
        : "") +
      (alternatives.length
        ? `<p>Также близко: ${alternatives.map((x) => escapeHtml(SUB_MAP[x.id]?.label || x.id)).join(", ")}</p>`
        : "") +
      (["semantic-v9", "semantic-v10", "semantic-v11"].includes(ch.classification_engine) && ch.post_routing
        ? `<p>${ch.post_routing.status === "usable" ? "Учтены публикации: " + (ch.post_routing.usable ?? (ch.topic_evidence || []).filter((x) => typeof x === "object" && x.source === "post").length) + "." : ch.post_routing.requested ? "Для уточнения нужны содержательные публикации канала." : "Использовано название и описание профиля."}</p>`
        : "") +
      (ch.topic_review_required
        ? "<p>Тема требует проверки. Её можно уточнить ниже.</p>"
        : "") +
      "</div>"
    );
  }

  function closeDetail(render = true) {
    const restoreFocus=!el.detail.hidden&&el.detail.contains(document.activeElement);
    el.detail.hidden = true;
    state.selectedId = null;
    map.setSelected(null);
    if (render) renderList();
    if(restoreFocus)document.getElementById('search-toggle').focus({preventScroll:true});
  }

  function showFatal(msg) {
    document.body.innerHTML =
      `<div class="app-message"><h2>Не удалось загрузить данные</h2>` +
      `<p class="muted">${escapeHtml(msg)}</p></div>`;
  }

  function formatSubs(n) {
    const v = Number(n) || 0;
    if (v <= 0) return "";
    if (v >= 1_000_000)
      return (v / 1_000_000).toFixed(1).replace(".0", "") + "M";
    if (v >= 1_000) return (v / 1_000).toFixed(1).replace(".0", "") + "K";
    return String(v);
  }

  function telegramUrl(username) {
    return /^[a-z][a-z0-9_]{0,31}$/i.test(username || "")
      ? "https://t.me/" + encodeURIComponent(username)
      : "#";
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(
      /[&<>"']/g,
      (m) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[m],
    );
  }
  function escapeAttr(s) {
    return escapeHtml(s);
  }
})();
