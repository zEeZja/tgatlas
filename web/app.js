(async function () {
  const SUB_CATEGORIES = [
    { id: 'general',  label: 'Общие новости',  color: '#4d6bfe' },
    { id: 'sport',    label: 'Спорт',          color: '#3f9e4d' },
    { id: 'science',  label: 'Наука',          color: '#2f8fbf' },
    { id: 'business', label: 'Бизнес',         color: '#c9922e' },
    { id: 'tech',     label: 'Технологии',     color: '#7a5cf0' },
    { id: 'crypto',   label: 'Крипто',         color: '#d4692a' },
    { id: 'military', label: 'Военная сводка', color: '#5a6472' },
    { id: 'politics', label: 'Политика',       color: '#e0564d' },
    { id: 'world',    label: 'Мир',            color: '#2f9e8a' },
    { id: 'culture',  label: 'Культура',       color: '#b05a9c' },
    { id: 'other',    label: 'Другое',         color: '#8a8f98' },
  ];
  const SUB_MAP = Object.fromEntries(SUB_CATEGORIES.map(c => [c.id, c]));

  const REGION_PALETTE = [
    '#4d6bfe', '#2f9e8a', '#7a5cf0', '#c9922e', '#e0564d',
    '#d4692a', '#5a6472', '#2f8fbf', '#b05a9c', '#3f9e4d',
    '#a8742e', '#5a78d6', '#c14b6e', '#4a9a7c', '#8b6bb1',
    '#c77d3a', '#6e8a42', '#a14f8a', '#3d7ba8', '#b07850',
  ];

  const CATEGORY_LABELS = {
    news: 'Новости', community: 'Сообщества', education: 'Образование',
    sports: 'Спорт', business: 'Бизнес', media: 'Медиа', other: 'Другое',
  };

  const COUNTRY_LABELS = {
    RU: 'Россия', UA: 'Украина', BY: 'Беларусь', KZ: 'Казахстан',
    US: 'США', GB: 'Великобритания', DE: 'Германия', FR: 'Франция',
    IL: 'Израиль', AE: 'ОАЭ', TR: 'Турция', LV: 'Латвия', NL: 'Нидерланды', CZ: 'Чехия',
  };

  const FEDERAL_COLOR = '#a0b5a8';
  const UNKNOWN_COLOR = '#a0aec0';

  const el = {
    search: document.getElementById('search'),
    clear: document.getElementById('clear'),
    chips: document.getElementById('chips'),
    regionBlock: document.getElementById('region-block'),
    region: document.getElementById('region'),
    country: document.getElementById('country'),
    list: document.getElementById('list'),
    count: document.getElementById('count'),
    banner: document.getElementById('banner'),
    wrap: document.getElementById('map-wrap'),
    canvas: document.getElementById('map'),
    detail: document.getElementById('detail'),
  };

  const state = {
    channels: [], visible: [],
    listLimit: 200, sort: 'popular', query: '', subcategory: '', region: '', country: '',
    selectedId: null,
  };

  let channels;
  try {
    channels = await window.Api.getChannels();
  } catch (e) {
    showFatal(e.message);
    return;
  }
  state.channels = channels;

  if (window.TG_DATA && window.TG_DATA._sample) {
    el.banner.hidden = false;
    el.banner.innerHTML =
      'Демо · каналы из конфигурации проекта. Аудитория и актуальность не проверены.';
  }

  const regionColors = (() => {
    const regions = [...new Set(channels.map(c => c.region).filter(Boolean))].sort();
    const m = {};
    regions.forEach((r, i) => { m[r] = REGION_PALETTE[i % REGION_PALETTE.length]; });
    return m;
  })();
  const subColors = Object.fromEntries(SUB_CATEGORIES.map(c => [c.id, c.color]));

  function colorOf(ch) {
    if (ch.channel_type === 'federal') return FEDERAL_COLOR;
    if (ch.channel_type === 'unknown') return UNKNOWN_COLOR;
    const cat = ch.subcategory || 'other';
    if (cat !== 'general' && cat !== 'other') {
      return subColors[cat] || '#8a8f98';
    }
    if (ch.region) return regionColors[ch.region] || '#8a8f98';
    return '#b0b5c0';
  }

  let layout;
  try {
    layout = await window.Layout.loadGeoLayout();
  } catch (e) {
    showFatal('Не удалось загрузить географическую карту: ' + e.message);
    return;
  }
  const channelsById = new Map(channels.map(ch => [ch.id, ch]));
  for (const node of layout.nodes) {
    node.color = colorOf(channelsById.get(node.id) || node);
  }

  const map = new window.CanvasMap(el.canvas, el.wrap, {
    onSelect: (id) => selectChannel(id),
  });
  map.setLayout(layout);
  document.getElementById('zoom-in').onclick = () => map.zoomBy(1.35);
  document.getElementById('zoom-out').onclick = () => map.zoomBy(1 / 1.35);
  document.getElementById('fit-map').onclick = () => map.fit();
  const mobileToggle = document.getElementById('mobile-toggle');
  mobileToggle.onclick = () => {
    const mode = document.querySelector('.app').classList.toggle('map-mode');
    mobileToggle.textContent = mode ? '← К списку каналов' : 'Показать карту →';
  };
  const helpToggle = document.getElementById('help-toggle');
  helpToggle.onclick = () => {
    const help = document.getElementById('help');
    help.hidden = !help.hidden;
    helpToggle.setAttribute('aria-expanded', String(!help.hidden));
  };
  document.getElementById('sort').onchange = (e) => { state.sort = e.target.value; refresh(); };
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) {
      e.preventDefault(); el.search.focus();
    }
    if (e.key === 'Escape') {
      closeDetail(); document.getElementById('help').hidden = true;
      helpToggle.setAttribute('aria-expanded', 'false');
    }
  });

  const statsBar = document.createElement('div');
  statsBar.className = 'stats-bar';
  const federalCount = channels.filter(c => c.channel_type === 'federal').length;
  const regionalCount = channels.filter(c => c.channel_type === 'regional').length;
  const unknownCount = channels.filter(c => c.channel_type === 'unknown' || (!c.channel_type && !c.region)).length;
  const mappedCount = layout.nodes.length;
  statsBar.innerHTML = `<span><b>${channels.length}</b> каналов ·
    <b>${regionalCount}</b> региональных · <b>${mappedCount}</b> на карте ·
    <b>${federalCount}</b> федеральных · <b>${unknownCount}</b> на проверку</span>`;
  el.chips.parentElement.insertBefore(statsBar, el.chips.parentElement.firstChild);

  const resetBtn = document.createElement('button');
  resetBtn.className = 'reset-btn';
  resetBtn.textContent = 'Сбросить фильтры';
  resetBtn.hidden = true;
  resetBtn.addEventListener('click', () => {
    state.query = ''; state.subcategory = '';
    state.region = ''; state.country = '';
    el.search.value = ''; el.region.value = ''; el.country.value = '';
    el.clear.hidden = true;
    renderChips();
    refresh();
  });
  el.chips.parentElement.appendChild(resetBtn);

  renderChips();
  renderRegions();
  renderCountries();

  let legendEl = null;
  let searchTimer = 0;
  el.search.addEventListener('input', () => {
    state.query = el.search.value;
    el.clear.hidden = !state.query;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refresh, 150);
  });
  el.clear.addEventListener('click', () => {
    el.search.value = ''; state.query = ''; el.clear.hidden = true; refresh();
  });
  el.region.addEventListener('change', () => {
    state.region = el.region.value; refresh();
  });
  el.country.addEventListener('change', () => {
    state.country = el.country.value; refresh();
  });

  refresh();

  function renderChips() {
    el.chips.innerHTML = '';
    const counts = new Map();
    for (const ch of state.channels) {
      const key = ch.subcategory || 'other';
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const allBtn = document.createElement('button');
    allBtn.className = 'chip' + (state.subcategory === '' ? ' chip-active' : '');
    allBtn.textContent = 'Все темы';
    allBtn.setAttribute('aria-pressed', String(state.subcategory === ''));
    allBtn.addEventListener('click', () => {
      state.subcategory = ''; renderChips(); refresh();
    });
    el.chips.appendChild(allBtn);

    for (const cat of SUB_CATEGORIES) {
      const n = counts.get(cat.id) || 0;
      if (n === 0 && state.subcategory !== cat.id) continue;
      const active = state.subcategory === cat.id;
      const btn = document.createElement('button');
      btn.className = 'chip' + (active ? ' chip-active' : '');
      btn.setAttribute('aria-pressed', String(active));
      if (active) {
        btn.style.background = cat.color;
        btn.style.borderColor = cat.color;
        btn.style.color = '#fff';
      }
      btn.innerHTML =
        `<span class="chip-dot" style="background:${cat.color}"></span>` +
        `${escapeHtml(cat.label)}<span class="chip-count">${n}</span>`;
      btn.addEventListener('click', () => {
        state.subcategory = active ? '' : cat.id;
        renderChips(); refresh();
      });
      el.chips.appendChild(btn);
    }
  }

  function renderRegions() {
    const regions = [...new Set(
      state.channels.map(c => c.region).filter(Boolean)
    )].sort((a, b) => a.localeCompare(b, 'ru'));

    if (regions.length === 0) { el.regionBlock.hidden = true; return; }
    el.regionBlock.hidden = false;
    el.region.innerHTML = '<option value="">Все регионы</option>';
    for (const r of regions) {
      const opt = document.createElement('option');
      opt.value = r; opt.textContent = r;
      el.region.appendChild(opt);
    }
  }

  function renderCountries() {
    const present = new Set(state.channels.map(c => c.country).filter(Boolean));
    el.country.innerHTML = '<option value="">Все страны</option>';
    for (const [code, label] of Object.entries(COUNTRY_LABELS)) {
      if (!present.has(code)) continue;
      const opt = document.createElement('option');
      opt.value = code; opt.textContent = label;
      el.country.appendChild(opt);
    }
  }

  function refresh() {
    let list = state.channels;
    if (state.subcategory) {
      list = list.filter(c => (c.subcategory || 'other') === state.subcategory);
    }
    if (state.region) list = list.filter(c => c.region === state.region);
    if (state.country) list = list.filter(c => c.country === state.country);

    if (state.query.trim()) {
      list = window.Search.searchChannels(list, state.query);
    } else {
      list = [...list].sort(
        (a, b) => (Number(b.subscribers) || 0) - (Number(a.subscribers) || 0)
      );
    }

    if (state.sort === 'name') list = [...list].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
    if (state.selectedId && !list.some(c => c.id === state.selectedId)) closeDetail(false);
    state.listLimit = 200;
    state.visible = list;
    map.setVisibleIds(new Set(list.map(c => c.id)));
    renderList();
    renderLegend();
    el.count.textContent = list.length;
    const visibleIds = new Set(list.map(c => c.id));
    const mapped = layout.nodes.filter(n => visibleIds.has(n.id)).length;
    document.getElementById('map-summary').innerHTML = `<strong>${mapped.toLocaleString('ru-RU')}</strong>каналов на карте / ${list.length} в каталоге`;

    const hasFilter = state.query || state.subcategory || state.region || state.country;
    resetBtn.hidden = !hasFilter;
  }

  function renderList() {
    el.list.innerHTML = '';
    if (state.visible.length === 0) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = 'Каналы не найдены. Попробуйте другой запрос или сбросьте фильтры.';
      el.list.appendChild(li);
      return;
    }
    const frag = document.createDocumentFragment();
    const limit = state.listLimit;

    for (let i = 0; i < Math.min(limit, state.visible.length); i++) {
      const ch = state.visible[i];
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.className = 'result' + (ch.id === state.selectedId ? ' result-active' : '');
      btn.dataset.id = ch.id;

      const color = colorOf(ch);
      const subs = formatSubs(ch.subscribers);
      const meta = [];
      if (ch.channel_type === 'federal') meta.push('федеральный');
      else if (ch.region) meta.push(ch.region);
      else if (ch.country) meta.push(COUNTRY_LABELS[ch.country] || ch.country);
      if (subs) meta.push(subs);

      btn.innerHTML =
        `<span class="result-bar" style="background:${color}40;color:${color}">${escapeHtml((ch.name || ch.username || '?').slice(0,1).toUpperCase())}</span>` +
        `<span class="result-body">` +
          `<span class="result-row"><span class="result-name">${escapeHtml(ch.name)}</span></span>` +
          `<span class="result-sub">@${escapeHtml(ch.username)}${meta.length ? ' · ' + escapeHtml(meta.join(' · ')) : ''}</span>` +
        `</span><span class="result-arrow" aria-hidden="true">↗</span>`;

      btn.setAttribute('aria-pressed', String(ch.id === state.selectedId));
      btn.addEventListener('click', () => selectChannel(ch.id));
      li.appendChild(btn);
      frag.appendChild(li);
    }

    if (state.visible.length > limit) {
      const li = document.createElement('li');
      li.className = 'empty';
      const more = document.createElement('button');
      more.className = 'load-more';
      more.textContent = `Показать ещё · осталось ${state.visible.length - limit}`;
      more.onclick = () => { state.listLimit += 200; renderList(); };
      li.appendChild(more);
      frag.appendChild(li);
    }
    el.list.appendChild(frag);
  }



  function renderLegend() {
    if (!legendEl) {
      legendEl = document.createElement('div');
      legendEl.className = 'legend';
      el.wrap.appendChild(legendEl);
    }

    const groups = new Map();
    for (const ch of state.visible) {
      const color = colorOf(ch);
      const sub = SUB_MAP[ch.subcategory] || SUB_MAP.other;
      const label = ch.channel_type === 'federal' ? 'Федеральные' :
        ch.channel_type === 'unknown' ? 'Без региона' :
        (sub.id !== 'general' && sub.id !== 'other' ? sub.label : ch.region || 'Без региона');
      const key = color + ':' + label;
      if (!groups.has(key)) groups.set(key, { label, color, count: 0 });
      groups.get(key).count++;
    }

    const items = [...groups.values()]
      .sort((a, b) => b.count - a.count)
      .slice(0, 15);

    legendEl.innerHTML = items.map(g =>
      `<div class="legend-item">` +
        `<span class="legend-dot" style="background:${g.color}"></span>` +
        `<span class="legend-label">${escapeHtml(g.label)}</span>` +
        `<span class="legend-count">${g.count}</span>` +
      `</div>`
    ).join('');
  }

  function selectChannel(id) {
    state.selectedId = id;
    map.setSelected(id);
    if (matchMedia('(max-width:760px)').matches) {
      document.querySelector('.app').classList.add('map-mode');
      mobileToggle.textContent = '← К списку каналов';
    }
    renderList();
    renderDetail(id);
  }

  function renderDetail(id) {
    const ch = state.channels.find(c => c.id === id);
    if (!ch) { el.detail.hidden = true; return; }

    const sub = SUB_MAP[ch.subcategory] || SUB_MAP.other;
    const color = colorOf(ch);
    const subs = formatSubs(ch.subscribers);
    const countryLabel = COUNTRY_LABELS[ch.country] || ch.country || '';
    const initial = (ch.name || '?').slice(0, 1).toUpperCase();

    const badges = [
      `<span class="badge" style="background:${sub.color}25;color:color-mix(in srgb, ${sub.color}, white 45%)">${escapeHtml(CATEGORY_LABELS[ch.category] || ch.category || 'Канал')} → ${escapeHtml(sub.label)}</span>`,
    ];
    if (ch.channel_type === 'federal') {
      badges.push(`<span class="badge" style="background:#a0b5a825;color:#bdcec2">Федеральный</span>`);
    } else if (ch.region) {
      badges.push(`<span class="badge badge-muted">${escapeHtml(ch.region)}</span>`);
    } else {
      badges.push(`<span class="badge badge-muted">Регион не определён</span>`);
    }
    if (ch.region_review_required) {
      badges.push(`<span class="badge badge-muted">Нужна проверка</span>`);
    }
    if (countryLabel) badges.push(`<span class="badge badge-muted">${escapeHtml(countryLabel)}</span>`);
    if (subs) badges.push(`<span class="badge badge-muted">${subs}</span>`);

    el.detail.hidden = false;
    el.detail.setAttribute('role', 'region');
    el.detail.setAttribute('aria-label', 'Карточка канала');
    el.detail.innerHTML =
      `<button class="detail-close" aria-label="Закрыть">×</button>` +
      `<div class="detail-head">` +
        `<span class="detail-avatar" style="background:${color}">${escapeHtml(initial)}</span>` +
        `<div>` +
          `<h2>${escapeHtml(ch.name)}</h2>` +
          `<a class="detail-user" href="${escapeAttr(ch.url)}" target="_blank" rel="noreferrer noopener">@${escapeHtml(ch.username)}</a>` +
        `</div>` +
      `</div>` +
      `<div class="detail-badges">${badges.join('')}</div>` +
      (ch.description ? `<p class="detail-desc">${escapeHtml(ch.description)}</p>` : '') +
      (ch.region_review_required && ch.region_candidates?.length
        ? `<p class="detail-desc">Кандидаты региона: ${ch.region_candidates.map(x => `${escapeHtml(x.region)} (${Math.round(x.confidence * 100)}%)`).join(' · ')}</p>`
        : '') +
      `<a class="detail-open" href="${escapeAttr(ch.url)}" target="_blank" rel="noreferrer noopener">Открыть в Telegram →</a>`;

    el.detail.querySelector('.detail-close').addEventListener('click', () => {
      closeDetail();
    });
  }

  function closeDetail(render = true) {
    el.detail.hidden = true; state.selectedId = null; map.setSelected(null);
    if (render) renderList();
  }

  function showFatal(msg) {
    document.body.innerHTML =
      `<div class="app-message"><h2>Не удалось загрузить данные</h2>` +
      `<p class="muted">${escapeHtml(msg)}</p></div>`;
  }

  function formatSubs(n) {
    const v = Number(n) || 0;
    if (v <= 0) return '';
    if (v >= 1_000_000) return (v / 1_000_000).toFixed(1).replace('.0', '') + 'M';
    if (v >= 1_000) return (v / 1_000).toFixed(1).replace('.0', '') + 'K';
    return String(v);
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, m => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
  }
  function escapeAttr(s) { return escapeHtml(s); }
})();