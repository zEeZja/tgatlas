/* Topic resolution for existing exports, HTTP responses and local corrections. */
(function () {
  "use strict";
  const taxonomy = window.TG_TAXONOMY;
  const byId = Object.fromEntries(taxonomy.topics.map((t) => [t.id, t]));
  const storageKey = "tg-atlas.topic-overrides.v2";
  let local = {};
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved))
      local = saved;
  } catch (_) {
    /* Storage can be unavailable in private/file browsing. */
  }
  function normalize(value) {
    return String(value || "")
      .normalize("NFKC")
      .toLowerCase()
      .replace(/ё/g, "е")
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim();
  }
  function informativeDescription(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((line) =>
        line
          .split(
            /(?:по (?:вопросам )?(?:реклам|сотрудничеств)|разместить реклам|реклам(?:а|ный отдел)\s*[:👉📩]|для связи\s*[:👉]|обратная связь\s*[:👉]|регистраци[яи] в|зарегистрирован|заявление (?:на|ркн)|реестр ркн|\bркн\s*[:№]|\bpr\s*:)/iu,
          )[0]
          .replace(
            /https?:\/\/\S+|\b(?:t\.me|vk\.com|www\.)\/??\S*|[\w.+-]+@[\w.-]+\.\w+|@[\w]+/giu,
            " ",
          )
          .trim(),
      )
      .filter(Boolean)
      .join("\n");
  }
  function canonical(value) {
    const key = String(value || "")
      .trim()
      .toLowerCase();
    return taxonomy.aliases[key] || key;
  }
  function matches(keyword, text, field) {
    const pattern = normalize(keyword);
    if (pattern.includes(" ")) return ` ${text} `.includes(` ${pattern} `);
    return text.split(" ").some((token) => {
      if (
        keyword === "авто" &&
        ["автомат", "автор", "автоном", "автопортрет"].some((x) =>
          token.startsWith(x),
        )
      )
        return false;
      if (keyword === "искусств" && token.startsWith("искусствен"))
        return false;
      if (keyword === "новост" && token.startsWith("новостро")) return false;
      return (
        token === pattern ||
        (pattern.length >= (/^[a-z0-9]+$/.test(pattern) ? 5 : 4) &&
          token.startsWith(pattern)) ||
        (field === "username" &&
          pattern.length >= 5 &&
          /^[a-z0-9]+$/.test(pattern) &&
          token.endsWith(pattern) &&
          !(pattern === "sport" && /(?:transport|passport)$/.test(token)))
      );
    });
  }
  function classify(name = "", username = "", description = "", threshold = 3) {
    const fields = [
      ["name", normalize(name), 4],
      ["username", normalize(username), 3],
      ["description", normalize(informativeDescription(description)), 2],
    ];
    const ranked = [];
    const strongDescription = new Set();
    for (const topic of taxonomy.topics) {
      let score = 0;
      const evidence = [];
      for (const [field, text, weight] of fields) {
        const found = topic.keywords.filter((k) => matches(k, text, field));
        if (found.length) {
          score += weight * Math.min(found.length, 3);
          evidence.push(...found.slice(0, 3).map((k) => `${field}:${k}`));
        }
      }
      if (
        (topic.strong_description_keywords || []).some((k) =>
          matches(k, fields[2][1], "description"),
        )
      )
        strongDescription.add(topic.id);
      if (topic.id === "general") score *= 0.5;
      if (score) ranked.push({ id: topic.id, score, evidence });
    }
    ranked.sort(
      (a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
    const qualified = ranked.filter(
      (r) =>
        r.score >=
        (r.id === "general" || strongDescription.has(r.id)
          ? Math.min(threshold, 2)
          : threshold),
    );
    let best = qualified[0] ||
      ranked[0] || { id: "other", score: 0, evidence: [] };
    let winner = qualified.length ? best.id : "other";
    const contextual =
      winner === "other" ? localNewsEvidence(name, description) : null;
    if (contextual) {
      winner = "general";
      best = { id: "general", score: 3, evidence: [contextual] };
    }
    const ambiguous =
      ranked.length > 1 &&
      ranked[1].id !== "general" &&
      best.score - ranked[1].score < 2;
    return {
      subcategory: winner,
      score: best.score,
      evidence: best.evidence,
      candidates: ranked.slice(0, 3).map((t) => ({ id: t.id, score: t.score })),
      tags: qualified
        .filter(
          (r) =>
            r.id !== winner &&
            r.id !== "general" &&
            r.score >= Math.max(3, best.score * 0.6),
        )
        .slice(0, 3)
        .map((r) => r.id),
      review_required:
        winner === "other" ||
        ambiguous ||
        !!(contextual && contextual.includes("требует проверки")),
    };
  }
  const titleRules = Object.entries(taxonomy.region_aliases || {}).flatMap(
    ([region, aliases]) =>
      aliases.map((a) => [region, normalize(a).split(" ")]),
  );
  const biography = new Set([
    "родился",
    "родилась",
    "родом",
    "учился",
    "училась",
    "окончил",
    "окончила",
    "переехал",
    "переехала",
    "живу",
    "проживаю",
  ]);
  const geoContext = new Set([
    "новости",
    "новостей",
    "новостях",
    "жизнь",
    "жизни",
    "события",
    "событий",
    "города",
    "городе",
    "жители",
    "жителей",
    "жителям",
    "медиа",
    "мультимедиа",
    "афиша",
    "афиши",
    "канал",
    "телеграм",
    "сообщество",
    "сообщества",
    "журналист",
    "журналиста",
    "газета",
    "газеты",
    "портал",
    "портала",
  ]);
  const titleCache = new Map();
  function findTitleRegions(name, contextual = false) {
    const key = String(contextual) + ":" + name;
    if (titleCache.has(key)) return titleCache.get(key);
    const words = normalize(name).split(" "),
      hits = [];
    for (const [region, parts] of titleRules)
      for (let start = 0; start <= words.length - parts.length; start++) {
        const end = start + parts.length;
        if (
          parts.every(
            (b, i) =>
              words[start + i] === b ||
              (b.length >= 5 && words[start + i].startsWith(b)),
          )
        ) {
          if (
            contextual &&
            words
              .slice(Math.max(0, start - 4), Math.min(words.length, end + 2))
              .some((w) => biography.has(w))
          )
            continue;
          if (
            contextual &&
            !words
              .slice(Math.max(0, start - 6), Math.min(words.length, end + 6))
              .some((w) => geoContext.has(w))
          )
            continue;
          hits.push({
            region,
            start,
            end,
            length: parts.reduce((n, p) => n + p.length, 0),
          });
        }
      }
    const kept = hits.filter(
      (h) =>
        !hits.some(
          (k) => k.length > h.length && k.start <= h.start && k.end >= h.end,
        ),
    );
    const regions = [...new Set(kept.map((h) => h.region))].sort();
    if (titleCache.size >= 4096) titleCache.clear();
    titleCache.set(key, regions);
    return regions;
  }
  function inferTitleRegion(name) {
    const regions = findTitleRegions(name);
    return regions.length === 1 ? regions[0] : null;
  }
  function inferForeignTitle(name) {
    const words = normalize(name).split(" ");
    const found = (taxonomy.foreign_geography || []).filter((p) =>
      p.aliases.some((alias) =>
        words.some(
          (t) => t === alias || (alias.length >= 5 && t.startsWith(alias)),
        ),
      ),
    );
    if (new Set(found.map((p) => p.country)).size !== 1) return null;
    const cities = found.filter((p) => p.scope === "region");
    return cities.length === 1
      ? cities[0]
      : !cities.length && found.length === 1
        ? found[0]
        : null;
  }
  function localNewsEvidence(name, description) {
    const text = normalize(name),
      words = text.split(" "),
      region = inferTitleRegion(name),
      foreign = inferForeignTitle(name);
    if (!region && !(foreign && foreign.scope === "region")) {
      const desc = informativeDescription(description),
        tokens = normalize(desc).split(" ");
      if (
        findTitleRegions(desc, true).length === 1 &&
        tokens.some(
          (t) => ["медиа", "мультимедиа"].includes(t) || t.startsWith("новост"),
        )
      )
        return "description:городское медиа (требует проверки темы)";
      return null;
    }
    const markers = [
      "сейчас",
      "сегодня",
      "онлайн",
      "online",
      "live",
      "life",
      "лайф",
      "подслушано",
      "типичный",
      "типичная",
      "главный",
      "события",
      "жесть",
      "чпн",
    ];
    if (
      words.some((t) => markers.includes(t)) ||
      text.includes("в курсе") ||
      /№\s*1\b|\.(?:ru|рф|txt)\b/iu.test(name)
    )
      return "name:городской формат (город + сейчас/онлайн/подслушано)";
    const desc = normalize(informativeDescription(description));
    if (
      [
        "поделиться новост",
        "прислать новост",
        "присылайте новост",
        "предложить новост",
        "новости города",
        "новости нашего города",
        "события города",
        "народных новост",
      ].some((x) => desc.includes(x))
    )
      return "description:приём городских новостей";
    if (
      desc
        .split(" ")
        .some((t) => t.startsWith("новост") || t.startsWith("событи"))
    )
      return "description:город и новостное описание (требует проверки темы)";
    const aliases = region ? taxonomy.region_aliases[region] : foreign.aliases;
    if (aliases.map(normalize).includes(text))
      return "name:название города (требует проверки темы)";
    return null;
  }
  function profileGeography(channel) {
    const name = channel.name || "",
      title = findTitleRegions(name),
      foreign = inferForeignTitle(name);
    if (title.length || foreign) {
      if (
        title.length === 1 &&
        (!foreign || (foreign.scope === "country" && foreign.country === "RU"))
      )
        return [title, null, "name"];
      if (foreign && !title.length)
        return [
          foreign.scope === "region" ? [foreign.label] : [],
          foreign,
          "name",
        ];
      return [[], null, "conflict"];
    }
    const description = findTitleRegions(
        informativeDescription(channel.description),
        true,
      ),
      hint = findTitleRegions(channel.region_hint || "");
    if (
      description.length > 1 ||
      (description.length &&
        hint.length &&
        JSON.stringify(description) !== JSON.stringify(hint))
    )
      return [[], null, "conflict"];
    if (description.length === 1) return [description, null, "description"];
    if (hint.length === 1) return [hint, null, "hint"];
    return [[], null, "none"];
  }
  function geography(channel) {
    const key = String(channel.username || "")
      .replace(/^@/, "")
      .toLowerCase();
    const override = taxonomy.geography_overrides?.[key];
    if (override) {
      if (
        !["region", "multi_region", "country", "global", "unknown"].includes(
          override.scope,
        )
      )
        throw new Error("Неизвестный географический охват");
      channel = {
        ...channel,
        ...override,
        geographic_scope: override.scope,
        region_review_required: false,
      };
    }
    let regions = Array.isArray(channel.regions)
      ? [
          ...new Set(
            channel.regions
              .filter((x) => typeof x === "string" && x.trim())
              .map((x) => x.trim()),
          ),
        ].sort()
      : [];
    if (
      !regions.length &&
      typeof channel.region === "string" &&
      channel.region.trim()
    )
      regions = [channel.region.trim()];
    if (channel.region_review_required) regions = [];
    const federal =
      channel.channel_type === "federal" ||
      taxonomy.federal_channels.some((x) => x.toLowerCase() === key);
    let foreign = null,
      source = override ? "manual" : regions.length ? "stored" : "none";
    if (federal && !override && !channel.geographic_scope) {
      regions = [];
      source = "federal";
    }
    if (!regions.length && !override && !federal && !channel.geographic_scope)
      [regions, foreign, source] = profileGeography(channel);
    let scope;
    if (["global", "country", "unknown"].includes(channel.geographic_scope)) {
      scope = channel.geographic_scope;
      regions = [];
    } else if (foreign?.scope === "country") scope = "country";
    else if (regions.length > 1) scope = "multi_region";
    else if (regions.length) scope = "region";
    else if (federal) scope = "country";
    else scope = "unknown";
    const country = String(foreign?.country || channel.country || "")
      .trim()
      .toUpperCase();
    if (scope === "country" && !country) scope = "unknown";
    return {
      geography_scope: scope,
      geography_regions: regions,
      geography_country: country,
      geography_review_required: scope === "unknown",
      geography_source: source,
    };
  }
  function enrich(channel) {
    const username = String(channel.username || "")
      .replace(/^@/, "")
      .toLowerCase();
    const original = Object.hasOwn(channel, "original_subcategory")
      ? channel.original_subcategory
      : channel.subcategory;
    const declared = canonical(original);
    const inferred = classify(channel.name, username, channel.description);
    let topic, source;
    if (byId[local[username]]) {
      topic = local[username];
      source = "local";
    } else if (byId[taxonomy.overrides[username]]) {
      topic = taxonomy.overrides[username];
      source = "manual";
    } else if (byId[declared] && !["general", "other"].includes(declared)) {
      topic = declared;
      source = "declared";
    } else if (inferred.subcategory !== "other") {
      topic = inferred.subcategory;
      source = "heuristic";
    } else if (
      declared === "general" ||
      canonical(channel.category) === "general"
    ) {
      topic = "general";
      source = "fallback";
    } else if (
      byId[canonical(channel.category)] &&
      canonical(channel.category) !== "other"
    ) {
      topic = canonical(channel.category);
      source = "fallback";
    } else {
      topic = "other";
      source = "unknown";
    }
    const extraTags = (
      taxonomy.tag_overrides?.[username] || inferred.tags
    ).filter((x) => x !== topic && x !== "other");
    return {
      ...channel,
      original_subcategory: original ?? null,
      ...geography(channel),
      primary_category: topic,
      subcategory: topic,
      topic_category: byId[topic].section,
      topic_source: source,
      topic_version: taxonomy.version,
      topic_score: source === "heuristic" ? inferred.score : null,
      topic_evidence: source === "heuristic" ? inferred.evidence : [],
      topic_candidates: inferred.candidates,
      topic_tags: [topic, ...extraTags],
      secondary_topics: extraTags,
      topic_review_required:
        topic === "other" ||
        source === "fallback" ||
        (source === "heuristic" && inferred.review_required),
    };
  }
  function setLocal(username, topic) {
    const key = String(username).replace(/^@/, "").toLowerCase();
    if (topic && !byId[topic]) throw new Error("Неизвестная тема");
    const next = { ...local };
    if (topic) next[key] = topic;
    else delete next[key];
    // Only update active data if persistence succeeds; report storage failures to the user.
    localStorage.setItem(storageKey, JSON.stringify(next));
    local = next;
  }
  window.Topics = {
    taxonomy,
    byId,
    classify,
    geography,
    enrich,
    canonical,
    setLocal,
    hasLocal: (username) =>
      Object.hasOwn(local, String(username).replace(/^@/, "").toLowerCase()),
  };
})();
