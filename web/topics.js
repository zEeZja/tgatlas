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
    enrich,
    canonical,
    setLocal,
    hasLocal: (username) =>
      Object.hasOwn(local, String(username).replace(/^@/, "").toLowerCase()),
  };
})();
