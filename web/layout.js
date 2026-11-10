/* Deterministic, collision-free clusters. Filters never move individual channels. */
(function () {
  "use strict";
  const GAP = 28,
    PAD = 28,
    HEADER = 70,
    TILE_GAP = 42;
  function nodeRadius(channel) {
    const subscribers = Number(channel.subscribers) || 0;
    return subscribers > 0
      ? 4 + 3 * Math.min(1, Math.log10(subscribers) / 7)
      : 4;
  }
  function hash(value) {
    let h = 2166136261;
    for (const char of String(value))
      h = Math.imul(h ^ char.charCodeAt(0), 16777619);
    return h >>> 0;
  }
  function buildClusterLayout(channels, mode = "topic") {
    const groups = new Map();
    for (const ch of channels) {
      const topic =
        window.Topics.byId[ch.subcategory] || window.Topics.byId.other;
      const key =
        mode === "region"
          ? ch.region ||
            (ch.channel_type === "federal" ? "Федеральные" : "Без региона")
          : topic.id;
      if (!groups.has(key))
        groups.set(key, {
          id: key,
          label: mode === "region" ? key : topic.label,
          color: mode === "region" ? "#a6cbb2" : topic.color,
          channels: [],
        });
      groups.get(key).channels.push(ch);
    }
    const clusters = [...groups.values()].sort(
      (a, b) =>
        b.channels.length - a.channels.length || a.id.localeCompare(b.id),
    );
    const tiles = [],
      nodes = [];
    let width = 0,
      height = 0;
    for (const group of clusters) {
      const count = group.channels.length;
      const cols = Math.max(1, Math.ceil(Math.sqrt(count * 1.3)));
      const rows = Math.ceil(count / cols);
      const tileWidth = Math.max(248, (cols - 1) * GAP + PAD * 2);
      const tileHeight = Math.max(138, (rows - 1) * GAP + PAD * 2 + HEADER);
      const candidates = [{ x: 0, y: 0 }];
      for (const t of tiles)
        candidates.push(
          { x: t.x + t.width + TILE_GAP, y: t.y },
          { x: t.x, y: t.y + t.height + TILE_GAP },
        );
      const allowed = candidates.filter((p) =>
        tiles.every(
          (t) =>
            p.x + tileWidth + TILE_GAP <= t.x ||
            p.x >= t.x + t.width + TILE_GAP ||
            p.y + tileHeight + TILE_GAP <= t.y ||
            p.y >= t.y + t.height + TILE_GAP,
        ),
      );
      const cost = (p) => {
        const w = Math.max(width, p.x + tileWidth),
          h = Math.max(height, p.y + tileHeight);
        return w * h + 0.4 * (w - h * 1.35) ** 2;
      };
      allowed.sort((a, b) => cost(a) - cost(b) || a.y - b.y || a.x - b.x);
      const place = allowed[0];
      const tile = {
        id: group.id,
        label: group.label,
        color: group.color,
        count,
        x: place.x,
        y: place.y,
        width: tileWidth,
        height: tileHeight,
      };
      tiles.push(tile);
      width = Math.max(width, tile.x + tileWidth);
      height = Math.max(height, tile.y + tileHeight);
      const sorted = group.channels.sort(
        (a, b) =>
          hash(a.id) - hash(b.id) || String(a.id).localeCompare(String(b.id)),
      );
      for (let i = 0; i < count; i++) {
        const ch = sorted[i],
          row = Math.floor(i / cols),
          col = i % cols;
        nodes.push({
          ...ch,
          cluster: group.id,
          color: window.Topics.byId[ch.subcategory]?.color || "#83968c",
          x: tile.x + (tileWidth - (cols - 1) * GAP) / 2 + col * GAP,
          y: tile.y + HEADER + PAD + row * GAP,
          radius: nodeRadius(ch),
        });
      }
    }
    // World-space margins reserve room for the fixed map title/footer.
    for (const n of nodes) {
      n.x += 60;
      n.y += 140;
    }
    for (const t of tiles) {
      t.x += 60;
      t.y += 140;
    }
    return {
      nodes,
      clusters: tiles,
      width: Math.max(640, width + 120),
      height: Math.max(480, height + 240),
      mode,
    };
  }
  window.Layout = { buildClusterLayout, nodeRadius };
})();
