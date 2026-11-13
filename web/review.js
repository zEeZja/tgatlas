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
     "topic_reviewed",
     "geography_reviewed",
     "formats_reviewed",
  ].includes(k);
function adopt(target, saved) {
  for (const key of payload.fields)
    if (editable(key) && saved[key] !== undefined) target[key] = saved[key];
  target.review_schema_version = saved.review_schema_version || "1";
  target.taxonomy_version = saved.taxonomy_version || "5";
  const legacy = saved.review_schema_version !== "3" && saved.reviewed === "yes";
  for (const name of ["topic", "geography"])
    target[name + "_reviewed"] = saved[name + "_reviewed"] || (legacy ? "yes" : "no");
  target.formats_reviewed = saved.formats_reviewed || (saved.review_schema_version === "2" && legacy ? "yes" : "no");
  if (!byTopic[target.expected_topic]) {
    target.proposed_topic = target.expected_topic;
    target.expected_topic = "other";
    target.review_reason = "missing_topic";
    target.reviewed = "no";
    target.topic_reviewed = "no";
  }
  target.expected_group =
    target.expected_topic !== "other"
      ? byTopic[target.expected_topic].group
      : saved.expected_group || "other_group";
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
  const current = row.current_suggestion;
  el("new-suggestion").textContent = current ? `Предложение правил V${payload.taxonomyVersion}: ${byTopic[current.expected_topic]?.label || current.expected_topic} · ${payload.scopes.find(s => s.id === current.expected_scope)?.label || current.expected_scope}. Это не подтверждённый ответ.` : "";
  options(el("topic-group"), payload.groups, [row.expected_group]);
  options(el("topic"), payload.topics, [row.expected_topic]);
  el("topic-help").textContent = byTopic[row.expected_topic]?.definition || "Можно подтвердить только крупную группу, не выбирая случайную подтему.";
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
  el("topic-reviewed").checked = row.topic_reviewed === "yes";
  el("geography-reviewed").checked = row.geography_reviewed === "yes";
  el("reviewed").checked = row.topic_reviewed === "yes" && row.geography_reviewed === "yes";
  el("regions").disabled = !["region", "multi_region"].includes(
    row.expected_scope,
  );
  el("country").disabled = row.expected_scope === "nonlocal";
  el("scope-help").textContent = {
    nonlocal: "Регион не требуется: предмет канала не ограничен территорией. Это полноценная классификация, не неопределённость.",
    country: "Выберите страну по направленности содержания. Русский язык сам по себе не означает Россию.",
    unknown: "Не хватает сведений или есть противоречие. Не выбирайте это для заведомо нетерриториального канала.",
    global: "Международная повестка, а не просто отсутствие города.",
    region: "Один субъект или однозначно определённый город. Биография автора и разовая новость не считаются охватом.",
    multi_region: "Несколько подтверждённых регионов, разделённых символом |.",
  }[row.expected_scope] || "";
  const problems = validation(row);
  el("validation").textContent = problems.join(" ");
  el("progress").textContent =
    `${index + 1} / ${rows.length} · проверено ${rows.filter((r) => r.topic_reviewed === "yes" && r.geography_reviewed === "yes").length} · тем: ${rows.filter(r => r.topic_reviewed === "yes").length} · география: ${rows.filter(r => r.geography_reviewed === "yes").length}`;
  el("previous").disabled = index === 0;
  el("next").disabled = index === rows.length - 1;
}
function validation(row, dimension = "all") {
  const errors = [];
  if (dimension !== "geography" && row.topic_reviewed === "yes") {
    if (!byTopic[row.expected_topic] || (row.expected_topic !== "other" && byTopic[row.expected_topic].group !== row.expected_group))
      errors.push("Тема не соответствует выбранной группе.");
  }
  if (dimension !== "topic" && row.geography_reviewed === "yes") {
    const regions = [...new Set(row.expected_regions.split("|").map(x => x.trim()).filter(Boolean))];
    if (!payload.scopes.some(s => s.id === row.expected_scope)) errors.push("Выберите корректную направленность.");
    if (row.expected_scope === "country" && !/^[A-Z]{2}$/.test(row.expected_country))
      errors.push("Для страновой направленности укажите двухбуквенный код страны, например RU. Или выберите «Без территориальной привязки».");
    if (row.expected_country && !/^[A-Z]{2}$/.test(row.expected_country)) errors.push("Код страны: две латинские буквы.");
    if (row.expected_scope === "region" && regions.length !== 1) errors.push("Нужен один регион.");
    if (row.expected_scope === "multi_region" && regions.length < 2) errors.push("Нужны минимум два региона.");
    if (!["region", "multi_region"].includes(row.expected_scope) && regions.length) errors.push("Для этой направленности регионы должны быть пустыми. Измените направленность явно, чтобы очистить их.");
  }
  return errors;
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
  const id = event?.target?.id;
  row.topic_reviewed = el("topic-reviewed").checked ? "yes" : "no";
  row.geography_reviewed = el("geography-reviewed").checked ? "yes" : "no";
  if (id === "reviewed") {
    row.topic_reviewed = row.geography_reviewed = el("reviewed").checked ? "yes" : "no";
    row.formats_reviewed = row.topic_reviewed;
  } else if (id === "topic-reviewed") row.formats_reviewed = row.topic_reviewed;
  else if (["topic", "topic-group", "tags", "formats"].includes(id)) {
    row.topic_reviewed = "no";
    row.formats_reviewed = "no";
  } else if (["scope", "regions", "country"].includes(id)) row.geography_reviewed = "no";
  if (id && !["note", "reason", "proposed-topic"].includes(id)) {
    row.review_schema_version = "3";
    row.taxonomy_version = String(payload.taxonomyVersion);
  }
  if (id === "scope" && ["country", "global", "unknown", "nonlocal"].includes(row.expected_scope))
    row.expected_regions = "";
  if (id === "scope" && row.expected_scope === "nonlocal") row.expected_country = "";
  let blocked = false;
  for (const dimension of ["topic", "geography"])
    if (validation(row, dimension).length && row[dimension + "_reviewed"] === "yes" && id) {
      row[dimension + "_reviewed"] = "no";
      blocked = true;
    }
  row.reviewed = row.topic_reviewed === "yes" && row.geography_reviewed === "yes" ? "yes" : "no";
  try {
    localStorage.setItem(storageKey, JSON.stringify(rows));
    el("status").textContent =
      "Сохранено в этом браузере. Для передачи скачайте CSV.";
  } catch (e) {
    el("status").textContent =
      "Скачайте CSV: сохранение в браузере недоступно.";
  }
  show();
  if (blocked) el("validation").textContent = "Подтверждение не принято: проверьте страну, регионы и соответствие темы группе. Другая проверенная часть сохранена.";
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
  "topic-reviewed",
  "geography-reviewed",
])
  el(id).onchange = save;
el("previous").onclick = () => {
  if (index > 0) {
    index--;
    show();
  }
};
el("choose-nonlocal").onclick = () => {
  el("scope").value = "nonlocal";
  save({target: el("scope")});
};
el("use-suggestion").onclick = () => {
  const row = rows[index], current = row.current_suggestion;
  if (!current) return;
  for (const key of ["topic", "group", "secondary_topics", "formats"])
    if (row.topic_reviewed !== "yes") row["expected_" + key] = current["expected_" + key];
  for (const key of ["scope", "regions", "country"])
    if (row.geography_reviewed !== "yes") row["expected_" + key] = current["expected_" + key];
  show();
  save();
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
    if (rows[next].topic_reviewed !== "yes" || rows[next].geography_reviewed !== "yes") {
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
        target.topic_reviewed = target.geography_reviewed = target.formats_reviewed = "no";
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
