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
  const ramp=(a,b,x)=>{const t=Math.max(0,Math.min(1,(x-a)/(b-a)));return t*t*(3-2*t);};
  function annotationOpacity(kind, zoom) {
    if(kind==="category")return 1-ramp(1.35,2.35,zoom);
    if(kind==="region"||kind==="country")return ramp(1.1,1.65,zoom)*(1-ramp(3,4.6,zoom));
    return ramp(1.45,2,zoom)*(1-ramp(2.8,4,zoom));
  }

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
      this.showRelief = true;

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
      this._annotations=null;
      this.hover = null;
      this.view.ready = false;

      // Пересобрать пространственный индекс
      this._grid = new SpatialGrid(layout.nodes, GRID_CELL_SIZE);
      let maxR = 3;
      for (const n of layout.nodes) {
        if (n.radius > maxR) maxR = n.radius;
      }
      this._maxRadius = maxR;
      const r=this.wrap.getBoundingClientRect();
      this._overviewScale=Math.min(r.width/layout.width,r.height/layout.height)*.95 || 1;

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
      this._overviewScale=Math.min(r.width/this.layout.width,r.height/this.layout.height)*.95 || 1;
      const dpr = Math.min(window.devicePixelRatio || 1,2);
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
      const dpr = Math.min(window.devicePixelRatio || 1,2);
      const W = this.canvas.width / dpr;
      const H = this.canvas.height / dpr;
      const v = this.view;
      const { nodes } = this.layout;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#0c1916";
      ctx.fillRect(0, 0, W, H);

      // Границы видимой области в мировых координатах (+ отступ)
      const margin = (this._maxRadius + 20) / v.scale;
      const worldLeft = -v.x / v.scale - margin;
      const worldTop = -v.y / v.scale - margin;
      const worldRight = (W - v.x) / v.scale + margin;
      const worldBottom = (H - v.y) / v.scale + margin;

      ctx.save();
      ctx.translate(v.x, v.y);
      ctx.scale(v.scale, v.scale);

      const activeClouds = new Set();
      for (const n of nodes) {
        if (!this.visibleIds || this.visibleIds.has(n.id)) {
          activeClouds.add(n.neighbourhood);
        }
      }
      // Density hints only: semantic clusters are not administrative borders.
      for (const cloud of this.layout.clouds || []) {
        if (!this.showRelief) continue;
        if (!activeClouds.has(cloud.id)) continue;
        if (
          cloud.x + cloud.radius < worldLeft ||
          cloud.x - cloud.radius > worldRight ||
          cloud.y + cloud.radius < worldTop ||
          cloud.y - cloud.radius > worldBottom
        )
          continue;
        const halo = ctx.createRadialGradient(
          cloud.x,
          cloud.y,
          0,
          cloud.x,
          cloud.y,
          cloud.radius,
        );
        halo.addColorStop(0, _hexA(cloud.color, 0.045));
        halo.addColorStop(1, _hexA(cloud.color, 0));
        ctx.fillStyle = halo;
        ctx.fillRect(
          cloud.x - cloud.radius,
          cloud.y - cloud.radius,
          cloud.radius * 2,
          cloud.radius * 2,
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
        const drawRadius = Math.max(.7,Math.min(2.8,n.radius*v.scale*.48))/v.scale;
        const isSel = n.id === this.selectedId;
        const isHov = this.hover && this.hover.id === n.id;

        ctx.globalAlpha = active ? (isHov || isSel ? 1 : 0.72) : 0.045;

        if (isHov || isSel) {
          ctx.beginPath();
          ctx.fillStyle = _hexA(n.color || "#6b8afd", 0.25);
          ctx.arc(n.x, n.y, drawRadius + 5/v.scale, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.beginPath();
        ctx.fillStyle = n.color || "#6b8afd";
        ctx.arc(n.x, n.y, drawRadius, 0, Math.PI * 2);
        ctx.fill();

        if (isSel) {
          ctx.beginPath();
          ctx.lineWidth = 2 / v.scale;
          ctx.strokeStyle = "#ffffff";
          ctx.arc(n.x, n.y, drawRadius + 3/v.scale, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      // Avoid turning dense areas into overlapping labels. Hover always has a tooltip.
      if (v.scale / (this._overviewScale||1) > 5 && v.scale > .8) {
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

      this._drawAnnotations(ctx,W,H);

      if (this.hover) this._drawTooltip(ctx, this.hover);
    }

    _drawAnnotations(ctx,W,H) {
      const v=this.view,zoom=v.scale/(this._overviewScale||1);
      const nodes=this.layout.nodes;
      const visible=this.visibleIds;
      const annotations=this._annotations || (this._annotations=this.layout.annotations || [
        ...(this.layout.clusters||[]).map(c=>({id:c.id,kind:"category",label:c.label,x:c.labelX??c.cx,y:c.cy,count:c.count,members:nodes.filter(n=>n.cluster===c.id).map(n=>n.id)})),
        ...(this.layout.clouds||[]).map(c=>({id:c.id,kind:"topic",label:c.label,x:c.x,y:c.y,count:c.count,members:nodes.filter(n=>n.neighbourhood===c.id).map(n=>n.id)}))
      ]);
      const boxes=[];
      // Regions take precedence when they appear; never a coloured headline
      // spanning a whole island. All collision tests are in CSS screen pixels.
      const priority={region:0,country:0,category:1,topic:2};
      const candidates=[...annotations].sort((a,b)=>(priority[a.kind]??2)-(priority[b.kind]??2)||(b.count||0)-(a.count||0)||a.id.localeCompare(b.id));
      ctx.save();
      ctx.textAlign="center";ctx.textBaseline="middle";
      for(const item of candidates) {
        const opacity=annotationOpacity(item.kind,zoom);
        if(opacity<.08||visible&&!(item.members||[]).some(id=>visible.has(id)))continue;
        const x=item.x*v.scale+v.x,y=item.y*v.scale+v.y;
        const size=item.kind==="category"?12:11;
        ctx.font=`600 ${size}px system-ui, sans-serif`;
        const maxWidth=Math.min(190,W*.36);
        const words=String(item.label).split(/\s+/),lines=[];
        let line="";
        for(const word of words) {
          const next=line?line+" "+word:word;
          if(line&&ctx.measureText(next).width>maxWidth){lines.push(line);line=word;}else line=next;
        }
        if(line)lines.push(line);
        if(lines.length>2){lines.length=2;lines[1]=lines[1].replace(/[,.·]$/,'')+"…";}
        const w=Math.max(...lines.map(l=>ctx.measureText(l).width)),h=lines.length*16;
        if(x-w/2<12||x+w/2>W-12||y-h/2<16||y+h/2>H-16)continue;
        if(boxes.some(b=>Math.abs(b.x-x)<(b.w+w)/2+18&&Math.abs(b.y-y)<(b.h+h)/2+14))continue;
        boxes.push({x,y,w,h});
        ctx.globalAlpha=opacity;
        ctx.lineJoin="round";ctx.lineWidth=3.5;ctx.strokeStyle="#0d1417";
        ctx.fillStyle=item.kind==="region"||item.kind==="country"?"#b6cad1":"#d5dddf";
        for(let i=0;i<lines.length;i++) {
          const ly=y+(i-(lines.length-1)/2)*16;
          ctx.strokeText(lines[i],x,ly);ctx.fillText(lines[i],x,ly);
        }
      }
      ctx.restore();
    }

    _drawTooltip(ctx, node) {
      let title = node.name;
      const sub = "@" + node.username;
      const role=this.layout.geographyVisible===false?null:node.layout_geography;
      let location=role?.label ? role.label+(role.provisional?" · предварительно":"") : "";
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
      while(location.length>1&&ctx.measureText(location).width>maxWidth)location=location.slice(0,-2)+"…";
      const w = Math.max(w1, w2,ctx.measureText(location).width) + pad * 2;
      const h = location?64:46;
      x = Math.max(4, Math.min(x, this.wrap.clientWidth - w - 4));
      y = Math.max(4, Math.min(y, this.wrap.clientHeight - h - 4));

      ctx.shadowColor = "rgba(0,0,0,0.4)";
      ctx.shadowBlur = 14;
      ctx.shadowOffsetY = 4;
      _roundRect(ctx, x, y, w, h, 10);
      ctx.fillStyle = "#172124";
      ctx.fill();

      ctx.shadowColor = "transparent";
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#34434c";
      _roundRect(ctx, x, y, w, h, 10);
      ctx.stroke();

      ctx.fillStyle = "#ffffff";
      ctx.font = '600 13px "Inter", system-ui, sans-serif';
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(title, x + pad, y + 20);

      ctx.fillStyle = "#9cb2bc";
      ctx.font = '12px "Inter", system-ui, sans-serif';
      ctx.fillText(sub, x + pad, y + 36);
      if(location)ctx.fillText(location,x+pad,y+53);
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

  CanvasMap.annotationOpacity=annotationOpacity;
  window.CanvasMap = CanvasMap;
})();
