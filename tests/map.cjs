const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const size = { width: 1200, height: 900 };
const canvas = {
  style: {},
  addEventListener() {},
  getBoundingClientRect() {
    return { ...size, left: 0, top: 0 };
  },
};
const wrap = {
  getBoundingClientRect() {
    return { ...size };
  },
};
const context = {
  window: { devicePixelRatio: 1 },
  ResizeObserver: class {
    observe() {}
  },
  requestAnimationFrame() {
    return 1;
  },
  cancelAnimationFrame() {},
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(__dirname, "../web/map.js"), "utf8"),
  context,
);
const map = new context.window.CanvasMap(canvas, wrap);
map.setLayout({
  width: 1200,
  height: 900,
  clusters: [],
  nodes: [
    { id: "one", x: 200, y: 200, radius: 7 },
    { id: "two", x: 300, y: 300, radius: 7 },
  ],
});
let center = map._toWorld(size.width / 2, size.height / 2);
size.width = 390;
size.height = 782;
map._resize();
let next = map._toWorld(size.width / 2, size.height / 2);
assert(
  Math.abs(next.x - center.x) < 0.000001 &&
    Math.abs(next.y - center.y) < 0.000001,
  "Resize must preserve world center",
);
map.setSelected("one");
const point = {
  x: 200 * map.view.scale + map.view.x,
  y: 200 * map.view.scale + map.view.y,
};
assert.equal(map._hitTest(point.x, point.y).id, "one");
map.hover = map.layout.nodes[0];
map.setVisibleIds(new Set(["two"]));
assert.equal(map.hover, null);
assert.equal(map._hitTest(point.x, point.y), null);
map.fitIds(new Set(["two"]));
assert(Number.isFinite(map.view.scale));
assert(map.view.scale > 0);
map.fitIds(new Set());
assert(Number.isFinite(map.view.x));
console.log(
  "PASS: responsive canvas centering, point hit test, hidden point exclusion, hover reset, filtered/empty fit.",
);
