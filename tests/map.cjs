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
const opacity=context.window.CanvasMap.annotationOpacity;
assert.equal(opacity('category',1),1);
assert.equal(opacity('category',3),0);
assert(opacity('region',2)>.9);
assert.equal(opacity('region',5),0);
assert.equal(opacity('topic',5),0);
assert(opacity('category',1.8)>0&&opacity('category',1.8)<1,'Captions must fade, not pop');
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
const coordinates=map.layout.nodes.map(n=>[n.id,n.x,n.y]);
map.setVisibleIds(new Set(["two"]));
assert.equal(map.hover, null);
assert.equal(map._hitTest(point.x, point.y), null);
map.fitIds(new Set(["two"]));
assert(Number.isFinite(map.view.scale));
assert(map.view.scale > 0);
map.fitIds(new Set());
assert.deepEqual(map.layout.nodes.map(n=>[n.id,n.x,n.y]),coordinates,"Filters and fitting must not move nodes");
assert(Number.isFinite(map.view.x));
{
 const text=[];
 const ctx={save(){},restore(){},measureText(s){return {width:s.length*6};},strokeText(){},fillText(s,x,y){text.push({s,x,y});}};
 map.layout.annotations=[{id:'a',kind:'category',label:'Новости',x:200,y:200,count:10,members:['one']},{id:'b',kind:'category',label:'Город',x:200,y:200,count:8,members:['one']}];
 map._annotations=null;map.visibleIds=null;map._overviewScale=1;map.view={scale:1,x:0,y:0};
 map._drawAnnotations(ctx,600,500);assert.equal(text.length,1,'Screen-space caption collisions must be suppressed');
 text.length=0;map.view.scale=5;map._drawAnnotations(ctx,600,500);assert.equal(text.length,0,'Category captions disappear on close zoom');
 map.view.scale=1;map.visibleIds=new Set(['two']);map._drawAnnotations(ctx,600,500);assert.equal(text.length,0,'Filtered-out categories cannot leave orphan captions');
}
console.log(
  "PASS: responsive canvas centering, point hit test, hidden point exclusion, hover reset, filtered/empty fit.",
);
