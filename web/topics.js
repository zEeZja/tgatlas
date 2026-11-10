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
      if (keyword === "новост" && token.startsWith("новостро")) return false;
      return (
        token === pattern ||
        (pattern.length >= (/^[a-z0-9]+$/.test(pattern) ? 5 : 4) &&
          token.startsWith(pattern)) ||
        (field === "username" &&
          pattern.length >= 5 &&
          /^[a-z0-9]+$/.test(pattern) &&
          token.includes(pattern))
      );
    });
  }
  function classify(name = "", username = "", description = "", threshold = 3) {
    const fields = [
      ["name", normalize(name), 4],
      ["username", normalize(username), 3],
      ["description", normalize(description), 1],
    ];
    const ranked = [];
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
      if (topic.id === "general") score *= 0.5;
      if (score) ranked.push({ id: topic.id, score, evidence });
    }
    ranked.sort(
      (a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
    const best = ranked[0] || { id: "other", score: 0, evidence: [] };
    const winner =
      best.score >= (best.id === "general" ? Math.min(threshold, 2) : threshold)
        ? best.id
        : "other";
    const ambiguous =
      ranked.length > 1 &&
      ranked[1].id !== "general" &&
      best.score - ranked[1].score < 2;
    return {
      subcategory: winner,
      score: best.score,
      evidence: best.evidence,
      candidates: ranked.slice(0, 3).map((t) => ({ id: t.id, score: t.score })),
      review_required: winner === "other" || ambiguous,
    };
  }
  const titleRules = Object.entries(taxonomy.region_aliases || {}).flatMap(
    ([region, aliases]) =>
      aliases.map((a) => [region, normalize(a).split(" ")]),
  );
  const titleCache = new Map();
  function inferTitleRegion(name) {
    if (titleCache.has(name)) return titleCache.get(name);
    const words = normalize(name).split(" "),
      hits = [];
    for (const [region, parts] of titleRules)
      for (let start = 0; start <= words.length - parts.length; start++) {
        if (
          parts.every(
            (b, i) =>
              words[start + i] === b ||
              (b.length >= 5 && words[start + i].startsWith(b)),
          )
        )
          hits.push({
            region,
            start,
            end: start + parts.length,
            length: parts.reduce((n, p) => n + p.length, 0),
          });
      }
    const kept = hits.filter(
      (h) =>
        !hits.some(
          (k) => k.length > h.length && k.start <= h.start && k.end >= h.end,
        ),
    );
    const regions = [...new Set(kept.map((h) => h.region))];
    const result = regions.length === 1 ? regions[0] : null;
    titleCache.set(name, result);
    return result;
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
    if (!regions.length && !override && !federal && !channel.geographic_scope) {
      const inferred = inferTitleRegion(channel.name || "");
      if (inferred) regions = [inferred];
    }
    let scope;
    if (["global", "country", "unknown"].includes(channel.geographic_scope)) {
      scope = channel.geographic_scope;
      regions = [];
    } else if (regions.length > 1) scope = "multi_region";
    else if (regions.length) scope = "region";
    else if (channel.channel_type === "federal") scope = "country";
    else scope = "unknown";
    const country = String(channel.country || "")
      .trim()
      .toUpperCase();
    if (scope === "country" && !country) scope = "unknown";
    return {
      geography_scope: scope,
      geography_regions: regions,
      geography_country: country,
      geography_review_required: scope === "unknown",
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
      topic_review_required:
        topic === "other" ||
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
