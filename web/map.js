/**
 * Canvas-карта с оптимизациями:
 *  — viewport culling: точки вне видимой области не рисуются;
 *  — spatial grid для hit-test (вместо O(n) линейного поиска);
 *  — подписи рисуются только при достаточном зуме и только для
 *    видимых точек.
 *
 * Grid и maxRadius пересобираются в setLayout; видимость (visibleIds)
 * на них не влияет — grid всегда содержит все точки, а фильтрация по
 * visibleIds делается при итерации.
 */
(function () {
  const MIN_SCALE = 0.03;
  const MAX_SCALE = 10;
  const GRID_CELL_SIZE = 50; // world units

  class SpatialGrid {
    constructor(nodes, cellSize) {
      this.cellSize = cellSize;
      this.cells = new Map();
      for (const n of nodes) {
        const key = this._key(n.x, n.y);
        let arr = this.cells.get(key);
        if (!arr) {
          arr = [];
          this.cells.set(key, arr);
        }
        arr.push(n);
      }
    }

    _key(x, y) {
      return (
        Math.floor(x / this.cellSize) + ":" + Math.floor(y / this.cellSize)
      );
    }

    /** Вернуть все точки в радиусе `r` от (x, y) по сетке. */
    query(x, y, r) {
      const result = [];
      const minCx = Math.floor((x - r) / this.cellSize);
      const maxCx = Math.floor((x + r) / this.cellSize);
      const minCy = Math.floor((y - r) / this.cellSize);
      const maxCy = Math.floor((y + r) / this.cellSize);
      for (let cx = minCx; cx <= maxCx; cx++) {
        for (let cy = minCy; cy <= maxCy; cy++) {
          const cell = this.cells.get(cx + ":" + cy);
          if (cell) for (const n of cell) result.push(n);
        }
      }
      return result;
    }
  }

  class CanvasMap {
    constructor(canvas, wrap, opts) {
      opts = opts || {};
      this.canvas = canvas;
      this.wrap = wrap;
      this.onSelect = opts.onSelect || (() => {});
      this.layout = { nodes: [], width: 1, height: 1 };
      this.visibleIds = null;
      this.selectedId = null;

      this._grid = null;
      this._maxRadius = 3;

      this.view = { scale: 1, x: 0, y: 0, ready: false };
      this.drag = { active: false, moved: false, lastX: 0, lastY: 0 };
      this.pointer = { x: 0, y: 0 };
      this.hover = null;
      this.raf = 0;

      this._resize = this._resize.bind(this);
      this._schedule = this._schedule.bind(this);
      this._draw = this._draw.bind(this);

      const ro = new ResizeObserver(this._resize);
      ro.observe(wrap);
      this._ro = ro;

      canvas.addEventListener("wheel", (e) => this._onWheel(e), {
        passive: false,
      });
      canvas.addEventListener("pointerdown", (e) => this._onDown(e));
      canvas.addEventListener("pointermove", (e) => this._onMove(e));
      canvas.addEventListener("pointerup", (e) => this._onUp(e));
      canvas.addEventListener("pointercancel", () => {
        this.drag.active = false;
        this._onLeave();
      });
      canvas.style.touchAction = "none";
      canvas.addEventListener("pointerleave", () => this._onLeave());

      this._resize();
    }

    setLayout(layout) {
      this.layout = layout;
      this.hover = null;
      this.view.ready = false;

      // Пересобрать пространственный индекс
      this._grid = new SpatialGrid(layout.nodes, GRID_CELL_SIZE);
      let maxR = 3;
      for (const n of layout.nodes) {
        if (n.radius > maxR) maxR = n.radius;
      }
      this._maxRadius = maxR;

      this._resize();
    }

    setVisibleIds(set) {
      this.visibleIds = set;
      if (this.hover && !set.has(this.hover.id)) this.hover = null;
      this._schedule();
    }
    setSelected(id) {
      this.selectedId = id;
      const n = this.layout.nodes.find((n) => n.id === id);
      if (n) {
        const r = this.wrap.getBoundingClientRect();
        this.view.scale = Math.max(this.view.scale, 1.4);
        this.view.x =
          r.width * (r.width > 760 ? 0.38 : 0.5) - n.x * this.view.scale;
        this.view.y = r.height * 0.38 - n.y * this.view.scale;
      }
      this._schedule();
    }

    fit() {
      this.view.ready = false;
      this._resize();
    }

    fitVisible() {
      if (
        !this.visibleIds ||
        this.visibleIds.size === this.layout.nodes.length
      ) {
        this.fit();
        return;
      }
      this.fitIds(this.visibleIds);
    }

    fitIds(ids) {
      const nodes = this.layout.nodes.filter((n) => ids.has(n.id));
      if (!nodes.length) {
        this.fit();
        return;
      }
      const r = this.wrap.getBoundingClientRect();
      const left = Math.min(...nodes.map((n) => n.x - n.radius)) - 45;
      const right = Math.max(...nodes.map((n) => n.x + n.radius)) + 45;
      const top = Math.min(...nodes.map((n) => n.y - n.radius)) - 65;
      const bottom = Math.max(...nodes.map((n) => n.y + n.radius)) + 45;
      const scale = Math.max(
        MIN_SCALE,
        Math.min(
          3,
          (r.width - 70) / (right - left),
          (r.height - 240) / (bottom - top),
        ),
      );
      this.view = {
        scale,
        x: r.width / 2 - ((left + right) / 2) * scale,
        y: 140 + (r.height - 240) / 2 - ((top + bottom) / 2) * scale,
        ready: true,
      };
      this.hover = null;
      this._schedule();
    }

    zoomBy(factor) {
      const { width, height } = this.wrap.getBoundingClientRect();
      const next = Math.max(
        MIN_SCALE,
        Math.min(MAX_SCALE, this.view.scale * factor),
      );
      const world = this._toWorld(width / 2, height / 2);
      this.view.scale = next;
      this.view.x = width / 2 - world.x * next;
      this.view.y = height / 2 - world.y * next;
      this._schedule();
    }

    _resize() {
      const r = this.wrap.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      if (
        this.view.ready &&
        this._size &&
        this._size.width > 0 &&
        this._size.height > 0 &&
        r.width > 0 &&
        r.height > 0
      ) {
        const center = this._toWorld(
          this._size.width / 2,
          this._size.height / 2,
        );
        const ratio = Math.min(
          r.width / this._size.width,
          r.height / this._size.height,
        );
        this.view.scale = Math.max(
          MIN_SCALE,
          Math.min(MAX_SCALE, this.view.scale * ratio),
        );
        this.view.x = r.width / 2 - center.x * this.view.scale;
        this.view.y = r.height / 2 - center.y * this.view.scale;
      }
      this._size = { width: r.width, height: r.height };
      this.canvas.width = Math.max(1, Math.floor(r.width * dpr));
      this.canvas.height = Math.max(1, Math.floor(r.height * dpr));
      this.canvas.style.width = r.width + "px";
      this.canvas.style.height = r.height + "px";

      if (!this.view.ready && r.width > 0 && this.layout.nodes.length > 0) {
        const l = this.layout;
        const s = Math.min(r.width / l.width, r.height / l.height) * 0.95;
        this.view.scale = s;
        this.view.x = r.width / 2 - (l.width / 2) * s;
        this.view.y = r.height / 2 - (l.height / 2) * s;
        this.view.ready = true;
      }
      this._schedule();
    }

    _schedule() {
      cancelAnimationFrame(this.raf);
      this.raf = requestAnimationFrame(this._draw);
    }

    _toWorld(sx, sy) {
      return {
        x: (sx - this.view.x) / this.view.scale,
        y: (sy - this.view.y) / this.view.scale,
      };
    }

    _hitTest(sx, sy) {
      const { x, y } = this._toWorld(sx, sy);
      const candidates = this._grid
        ? this._grid.query(
            x,
            y,
            Math.max(this._maxRadius + 3, 9 / this.view.scale),
          )
        : this.layout.nodes;
      let best = null;
      let bestDist2 = Infinity;
      for (const n of candidates) {
        if (this.visibleIds && !this.visibleIds.has(n.id)) continue;
        const r = Math.max(n.radius + 3, 9 / this.view.scale);
        const dx = x - n.x;
        const dy = y - n.y;
        const d2 = dx * dx + dy * dy;
        if (d2 <= r * r && d2 < bestDist2) {
          best = n;
          bestDist2 = d2;
        }
      }
      return best;
    }

    _draw() {
      const ctx = this.canvas.getContext("2d");
      if (!ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const W = this.canvas.width / dpr;
      const H = this.canvas.height / dpr;
      const v = this.view;
      const { nodes } = this.layout;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#111a18";
      ctx.fillRect(0, 0, W, H);

      // Quiet coordinate grid in screen space, anchored to the projection.
      ctx.strokeStyle = "#26382b";
      ctx.lineWidth = 0.5;
      const step = 100 * v.scale;
      if (step > 12) {
        ctx.beginPath();
        for (let x = ((v.x % step) + step) % step; x < W; x += step) {
          ctx.moveTo(x, 0);
          ctx.lineTo(x, H);
        }
        for (let y = ((v.y % step) + step) % step; y < H; y += step) {
          ctx.moveTo(0, y);
          ctx.lineTo(W, y);
        }
        ctx.stroke();
      }

      // Границы видимой области в мировых координатах (+ отступ)
      const margin = (this._maxRadius + 20) / v.scale;
      const worldLeft = -v.x / v.scale - margin;
      const worldTop = -v.y / v.scale - margin;
      const worldRight = (W - v.x) / v.scale + margin;
      const worldBottom = (H - v.y) / v.scale + margin;

      ctx.save();
      ctx.translate(v.x, v.y);
      ctx.scale(v.scale, v.scale);

      const visibleCounts = new Map();
      for (const n of nodes) {
        if (!this.visibleIds || this.visibleIds.has(n.id))
          visibleCounts.set(n.cluster, (visibleCounts.get(n.cluster) || 0) + 1);
      }
      for (const cluster of this.layout.clusters || []) {
        const count = visibleCounts.get(cluster.id) || 0;
        if (!count) continue;
        ctx.fillStyle = _hexA(cluster.color, 0.045);
        ctx.strokeStyle = _hexA(cluster.color, 0.24);
        ctx.lineWidth = 1 / v.scale;
        _roundRect(
          ctx,
          cluster.x,
          cluster.y,
          cluster.width,
          cluster.height,
          18,
        );
        ctx.fill();
        ctx.stroke();
        const fontSize = Math.min(18, 13 / v.scale);
        ctx.font = `500 ${fontSize}px Arial, sans-serif`;
        ctx.textAlign = "left";
        ctx.fillStyle = cluster.color;
        let title = cluster.label;
        const available = cluster.width - 40;
        while (title.length > 1 && ctx.measureText(title).width > available)
          title = title.slice(0, -1);
        if (title !== cluster.label) title = title.slice(0, -1) + "…";
        ctx.fillText(title, cluster.x + 22, cluster.y + 30);
        ctx.font = `${Math.min(13, 10 / v.scale)}px Arial, sans-serif`;
        ctx.fillStyle = "#8fa397";
        ctx.fillText(
          `${count} / ${cluster.count} каналов`,
          cluster.x + 22,
          cluster.y + 51,
        );
      }
      for (const n of nodes) {
        // Viewport culling
        if (
          n.x + n.radius < worldLeft ||
          n.x - n.radius > worldRight ||
          n.y + n.radius < worldTop ||
          n.y - n.radius > worldBottom
        )
          continue;

        const active = !this.visibleIds || this.visibleIds.has(n.id);
        const isSel = n.id === this.selectedId;
        const isHov = this.hover && this.hover.id === n.id;

        ctx.globalAlpha = active ? (isHov || isSel ? 1 : 0.8) : 0.08;

        if (isHov || isSel) {
          ctx.beginPath();
          ctx.fillStyle = _hexA(n.color || "#6b8afd", 0.25);
          ctx.arc(n.x, n.y, n.radius + 5, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.beginPath();
        ctx.fillStyle = n.color || "#6b8afd";
        ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
        ctx.fill();

        if (isSel) {
          ctx.beginPath();
          ctx.lineWidth = 2 / v.scale;
          ctx.strokeStyle = "#ffffff";
          ctx.arc(n.x, n.y, n.radius + 2, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      // Avoid turning dense areas into overlapping labels. Hover always has a tooltip.
      if (v.scale > 2.2) {
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#d8e9dc";
        ctx.font = `${11 / v.scale}px Arial, sans-serif`;
        ctx.textAlign = "center";
        const boxes = [];
        for (const n of nodes) {
          if (this.visibleIds && !this.visibleIds.has(n.id)) continue;
          if (
            n.x < worldLeft ||
            n.x > worldRight ||
            n.y < worldTop ||
            n.y > worldBottom
          )
            continue;
          const text = n.name.slice(0, 25),
            w = ctx.measureText(text).width * v.scale;
          const x = n.x * v.scale + v.x,
            y = (n.y - n.radius) * v.scale + v.y - 10;
          if (
            boxes.some(
              (b) =>
                Math.abs(b.x - x) < (b.w + w) / 2 + 10 &&
                Math.abs(b.y - y) < 17,
            )
          )
            continue;
          boxes.push({ x, y, w });
          ctx.fillText(text, n.x, n.y - n.radius - 10 / v.scale);
        }
      }

      ctx.restore();
      ctx.globalAlpha = 1;

      if (this.hover) this._drawTooltip(ctx, this.hover);
    }

    _drawTooltip(ctx, node) {
      let title = node.name;
      const sub = "@" + node.username;
      const pad = 10;
      let x = this.pointer.x + 14;
      let y = this.pointer.y + 14;

      ctx.save();
      ctx.font = '600 13px "Inter", system-ui, sans-serif';
      const maxWidth = Math.max(80, this.wrap.clientWidth - 40);
      while (title.length > 1 && ctx.measureText(title).width > maxWidth)
        title = title.slice(0, -1);
      if (title !== node.name) title = title.slice(0, -1) + "…";
      const w1 = ctx.measureText(title).width;
      ctx.font = '12px "Inter", system-ui, sans-serif';
      const w2 = ctx.measureText(sub).width;
      const w = Math.max(w1, w2) + pad * 2;
      const h = 46;
      x = Math.max(4, Math.min(x, this.wrap.clientWidth - w - 4));
      y = Math.max(4, Math.min(y, this.wrap.clientHeight - h - 4));

      ctx.shadowColor = "rgba(0,0,0,0.4)";
      ctx.shadowBlur = 14;
      ctx.shadowOffsetY = 4;
      _roundRect(ctx, x, y, w, h, 10);
      ctx.fillStyle = "#1a1a2e";
      ctx.fill();

      ctx.shadowColor = "transparent";
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#2a2a4a";
      _roundRect(ctx, x, y, w, h, 10);
      ctx.stroke();

      ctx.fillStyle = "#ffffff";
      ctx.font = '600 13px "Inter", system-ui, sans-serif';
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(title, x + pad, y + 20);

      ctx.fillStyle = "#8a8fa8";
      ctx.font = '12px "Inter", system-ui, sans-serif';
      ctx.fillText(sub, x + pad, y + 36);
      ctx.restore();
    }

    _onWheel(e) {
      e.preventDefault();
      const r = this.canvas.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      const v = this.view;
      const factor = Math.exp(-e.deltaY * 0.0015);
      const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, v.scale * factor));
      if (next === v.scale) return;

      const wx = (px - v.x) / v.scale;
      const wy = (py - v.y) / v.scale;
      v.scale = next;
      v.x = px - wx * next;
      v.y = py - wy * next;

      this.hover = this._hitTest(px, py);
      this._schedule();
    }

    _onDown(e) {
      const r = this.canvas.getBoundingClientRect();
      this.drag = {
        active: true,
        moved: false,
        lastX: e.clientX - r.left,
        lastY: e.clientY - r.top,
      };
      this.canvas.setPointerCapture(e.pointerId);
      this.canvas.style.cursor = "grabbing";
    }

    _onMove(e) {
      const r = this.canvas.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      this.pointer = { x: px, y: py };

      if (this.drag.active) {
        const dx = px - this.drag.lastX;
        const dy = py - this.drag.lastY;
        if (Math.abs(dx) + Math.abs(dy) > 2) this.drag.moved = true;
        this.drag.lastX = px;
        this.drag.lastY = py;
        this.view.x += dx;
        this.view.y += dy;
        this._schedule();
        return;
      }

      const hit = this._hitTest(px, py);
      const prevId = this.hover && this.hover.id;
      const nextId = hit && hit.id;
      if (prevId !== nextId || hit) {
        this.hover = hit;
        this.canvas.style.cursor = hit ? "pointer" : "grab";
        this._schedule();
      }
    }

    _onUp(e) {
      const wasDrag = this.drag.moved;
      this.drag.active = false;
      if (this.canvas.hasPointerCapture(e.pointerId))
        this.canvas.releasePointerCapture(e.pointerId);
      this.canvas.style.cursor = "grab";
      if (wasDrag) return;
      const r = this.canvas.getBoundingClientRect();
      const hit = this._hitTest(e.clientX - r.left, e.clientY - r.top);
      if (hit) this.onSelect(hit.id);
    }

    _onLeave() {
      this.hover = null;
      this.drag.active = false;
      this._schedule();
    }
  }

  function _hexA(hex, alpha) {
    const h = hex.replace("#", "");
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function _roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  window.CanvasMap = CanvasMap;
})();
