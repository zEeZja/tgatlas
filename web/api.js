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
  function activateSnapshot(data) {
    const info=data?._snapshot;
    const reference=Date.parse(info?.reference_at);
    if(info?.format!=="atlas-public-snapshot-v1"||typeof info.id!=="string"||!Number.isFinite(reference))throw new Error("Повреждён публичный снимок карты");
    const rows=unpack(data);
    if(rows.length!==info.channels)throw new Error("Публичный снимок содержит неполный каталог");
    CONFIG.snapshotReference=reference;
    CONFIG.snapshotInfo=info;
    window.TG_DATA=data;
    return rows;
  }
  async function publishedSnapshot() {
    if(location.protocol==="file:") {
      try { if(window.TG_PUBLISHED_CHANNELS===undefined)await loadScript("./snapshot/channels.js"); }
      catch (_) { return demo(); }
      return activateSnapshot(window.TG_PUBLISHED_CHANNELS);
    }
    const response=await fetch("./snapshot/channels.json",{cache:"no-store"});
    if(response.status===404)return demo();
    if(!response.ok)throw new Error(`HTTP ${response.status} при загрузке публичного снимка`);
    return activateSnapshot(await response.json());
  }
  async function getChannels() {
    CONFIG.snapshotReference=null;
    CONFIG.snapshotInfo=null;
    if (CONFIG.mode === "http") {
      const url = CONFIG.httpBase.replace(/\/$/, "") + CONFIG.httpPath;
      const response = await fetch(url);
      if (!response.ok)
        throw new Error(`HTTP ${response.status} при запросе ${url}`);
      return unpack(await response.json());
    }
    if (location.protocol === "file:") {
      if (window.TG_DATA !== undefined) return window.TG_DATA?._snapshot ? activateSnapshot(window.TG_DATA) : unpack(window.TG_DATA);
      try {
        await loadScript("./data/channels.js");
      } catch (_) {
        return publishedSnapshot();
      }
      return window.TG_DATA === undefined ? publishedSnapshot() : unpack(window.TG_DATA);
    }
    const response = await fetch("./data/channels.json", { cache: "no-store" });
    if (response.status === 404) return publishedSnapshot();
    if (!response.ok)
      throw new Error(`HTTP ${response.status} при загрузке channels.json`);
    const data = await response.json();
    const rows = unpack(data);
    window.TG_DATA = Array.isArray(data) ? { channels: rows } : data;
    return rows;
  }
  async function getLayout() {
    try {
      if(CONFIG.snapshotInfo) {
        let artifact;
        if(location.protocol==="file:") {
          if(window.TG_PUBLISHED_LAYOUT===undefined)await loadScript("./snapshot/layout.js");
          artifact=window.TG_PUBLISHED_LAYOUT;
        } else {
          const response=await fetch("./snapshot/layout.json",{cache:"no-store"});
          if(!response.ok)return null;
          artifact=await response.json();
        }
        return artifact?._snapshot_id===CONFIG.snapshotInfo.id?artifact:null;
      }
      if(location.protocol==="file:") {
        if(window.TG_ATLAS_LAYOUT===undefined)await loadScript("./data/atlas_layout.js");
        return window.TG_ATLAS_LAYOUT||null;
      }
      const response=await fetch("./data/atlas_layout.json",{cache:"no-store"});
      return response.ok?await response.json():null;
    } catch (_) { return null; }
  }
  window.Api = { getChannels, getLayout, CONFIG };
})();
