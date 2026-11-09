(function () {
  async function loadGeoLayout() {
    let data = null;

    // В HTTP-режиме пробуем взять актуальную карту у API.
    if (window.Api?.CONFIG?.mode === 'http') {
      const base = window.Api.CONFIG.httpBase.replace(/\/$/, '');
      const response = await fetch(base + '/api/geo-map');
      if (!response.ok) throw new Error(`HTTP ${response.status} при загрузке /api/geo-map`);
      data = await response.json();
    } else {
      // Встроенный JS-файл позволяет открыть карту напрямую через file://.
      data = window.TG_GEO_MAP || null;
      if (!data || !Array.isArray(data.points)) {
        try {
          const response = await fetch('./data/geo_map.json');
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          data = await response.json();
        } catch (error) {
          throw new Error(
            'Геокарта не найдена. Запустите `python main.py build-geo-map`, ' +
            'затем `python main.py export` и обновите страницу.'
          );
        }
      }
    }

    const points = Array.isArray(data.points) ? data.points : [];
    if (points.length === 0) {
      throw new Error('Геокарта не содержит региональных каналов с координатами.');
    }

    const nodes = points.map(point => ({
      ...point,
      x: Number(point.x) || 0,
      y: Number(point.y) || 0,
      radius: nodeRadius(point),
    }));

    return {
      nodes,
      width: Number(data.width) || 1600,
      height: Number(data.height) || 780,
      meta: data.meta || {},
      clusters: [],
    };
  }

  function nodeRadius(channel) {
    const subscribers = Number(channel.subscribers) || 0;
    if (subscribers <= 0) return 2.2;
    const scale = Math.min(1, Math.log10(subscribers) / 7);
    return 2.2 + 4.8 * scale;
  }

  window.Layout = { loadGeoLayout, nodeRadius };
})();
