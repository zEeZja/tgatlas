(async function () {
  const SUB_CATEGORIES = window.Topics.taxonomy.topics;
  const SUB_MAP = window.Topics.byId;
  const GROUP_MAP = window.Topics.groupsById;
  const CATEGORY_LABELS = Object.fromEntries(
    window.Topics.taxonomy.sections.map((s) => [s.id, s.label]),
  );
  const COUNTRY_LABELS = {
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
    listLimit: 200,
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

  let channels;
  try {
    channels = (await window.Api.getChannels()).map((ch) =>
      window.Topics.enrich(ch),
    );
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

  function colorOf(ch) {
    return (
      GROUP_MAP[ch.topic_group]?.color ||
      (SUB_MAP[ch.subcategory] || SUB_MAP.other).color
    );
  }
  let layout = window.Layout.buildClusterLayout(channels, state.group);
  const channelsById = new Map(channels.map((ch) => [ch.id, ch]));
  for (const node of layout.nodes) {
    node.color = colorOf(channelsById.get(node.id) || node);
  }

  const map = new window.CanvasMap(el.canvas, el.wrap, {
    onSelect: (id) => selectChannel(id),
  });
  map.setLayout(layout);
  document.getElementById("relief-toggle").onclick = (e) => {
    map.showRelief = !map.showRelief;
    e.currentTarget.setAttribute("aria-pressed", String(map.showRelief));
    e.currentTarget.textContent =
      "Рельеф: " + (map.showRelief ? "вкл." : "выкл.");
    map._schedule();
  };
  document.getElementById("zoom-in").onclick = () => map.zoomBy(1.35);
  document.getElementById("zoom-out").onclick = () => map.zoomBy(1 / 1.35);
  document.getElementById("fit-map").onclick = () => map.fitVisible();
  const mobileToggle = document.getElementById("mobile-toggle");
  mobileToggle.onclick = () => {
    const mode = document.querySelector(".app").classList.toggle("map-mode");
    mobileToggle.textContent = mode ? "← К списку каналов" : "Показать карту →";
  };
  const helpToggle = document.getElementById("help-toggle");
  helpToggle.onclick = () => {
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
      el.search.focus();
    }
    if (e.key === "Escape") {
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
    statsBar.innerHTML = `<span><b>${rows.length}</b> каналов · <b>${new Set(rows.map((c) => c.subcategory)).size}</b> тем · <b>${rows.filter((c) => c.topic_review_required).length}</b> для проверки темы</span>` +
      `<span class="freshness-summary" title="Свежесть названия, описания и аудитории, не дата экспорта">Данные: ${freshness.fresh} свежих · ${freshness.stale} старше 24 ч · ${freshness.unknown} без проверки</span>`;
  }
  renderStats();
  el.chips.parentElement.insertBefore(
    statsBar,
    el.chips.parentElement.firstChild,
  );

  const resetBtn = document.createElement("button");
  resetBtn.className = "reset-btn";
  resetBtn.textContent = "Сбросить фильтры";
  resetBtn.hidden = true;
  resetBtn.addEventListener("click", () => {
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
  el.chips.parentElement.appendChild(resetBtn);

  const categoryEl = document.getElementById("category");
  const priority = window.Topics.taxonomy.priority_groups || [];
  for (const group of [...window.Topics.taxonomy.groups].sort((a, b) => {
    const rank = id => priority.includes(id) ? priority.indexOf(id) : priority.length;
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
  categoryEl.parentElement.append(formatLabel, formatSelect);
  const secondaryLabel = document.createElement("label");
  secondaryLabel.style.display = "flex";
  secondaryLabel.style.alignItems = "center";
  secondaryLabel.style.gap = "8px";
  const secondaryInput = document.createElement("input");
  secondaryInput.type = "checkbox";
  secondaryInput.id = "include-secondary";
  secondaryInput.style.width = "auto";
  secondaryLabel.append(
    secondaryInput,
    document.createTextNode("Учитывать дополнительные темы"),
  );
  categoryEl.parentElement.append(secondaryLabel);
  formatSelect.onchange = () => {
    state.publicationFormat = formatSelect.value;
    state.region = "";
    renderRegions();
    renderChips();
    refresh();
    map.fitVisible();
  };
  secondaryInput.onchange = () => {
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
    layout = window.Layout.buildClusterLayout(state.channels, state.group);
    map.setLayout(layout);
    refresh();
    map.fitVisible();
    document.getElementById("group-caption").textContent =
      state.group === "topic" ? " / По темам" : " / По регионам России";
  };
  renderChips();
  renderRegions();
  renderCountries();

  let legendEl = null;
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

  function categoryChannels() {
    return state.channels.filter(
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
    if (rows.length && rows.every(c => c.geography_scope === "nonlocal")) {
      const note = document.createElement("p");
      note.className = "muted small geo-not-applicable";
      note.textContent = "Для этой тематической подборки география не применяется. Каналы не относятся к неопределённому региону.";
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
      const button = document.createElement("button");
      button.className =
        "chip" + (state.geoScope === scope.id ? " chip-active" : "");
      button.setAttribute("aria-pressed", String(state.geoScope === scope.id));
      button.textContent = scope.label + " · " + count;
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
      state.channels.map((c) => c.geography_country).filter(Boolean),
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

  function refresh() {
    let list = state.channels;
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
    state.listLimit = 200;
    state.visible = list;
    map.setVisibleIds(new Set(list.map((c) => c.id)));
    renderList();
    renderLegend();
    for (const option of categoryEl.options)
      if (option.value) {
        const label = option.value.startsWith("group:")
          ? GROUP_MAP[option.value.slice(6)].label + " · все темы"
          : SUB_MAP[option.value].label;
        option.textContent =
          label +
          " · " +
          state.channels.filter((c) => matchesCategory(c, option.value)).length;
      }
    el.count.textContent = list.length;
    renderStats();
    const visibleIds = new Set(list.map((c) => c.id));
    const mapped = layout.nodes.filter((n) => visibleIds.has(n.id)).length;
    document.getElementById("map-summary").innerHTML =
      `<strong>${mapped.toLocaleString("ru-RU")}</strong>каналов на карте / ${list.length} в каталоге`;

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
  }

  function renderList() {
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
      const meta = [(SUB_MAP[ch.subcategory] || SUB_MAP.other).label];
      if (ch.channel_type === "federal") meta.push("федеральный");
      else if (ch.geography_regions.length)
        meta.push(ch.geography_regions.join(", "));
      else if (ch.country)
        meta.push(COUNTRY_LABELS[ch.geography_country] || ch.geography_country);
      if (subs) meta.push(subs);

      btn.innerHTML =
        `<span class="result-bar" style="background:${color}40;color:${color}">${escapeHtml((ch.name || ch.username || "?").slice(0, 1).toUpperCase())}</span>` +
        `<span class="result-body">` +
        `<span class="result-row"><span class="result-name">${escapeHtml(ch.name)}</span></span>` +
        `<span class="result-sub">@${escapeHtml(ch.username)}${meta.length ? " · " + escapeHtml(meta.join(" · ")) : ""}</span>` +
        `</span><span class="result-arrow" aria-hidden="true">↗</span>`;

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
        state.listLimit += 200;
        renderList();
      };
      li.appendChild(more);
      frag.appendChild(li);
    }
    el.list.appendChild(frag);
  }

  function renderLegend() {
    if (!legendEl) {
      legendEl = document.createElement("div");
      legendEl.className = "legend";
      el.wrap.appendChild(legendEl);
    }
    const counts = new Map();
    for (const ch of state.visible) {
      const key =
        state.group === "topic"
          ? "group:" + ch.topic_group
          : window.Layout.geographyGroup(ch);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    legendEl.replaceChildren();
    for (const cluster of layout.clusters.filter((t) => counts.has(t.id))) {
      const button = document.createElement("button");
      button.className = "legend-item";
      button.innerHTML = `<span class="legend-dot" style="background:${cluster.color}"></span><span class="legend-label">${escapeHtml(cluster.label)}</span><span class="legend-count">${counts.get(cluster.id)}</span>`;
      button.setAttribute("aria-label", "Приблизить кластер: " + cluster.label);
      button.onclick = () => {
        const visible = new Set(state.visible.map((ch) => ch.id));
        map.fitIds(
          new Set(
            layout.nodes
              .filter((n) => n.cluster === cluster.id && visible.has(n.id))
              .map((n) => n.id),
          ),
        );
      };
      legendEl.appendChild(button);
    }
  }

  function selectChannel(id) {
    state.selectedId = id;
    map.setSelected(id);
    if (matchMedia("(max-width:760px)").matches) {
      document.querySelector(".app").classList.add("map-mode");
      mobileToggle.textContent = "← К списку каналов";
    }
    renderList();
    renderDetail(id);
  }

  function renderDetail(id) {
    const ch = state.channels.find((c) => c.id === id);
    if (!ch) {
      el.detail.hidden = true;
      return;
    }

    const sub =
      ch.subcategory === "other" && ch.topic_group !== "other_group"
        ? GROUP_MAP[ch.topic_group]
        : SUB_MAP[ch.subcategory] || SUB_MAP.other;
    const color = colorOf(ch);
    const subs = formatSubs(ch.subscribers);
    const countryLabel =
      COUNTRY_LABELS[ch.geography_country] || ch.geography_country || "";
    const initial = (ch.name || "?").slice(0, 1).toUpperCase();

    const scopeLabel =
      window.Topics.taxonomy.geography_scopes.find(
        (g) => g.id === ch.geography_scope,
      )?.label || "География не уточнена";
    const badges = [
      `<span class="badge" style="background:${sub.color}25;color:color-mix(in srgb, ${sub.color}, white 45%)">${escapeHtml(sub.label)}</span>`,
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
    if (ch.geography_scope !== "region")
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
    } else if (ch.geography_scope === "unknown") {
      badges.push(
        `<span class="badge badge-muted">Территория не уточнена</span>`,
      );
    }
    if (ch.region_review_required && ch.geography_review_required) {
      badges.push(`<span class="badge badge-muted">Нужна проверка</span>`);
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
      metadataFreshness(ch) +
      `<p class="classification-path">${escapeHtml(sub.label)}${ch.geography_scope === "region" ? "" : " → " + escapeHtml(scopeLabel)}${ch.geography_regions.length ? " → " + escapeHtml(ch.geography_regions.join(", ")) : ch.geography_scope === "country" ? " → " + escapeHtml(countryLabel) : ""}</p>` +
      topicExplanation(ch) +
      `<p class="geography-explanation">${escapeHtml(ch.geography_explanation || "")}</p>` +
      (ch.classification_review_stale?'<p class="topic-explanation">Профиль изменился после проверки владельцем. Сохранённая метка требует повторной проверки.</p>':'') +
      (ch.description
        ? `<p class="detail-desc">${escapeHtml(ch.description)}</p>`
        : "") +
      (ch.region_review_required &&
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
          c.id === ch.id ? window.Topics.enrich(c) : c,
        );
        layout = window.Layout.buildClusterLayout(state.channels, state.group);
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
    const labels = { fresh: "Данные проверены за последние 24 часа", stale: "Данные старше 24 часов", unknown: "Нет достоверной даты проверки данных" };
    const checked = status === "unknown" ? null : window.Freshness.date(ch.last_meta_at);
    const attempted = window.Freshness.date(ch.last_meta_attempt_at);
    return `<div class="metadata-freshness" data-status="${status}"><b>${labels[status]}</b>` +
      (checked === null ? "" : `<p>Последняя успешная проверка: ${escapeHtml(new Date(checked).toLocaleString("ru-RU"))}</p>`) +
      (ch.last_meta_error ? `<p class="metadata-error">Последняя попытка${attempted === null ? "" : " " + escapeHtml(new Date(attempted).toLocaleString("ru-RU"))}: ${escapeHtml(window.Freshness.errorLabel(ch.last_meta_error))}. Прежние данные сохранены.</p>` : "") + "</div>";
  }

  function topicExplanation(ch) {
    const labels = {
      local: "Выбрано вами в этом браузере",
      manual: "Правило владельца каталога",
      declared: "Тема из каталога",
      heuristic: "Определено по названию и описанию",
      fallback: "Общая рубрика каталога",
      unknown: "Недостаточно данных для определения темы",
    };
    const matches = (ch.topic_evidence || []).map((x) =>
      x.split(":").slice(1).join(":"),
    );
    const alternatives = (ch.topic_candidates || [])
      .filter((x) => x.id !== ch.subcategory && x.score >= 3)
      .slice(0, 2);
    return (
      `<div class="topic-explanation"><b>${escapeHtml(labels[ch.topic_source] || "Тема каталога")}</b>` +
      (ch.broader_topics?.length ? `<p>Более широкая тема: ${escapeHtml(ch.broader_topics.map(t => SUB_MAP[t]?.label || t).join(" → "))}</p>` : "") +
      (matches.length
        ? `<p>Признаки: ${escapeHtml([...new Set(matches)].join(", "))}</p>`
        : "") +
      (alternatives.length
        ? `<p>Также близко: ${alternatives.map((x) => escapeHtml(SUB_MAP[x.id]?.label || x.id)).join(", ")}</p>`
        : "") +
      (ch.topic_review_required
        ? "<p>Тема требует проверки. Её можно уточнить ниже.</p>"
        : "") +
      "</div>"
    );
  }

  function closeDetail(render = true) {
    el.detail.hidden = true;
    state.selectedId = null;
    map.setSelected(null);
    if (render) renderList();
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
