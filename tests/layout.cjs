const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const root = path.join(__dirname, "..");
const context = { window: {}, localStorage: { getItem: () => null }, console };
vm.createContext(context);
for (const name of ["taxonomy", "topics", "layout"])
  vm.runInContext(
    fs.readFileSync(path.join(root, "web", name + ".js"), "utf8"),
    context,
  );
const { Topics, Layout } = context.window;
const channels = Array.from({ length: 2200 }, (_, i) =>
  Topics.enrich({
    id: `id_${i}`,
    username: `id_${i}`,
    name: `Канал ${i}`,
    subcategory: i < 1500 ? "general" : i % 2 ? "politics" : "tech",
    region: i < 1500 ? "Москва" : null,
    channel_type: i % 3 ? "regional" : "federal",
    subscribers: 10 ** (i % 8),
  }),
);
function validate(layout) {
  assert.equal(layout.nodes.length, channels.length);
  assert.equal(new Set(layout.nodes.map((n) => n.id)).size, channels.length);
  assert(layout.clouds.length > 0);
  assert(
    new Set(layout.nodes.map((n) => n.x)).size > layout.nodes.length * 0.9,
    "Clouds must not form aligned columns",
  );
  assert(
    new Set(layout.nodes.map((n) => n.y)).size > layout.nodes.length * 0.9,
    "Clouds must not form aligned rows",
  );
  const tiles = new Map(layout.clusters.map((c) => [c.id, c]));
  for (const n of layout.nodes) {
    const t = tiles.get(n.cluster);
    assert(n.x - n.radius >= t.x && n.x + n.radius <= t.x + t.width);
    assert(n.y - n.radius >= t.y && n.y + n.radius <= t.y + t.height);
  }
  for (let i = 0; i < layout.nodes.length; i++)
    for (let j = i + 1; j < layout.nodes.length; j++) {
      const a = layout.nodes[i],
        b = layout.nodes[j];
      assert(
        Math.hypot(a.x - b.x, a.y - b.y) >= a.radius + b.radius + 8,
        `Collision: ${a.id}/${b.id}`,
      );
    }
}
for (const mode of ["topic", "region"]) {
  const layout = Layout.buildClusterLayout(channels, mode);
  validate(layout);
  assert.equal(
    JSON.stringify(layout),
    JSON.stringify(Layout.buildClusterLayout([...channels].reverse(), mode)),
  );
}
const regionalRows = [
  "Москва",
  "Омская область",
  "Новосибирская область",
  "Хабаровский край",
].map((region, i) =>
  Topics.enrich({
    id: "geo" + i,
    username: "geo" + i,
    name: "Новости",
    subcategory: "general",
    region,
  }),
);
const geographic = Layout.buildClusterLayout(regionalRows, "region");
const centers = regionalRows.map((r) =>
  geographic.clusters.find((c) => c.id === r.region),
);
for (let i = 0; i < centers.length - 1; i++)
  assert(
    centers[i].cx < centers[i + 1].cx,
    "Regional layout must preserve west-to-east order",
  );
assert(
  new Set(centers.map((c) => Math.round(c.cy))).size > 2,
  "Regions must not form one eastward row",
);
const empty = Layout.buildClusterLayout([]);
assert.equal(empty.nodes.length, 0);
assert(Number.isFinite(empty.width));
const one = Layout.buildClusterLayout([channels[0]]);
assert.equal(one.nodes.length, 1);
console.log(
  "PASS: 2,200 nodes, dense Moscow, both grouping modes, no overlaps, deterministic order, empty and single catalogues.",
);
