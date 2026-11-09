/**
 * Слой абстракции над данными.
 *
 * Сейчас mode = 'static': данные читаются из web/data/channels.js
 * (генерируется командой `python main.py export`) или, если страница
 * открыта через HTTP, — из web/data/channels.json.
 *
 * Когда появится FastAPI — меняется ОДНА константа:
 *   mode: 'http',
 *   httpBase: 'http://localhost:8000'
 * Остальной код (карта, поиск, фильтры) не трогается.
 */
(function () {
  const CONFIG = {
    mode: 'static',                       // 'static' | 'http'
    httpBase: 'http://localhost:8000',    // используется при mode = 'http'
    httpPath: '/api/channels',            // ожидаемый эндпоинт
  };

  async function getChannels() {
    if (CONFIG.mode === 'http') {
      const url = CONFIG.httpBase + CONFIG.httpPath;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status} при запросе ${url}`);
      const data = await r.json();
      if (Array.isArray(data)) return data;
      if (Array.isArray(data.channels)) return data.channels;
      throw new Error('Неожиданный формат ответа API');
    }

    // static: сначала встроенные данные (работает из file://)
    if (window.TG_DATA && Array.isArray(window.TG_DATA.channels)) {
      return window.TG_DATA.channels;
    }

    // static: fallback — fetch, работает только при открытии через http
    try {
      const r = await fetch('./data/channels.json');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      return data.channels ?? [];
    } catch (e) {
      throw new Error(
        'Данные не загружены. Запустите `python main.py export` в папке проекта, ' +
        'затем обновите страницу.'
      );
    }
  }

  window.Api = { getChannels, CONFIG };
})();
