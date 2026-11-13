/* Static JS/JSON exports or the optional FastAPI endpoint. Demo loads only on absence. */
(function () {
  const CONFIG = { mode: "static", httpBase: "", httpPath: "/api/channels" };
  function unpack(data) {
    const rows = Array.isArray(data) ? data : data?.channels;
    if (!Array.isArray(rows)) throw new Error("Ожидается массив channels");
    if (
      rows.some(
        (ch) =>
          !ch ||
          typeof ch !== "object" ||
          typeof ch.username !== "string" ||
          !ch.username ||
          typeof ch.name !== "string",
      )
    )
      throw new Error("Повреждён формат записи канала");
    const seen = new Set();
    for (const row of rows) {
      const username = row.username.trim().replace(/^@/, "").toLowerCase();
      if (!username || seen.has(username))
        throw new Error("Пустой или повторный username в каталоге: " + username);
      seen.add(username);
    }
    return rows;
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () =>
        reject(new Error("Данные и демонстрационный каталог не найдены"));
      document.head.appendChild(script);
    });
  }
  async function demo() {
    // HTTP must not resurrect an old JS snapshot when JSON is absent.
    window.TG_DATA = undefined;
    await loadScript("./demo.js");
    return unpack(window.TG_DATA);
  }
  async function getChannels() {
    if (CONFIG.mode === "http") {
      const url = CONFIG.httpBase.replace(/\/$/, "") + CONFIG.httpPath;
      const response = await fetch(url);
      if (!response.ok)
        throw new Error(`HTTP ${response.status} при запросе ${url}`);
      return unpack(await response.json());
    }
    if (location.protocol === "file:") {
      if (window.TG_DATA !== undefined) return unpack(window.TG_DATA);
      try {
        await loadScript("./data/channels.js");
      } catch (_) {
        return demo();
      }
      return window.TG_DATA === undefined ? demo() : unpack(window.TG_DATA);
    }
    const response = await fetch("./data/channels.json", { cache: "no-store" });
    if (response.status === 404) return demo();
    if (!response.ok)
      throw new Error(`HTTP ${response.status} при загрузке channels.json`);
    const data = await response.json();
    const rows = unpack(data);
    window.TG_DATA = Array.isArray(data) ? { channels: rows } : data;
    return rows;
  }
  window.Api = { getChannels, CONFIG };
})();
