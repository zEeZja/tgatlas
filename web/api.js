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
    return rows;
  }
  async function demo() {
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "./demo.js";
      script.onload = resolve;
      script.onerror = () =>
        reject(new Error("Данные и демонстрационный каталог не найдены"));
      document.head.appendChild(script);
    });
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
    if (window.TG_DATA !== undefined) return unpack(window.TG_DATA);
    if (location.protocol === "file:") return demo();
    const response = await fetch("./data/channels.json");
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
