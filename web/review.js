/* Bundled into the standalone owner review page. No network or backend writes. */
const rows = payload.records;
let index = 0;
const storageKey =
  "atlas-review-" +
  rows
    .map((r) => r.group_id)
    .join("")
    .slice(0, 120);
const el = (id) => document.getElementById(id);
const byTopic = Object.fromEntries(payload.topics.map((t) => [t.id, t]));
const editable = (k) =>
  k.startsWith("expected_") ||
  [
    "note",
    "reviewed",
    "review_reason",
    "proposed_topic",
    "review_schema_version",
    "taxonomy_version",
  ].includes(k);
function adopt(target, saved) {
  for (const key of payload.fields)
    if (editable(key) && saved[key] !== undefined) target[key] = saved[key];
  target.review_schema_version = saved.review_schema_version || "1";
  target.taxonomy_version = saved.taxonomy_version || "5";
  if (!byTopic[target.expected_topic]) {
    target.proposed_topic = target.expected_topic;
    target.expected_topic = "other";
    target.review_reason = "missing_topic";
    target.reviewed = "no";
  }
  target.expected_group =
    target.expected_topic !== "other"
      ? byTopic[target.expected_topic].group
      : target.expected_group || "other_group";
}
try {
  const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
  if (
    Array.isArray(saved) &&
    saved.length === rows.length &&
    saved.every(
      (r, i) =>
        r.username === rows[i].username && r.group_id === rows[i].group_id,
    )
  )
    saved.forEach((r, i) => adopt(rows[i], r));
} catch (e) {}
function options(node, items, selected) {
  node.replaceChildren();
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = item.label;
    opt.selected = selected.includes(item.id);
    node.appendChild(opt);
  }
}
function show() {
  const row = rows[index];
  if (!row) {
    el("progress").textContent = "Нет каналов";
    return;
  }
  el("name").textContent = row.name;
  el("channel").textContent = "@" + row.username;
  el("channel").href = "https://t.me/" + encodeURIComponent(row.username);
  el("description").textContent = row.description;
  options(el("topic-group"), payload.groups, [row.expected_group]);
  options(el("topic"), payload.topics, [row.expected_topic]);
  options(
    el("tags"),
    payload.topics.filter(
      (t) => t.id !== "other" && t.id !== row.expected_topic,
    ),
    row.expected_secondary_topics.split(","),
  );
  options(
    el("formats"),
    payload.formats,
    (row.expected_formats || "").split(","),
  );
  options(el("scope"), payload.scopes, [row.expected_scope]);
  el("regions").value = row.expected_regions;
  el("country").value = row.expected_country;
  el("note").value = row.note;
  el("reason").value = row.review_reason || "";
  el("proposed-topic").value = row.proposed_topic || "";
  el("reviewed").checked = row.reviewed === "yes";
  el("regions").disabled = !["region", "multi_region"].includes(
    row.expected_scope,
  );
  el("country").disabled = row.expected_scope === "nonlocal";
  el("progress").textContent =
    `${index + 1} / ${rows.length} · проверено ${rows.filter((r) => r.reviewed === "yes").length}`;
  el("previous").disabled = index === 0;
  el("next").disabled = index === rows.length - 1;
}
function save(event) {
  const row = rows[index];
  let topic = el("topic").value,
    group = el("topic-group").value;
  if (event?.target.id === "topic-group" && byTopic[topic].group !== group)
    topic = "other";
  else if (topic !== "other") group = byTopic[topic].group;
  row.expected_topic = topic;
  row.expected_group = group;
  row.expected_scope = el("scope").value;
  row.expected_regions = el("regions").value.trim();
  row.expected_country = el("country").value.trim().toUpperCase();
  row.expected_secondary_topics = [...el("tags").selectedOptions]
    .map((i) => i.value)
    .filter((t) => t !== topic)
    .sort()
    .join(",");
  row.expected_formats = [...el("formats").selectedOptions]
    .map((i) => i.value)
    .sort()
    .join(",");
  row.note = el("note").value;
  row.review_reason = el("reason").value;
  row.proposed_topic = el("proposed-topic").value;
  row.reviewed = el("reviewed").checked ? "yes" : "no";
  if (event?.target) {
    row.review_schema_version = "2";
    row.taxonomy_version = String(payload.taxonomyVersion);
  }
  if (["country", "global", "unknown", "nonlocal"].includes(row.expected_scope))
    row.expected_regions = "";
  if (row.expected_scope === "nonlocal") row.expected_country = "";
  try {
    localStorage.setItem(storageKey, JSON.stringify(rows));
    el("status").textContent =
      "Сохранено в этом браузере. Для передачи скачайте CSV.";
  } catch (e) {
    el("status").textContent =
      "Скачайте CSV: сохранение в браузере недоступно.";
  }
  show();
}
for (const id of [
  "topic",
  "topic-group",
  "tags",
  "formats",
  "scope",
  "regions",
  "country",
  "note",
  "reason",
  "proposed-topic",
  "reviewed",
])
  el(id).onchange = save;
el("previous").onclick = () => {
  if (index > 0) {
    index--;
    show();
  }
};
el("next").onclick = () => {
  if (index < rows.length - 1) {
    index++;
    show();
  }
};
el("next-unreviewed").onclick = () => {
  for (let n = 1; n <= rows.length; n++) {
    const next = (index + n) % rows.length;
    if (rows[next].reviewed !== "yes") {
      index = next;
      show();
      return;
    }
  }
};
function cell(value) {
  let s = String(value ?? "");
  if (/^\s*[=+@-]/.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"', '""') + '"';
}
el("download").onclick = () => {
  save();
  const content =
    "\ufeff" +
    [
      payload.fields.map(cell).join(";"),
      ...rows.map((row) => payload.fields.map((k) => cell(row[k])).join(";")),
    ].join("\r\n");
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/csv;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "classification-reviewed.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
function parseCSV(text) {
  const table = [];
  let row = [],
    value = "",
    quoted = false;
  text = text.replace(/^\ufeff/, "");
  for (let n = 0; n < text.length; n++) {
    const c = text[n];
    if (c === '"') {
      if (quoted && text[n + 1] === '"') {
        value += '"';
        n++;
      } else quoted = !quoted;
    } else if (c === ";" && !quoted) {
      row.push(value);
      value = "";
    } else if ((c === "\n" || c === "\r") && !quoted) {
      if (c === "\r" && text[n + 1] === "\n") n++;
      row.push(value);
      if (row.some(Boolean)) table.push(row);
      row = [];
      value = "";
    } else value += c;
  }
  if (quoted) throw Error("Незакрытая кавычка в CSV");
  row.push(value);
  if (row.some(Boolean)) table.push(row);
  const headers = table.shift() || [];
  if (
    !["username", "expected_topic", "expected_scope", "reviewed"].every((k) =>
      headers.includes(k),
    )
  )
    throw Error("Это не файл разметки TG Atlas");
  return table.map((values) => {
    if (values.length !== headers.length) throw Error("Повреждена строка CSV");
    return Object.fromEntries(headers.map((h, i) => [h, values[i]]));
  });
}
el("import").onchange = async () => {
  try {
    const file = el("import").files[0];
    if (!file) return;
    if (file.size > 5000000) throw Error("CSV больше 5 МиБ");
    const imported = parseCSV(await file.text()),
      seen = new Set();
    for (const row of imported) {
      const id = row.username.toLowerCase().replace(/^@/, "");
      if (seen.has(id)) throw Error("Повторный username в CSV");
      seen.add(id);
    }
    let matched = 0,
      stale = 0;
    for (const saved of imported) {
      const id = saved.username.toLowerCase().replace(/^@/, "");
      const target = rows.find((r) => r.username.toLowerCase() === id);
      if (!target) continue;
      adopt(target, saved);
      matched++;
      if (saved.group_id && saved.group_id !== target.group_id) {
        target.reviewed = "no";
        target.review_reason = "profile_changed";
        stale++;
      }
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify(rows));
    } catch (e) {}
    show();
    el("import-status").textContent =
      `Загружено ${matched} каналов; изменившийся профиль требует проверки: ${stale}.`;
  } catch (e) {
    el("import-status").textContent = e.message;
  }
};
show();
