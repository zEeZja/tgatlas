/* Seeded organic archipelagos: topic → geographic neighbourhood, never a matrix.
 * Distances express curated category/coverage membership, not embedding similarity.
 */
(function () {
  "use strict";
  function nodeRadius(ch) {
    const n = Number(ch.subscribers) || 0;
    return n > 0 ? 4 + 3 * Math.min(1, Math.log10(n) / 7) : 4;
  }
  function hash(s) {
    let h = 2166136261;
    for (const c of String(s)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
    return h >>> 0;
  }
  function random(seed) {
    let a = hash(seed);
    return () => {
      a += 0x6d2b79f5;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function geo(ch) {
    // Explicit nonterritorial coverage must not inherit a legacy stored region.
    if (ch.geography_scope === "nonlocal")
      return "Без территориальной привязки";
    if (ch.geography_scope === "unknown") return "География не уточнена";
    if (ch.geography_regions?.length) return ch.geography_regions.join(" · ");
    if (ch.geography_scope === "country")
      return (
        "Вся страна · " + (ch.geography_country || ch.country || "не уточнена")
      );
    if (ch.geography_scope === "global") return "Международная повестка";
    if (ch.geography_scope) return "География не уточнена";
    return ch.region || "Без региона";
  }
  // Greedy radial packing leaves empty water between independent islands.
  function pack(items, gap) {
    const placed = [];
    for (const item of items) {
      let p = { x: 0, y: 0 };
      if (placed.length) {
        let found = false;
        for (let k = 0; k < 20000; k++) {
          const angle = k * 2.399963229728653;
          const d = 12 * Math.sqrt(k);
          p = { x: Math.cos(angle) * d, y: Math.sin(angle) * d };
          if (
            placed.every(
              (q) =>
                Math.hypot(p.x - q.x, p.y - q.y) >=
                item.radius + q.radius + gap,
            )
          ) {
            found = true;
            break;
          }
        }
        if (!found)
          p = {
            x:
              Math.max(...placed.map((q) => q.x + q.radius)) +
              gap +
              item.radius,
            y: 0,
          };
      }
      Object.assign(item, p);
      placed.push(item);
    }
    return placed;
  }
  function scatter(channels, seed) {
    const list = [...channels].sort(
      (a, b) =>
        hash(a.id) - hash(b.id) || String(a.id).localeCompare(String(b.id)),
    );
    const grid = new Map(),
      nodes = [];
    const cell = 24;
    let bound = Math.max(26, Math.sqrt(list.length) * 16);
    for (const ch of list) {
      const rnd = random(seed + ":" + ch.id);
      let x,
        y,
        r = nodeRadius(ch),
        placed = false;
      for (let k = 0; k < 10000; k++) {
        const angle = rnd() * Math.PI * 2,
          dist = Math.sqrt(rnd()) * bound;
        const warp =
          1 +
          0.13 * Math.sin(angle * 3 + (hash(seed) % 13)) +
          0.08 * Math.cos(angle * 5);
        x = Math.cos(angle) * dist * warp;
        y =
          Math.sin(angle) * dist * (0.82 + 0.12 * Math.sin(hash(seed))) * warp;
        const cx = Math.floor(x / cell),
          cy = Math.floor(y / cell);
        let good = true;
        for (let i = cx - 2; i <= cx + 2 && good; i++)
          for (let j = cy - 2; j <= cy + 2 && good; j++)
            for (const n of grid.get(i + ":" + j) || [])
              if (Math.hypot(x - n.x, y - n.y) < r + n.radius + 10) {
                good = false;
                break;
              }
        if (good) {
          const n = { ...ch, x, y, radius: r };
          nodes.push(n);
          const key = cx + ":" + cy;
          if (!grid.has(key)) grid.set(key, []);
          grid.get(key).push(n);
          placed = true;
          break;
        }
        if (k % 300 === 299) bound *= 1.04;
      }
      if (!placed) throw new Error("Не удалось разместить канал " + ch.id);
    }
    const radius =
      Math.max(30, ...nodes.map((n) => Math.hypot(n.x, n.y) + n.radius)) + 28;
    return { nodes, radius };
  }
  function hull(nodes) {
    const p = nodes
      .map((n) => ({ x: n.x, y: n.y }))
      .sort((a, b) => a.x - b.x || a.y - b.y);
    if (p.length < 3) return p;
    const cross = (o, a, b) =>
      (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    const low = [],
      high = [];
    for (const a of p) {
      while (low.length > 1 && cross(low.at(-2), low.at(-1), a) <= 0) low.pop();
      low.push(a);
    }
    for (const a of [...p].reverse()) {
      while (high.length > 1 && cross(high.at(-2), high.at(-1), a) <= 0)
        high.pop();
      high.push(a);
    }
    return low.slice(0, -1).concat(high.slice(0, -1));
  }
  function placeOnRussia(macros) {
    const centers = window.Topics.taxonomy.region_centers || {};
    const known = macros.filter((m) => centers[m.id]),
      extra = macros.filter((m) => !centers[m.id]);
    // LCC projection anchors; local collision relaxation preserves the continental silhouette.
    for (const m of known) {
      const [x, y] = centers[m.id];
      m.x = m.anchorX = x * 5000;
      m.y = m.anchorY = -y * 5000;
    }
    for (let pass = 0; pass < 260; pass++) {
      for (const m of known)
        if (pass < 100) {
          m.x += (m.anchorX - m.x) * 0.015;
          m.y += (m.anchorY - m.y) * 0.015;
        }
      for (let i = 0; i < known.length; i++)
        for (let j = i + 1; j < known.length; j++) {
          const a = known[i],
            b = known[j];
          let dx = b.x - a.x,
            dy = b.y - a.y,
            d = Math.hypot(dx, dy);
          const min = a.radius + b.radius + 35;
          if (d < min) {
            if (d < 0.001) {
              dx = 0.001;
              dy = 0.001;
              d = Math.hypot(dx, dy);
            }
            const f = ((min - d) / d) * 1.02;
            const massA = a.radius * a.radius,
              massB = b.radius * b.radius;
            const weightA = massB / (massA + massB),
              weightB = 1 - weightA;
            a.x -= dx * f * weightA;
            a.y -= dy * f * weightA;
            b.x += dx * f * weightB;
            b.y += dy * f * weightB;
          }
        }
    }
    let expand = 1;
    for (let i = 0; i < known.length; i++)
      for (let j = i + 1; j < known.length; j++)
        expand = Math.max(
          expand,
          (known[i].radius + known[j].radius + 35) /
            Math.max(
              0.001,
              Math.hypot(known[i].x - known[j].x, known[i].y - known[j].y),
            ),
        );
    for (const m of known) {
      m.x *= expand;
      m.y *= expand;
    }
    if (extra.length) {
      pack(extra, 50);
      const minY = Math.min(...extra.map((m) => m.y - m.radius));
      const y = known.length
        ? Math.max(...known.map((m) => m.y + m.radius)) + 180
        : 0;
      const cx = known.length
        ? known.reduce((n, m) => n + m.x, 0) / known.length
        : 0;
      for (const m of extra) {
        m.x += cx;
        m.y += y - minY;
      }
    }
  }
  function buildClusterLayout(channels, mode = "topic") {
    const groups = new Map();
    for (const ch of channels) {
      const t =
        window.Topics.groupsById?.[ch.topic_group] ||
        window.Topics.byId[ch.subcategory] ||
        window.Topics.byId.other;
      const id =
        mode === "region" ? geo(ch) : ch.topic_group ? "group:" + t.id : t.id;
      if (!groups.has(id))
        groups.set(id, {
          id,
          label: mode === "region" ? id : t.label,
          color: mode === "region" ? "#a6cbb2" : t.color,
          channels: [],
        });
      groups.get(id).channels.push(ch);
    }
    const macros = [...groups.values()].sort(
      (a, b) =>
        b.channels.length - a.channels.length || a.id.localeCompare(b.id),
    );
    for (const macro of macros) {
      const buckets = new Map();
      for (const ch of macro.channels) {
        const key = mode === "region" ? ch.subcategory : geo(ch);
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(ch);
      }
      macro.clouds = [...buckets]
        .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
        .map(([id, list]) => ({
          ...scatter(list, macro.id + ":" + id),
          id,
          label: mode === "region" ? window.Topics.byId[id]?.label || id : id,
        }));
      pack(macro.clouds, 24);
      macro.radius =
        Math.max(
          40,
          ...macro.clouds.map((c) => Math.hypot(c.x, c.y) + c.radius),
        ) + 45;
    }
    if (mode === "region") placeOnRussia(macros);
    else pack(macros, 90);
    const nodes = [],
      clusters = [],
      clouds = [];
    for (const m of macros) {
      const own = [];
      for (const c of m.clouds) {
        const ns = c.nodes.map((n) => ({
          ...n,
          x: n.x + c.x + m.x,
          y: n.y + c.y + m.y,
          cluster: m.id,
          neighbourhood: m.id + ":" + c.id,
          color: window.Topics.colorOf(n),
        }));
        nodes.push(...ns);
        own.push(...ns);
        clouds.push({
          id: m.id + ":" + c.id,
          cluster: m.id,
          label: c.label,
          color: m.color,
          count: ns.length,
          x: m.x + c.x,
          y: m.y + c.y,
          radius: c.radius,
          outline: hull(ns),
          labelY: Math.min(...ns.map((n) => n.y)) - 24,
        });
      }
      clusters.push({
        id: m.id,
        label: m.label,
        color: m.color,
        count: own.length,
        x: m.x - m.radius,
        y: m.y - m.radius,
        width: 2 * m.radius,
        height: 2 * m.radius,
        cx: m.x,
        cy: m.y,
        labelY:
          m.y +
          m.clouds[0].y +
          Math.min(...m.clouds[0].nodes.map((n) => n.y)) -
          65,
      });
    }
    if (!nodes.length)
      return { nodes, clusters, clouds, width: 640, height: 480, mode };
    const left = Math.min(...clusters.map((c) => c.x)) - 80,
      top = Math.min(...clusters.map((c) => c.y)) - 160;
    for (const n of nodes) {
      n.x -= left;
      n.y -= top;
    }
    for (const c of clusters) {
      c.x -= left;
      c.y -= top;
      c.cx -= left;
      c.cy -= top;
      c.labelY -= top;
    }
    for (const c of clouds) {
      c.x -= left;
      c.y -= top;
      c.labelY -= top;
      for (const p of c.outline) {
        p.x -= left;
        p.y -= top;
      }
    }
    return {
      nodes,
      clusters,
      clouds,
      width: Math.max(640, ...clusters.map((c) => c.x + c.width + 80)),
      height: Math.max(480, ...clusters.map((c) => c.y + c.height + 100)),
      mode,
    };
  }
  window.Layout = { buildClusterLayout, nodeRadius, geographyGroup: geo };
})();
