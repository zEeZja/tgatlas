/* Topic resolution for existing exports, HTTP responses and local corrections. */
(function () {
  "use strict";
  const taxonomy = window.TG_TAXONOMY;
  const byId = Object.fromEntries(taxonomy.topics.map((t) => [t.id, t]));
  const groups = Object.fromEntries(taxonomy.groups.map((g) => [g.id, g]));
  function colorOf(channel) {
    const topic = byId[channel?.subcategory];
    // A confirmed broad group without an exact topic keeps its group colour.
    if (topic && topic.id !== "other") return topic.color;
    return groups[channel?.topic_group]?.color || topic?.color || byId.other.color;
  }
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
  const keywordPatterns = new Map();
  const tokenCache = new Map();
  function matches(keyword, text, field) {
    if (!keywordPatterns.has(keyword))
      keywordPatterns.set(keyword, normalize(keyword));
    const pattern = keywordPatterns.get(keyword);
    if (pattern.includes(" ")) return ` ${text} `.includes(` ${pattern} `);
    if (!tokenCache.has(text)) {
      if (tokenCache.size >= 4096) tokenCache.clear();
      tokenCache.set(text, text.split(" "));
    }
    return tokenCache.get(text).some((token) => {
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
      const profileFields = [fields[0], fields[2]];
      if ((topic.exclude_phrases || []).some(k => profileFields.some(([field, text]) => matches(k, text, field)))) continue;
      const prefix = (topic.name_prefixes || []).some(p => (fields[0][1] + " ").startsWith(normalize(p) + " "));
      if (topic.require_any && !prefix && !topic.require_any.some(k => profileFields.some(([field, text]) => matches(k, text, field)))) continue;
      let score = 0;
      const evidence = [];
      for (const [field, text, weight] of fields) {
        const found = topic.keywords.filter((k) => matches(k, text, field));
        if (found.length) {
          score += weight * Math.min(found.length, 3);
          evidence.push(...found.slice(0, 3).map((k) => `${field}:${k}`));
        }
      }
      if (prefix && !score) {
        score = 4;
        evidence.push("name:наименование органа власти");
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
    let qualified = ranked.filter(
      (r) =>
        r.score >=
        (r.id === "general" || strongDescription.has(r.id)
          ? Math.min(threshold, 2)
          : threshold),
    );
    qualified = qualified.filter(
      (r) => !qualified.some((c) => (byId[c.id].refines || []).includes(r.id)),
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
      qualified.length > 1 &&
      qualified[1].id !== "general" &&
      best.score - qualified[1].score < 2;
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
      const [places, ambiguous] = resolvePlaces(desc, "", true);
      const oneRegion = findTitleRegions(desc, true).length === 1 || (places.length && !ambiguous && new Set(places.map(p => p.region)).size === 1);
      if (
        oneRegion &&
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
      "афиша",
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
  const placeCatalogue = taxonomy.geo_places.places;
  const placeContext = new Set([
    "новости",
    "новостей",
    "жизни",
    "жизнь",
    "город",
    "города",
    "городе",
    "жителей",
    "жители",
    "медиа",
    "мультимедиа",
    "события",
    "сообщество",
    "канал",
    "афиша",
  ]);
  const placeBio = new Set([
    "родился",
    "родилась",
    "родом",
    "учился",
    "училась",
    "окончил",
    "окончила",
    "живу",
    "переехал",
    "переехала",
    "проживаю",
  ]);
  const placeRules = placeCatalogue.flatMap((place) =>
    place.aliases.map((alias) => ({
      place,
      parts: normalize(alias).split(" "),
    })),
  );
  function resolvePlaces(text, hint = "", contextual = false) {
    const words = normalize(text).split(" "),
      hits = [];
    for (const { place, parts } of placeRules) {
      for (let start = 0; start <= words.length - parts.length; start++) {
        const end = start + parts.length;
        if (!parts.every((p, i) => words[start + i] === p)) continue;
        const nearby = words.slice(
          Math.max(0, start - 12),
          Math.min(words.length, end + 4),
        );
        if (
          contextual &&
          (!nearby.some((w) => placeContext.has(w)) ||
            nearby.some((w) => placeBio.has(w)))
        )
          continue;
        hits.push({ place, start, end });
      }
    }
    let found = placeCatalogue.filter((p) =>
      hits.some(
        (h) =>
          h.place.id === p.id &&
          !hits.some(
            (k) =>
              k.end - k.start > h.end - h.start &&
              k.start <= h.start &&
              k.end >= h.end,
          ),
      ),
    );
    if (new Set(found.map((p) => p.region)).size > 1) {
      const context = normalize(text + " " + hint);
      const selected = found.filter((p) =>
        context.includes(normalize(p.region)),
      );
      if (new Set(selected.map((p) => p.region)).size === 1) found = selected;
      else return [found, true];
    }
    return [found, false];
  }
  function profileGeography(channel) {
    const name = channel.name || "",
      title = findTitleRegions(name),
      foreign = inferForeignTitle(name);
    const [places, ambiguous] = resolvePlaces(name, channel.region_hint || "");
    if (ambiguous) return [[], null, "conflict"];
    if (places.length) {
      if (foreign && foreign.country !== "RU") return [[], null, "conflict"];
      return [[...new Set(places.map((p) => p.region))].sort(), null, "name"];
    }
    if (
      normalize(name)
        .split(" ")
        .some((w) => ["проспект", "улица", "шоссе", "переулок"].includes(w))
    )
      return [[], null, "conflict"];
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
    const [descPlaces, descAmbiguous] = resolvePlaces(
      informativeDescription(channel.description),
      channel.region_hint || "",
      true,
    );
    if (descAmbiguous) return [[], null, "conflict"];
    if (descPlaces.length)
      return [
        [...new Set(descPlaces.map((p) => p.region))].sort(),
        null,
        "description",
      ];
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
  function geography(channel, territorial = null) {
    const key = String(channel.username || "")
      .replace(/^@/, "")
      .toLowerCase();
    const override = taxonomy.geography_overrides?.[key];
    const subjectPolicy = territorial === false && !override && !channel.geographic_scope;
    if (subjectPolicy) channel = {...channel, geographic_scope: "nonlocal"};
    if (override) {
      if (
        ![
          "region",
          "multi_region",
          "country",
          "global",
          "unknown",
          "nonlocal",
        ].includes(override.scope)
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
      source = override ? "manual" : subjectPolicy ? "subject_policy" : regions.length ? "stored" : "none";
    if (federal && !override && !channel.geographic_scope) {
      regions = [];
      source = "federal";
    }
    if (!regions.length && !override && !federal && !channel.geographic_scope)
      [regions, foreign, source] = profileGeography(channel);
    const cleaned = informativeDescription(channel.description);
    if (source === "none" && !regions.length && !override) {
      const f = inferForeignTitle(cleaned),
        words = normalize(cleaned);
      if (
        f &&
        f.scope === "country" &&
        [
          "налог",
          "законодатель",
          "экономик",
          "рынок",
          "рынке",
          "политик",
          "туризм",
          "путешеств",
        ].some((x) => words.includes(x))
      ) {
        foreign = f;
        source = "description";
      } else if (
        !f &&
        !findTitleRegions(cleaned).length &&
        taxonomy.nonlocal_evidence.some((p) =>
          (" " + normalize((channel.name || "") + " " + cleaned) + " ").includes(" " + normalize(p) + " "),
        )
      )
        source = "nonlocal_evidence";
    }
    let scope;
    if (
      ["global", "country", "unknown", "nonlocal"].includes(
        channel.geographic_scope,
      )
    ) {
      scope = channel.geographic_scope;
      regions = [];
    } else if (source === "nonlocal_evidence") scope = "nonlocal";
    else if (foreign?.scope === "country") scope = "country";
    else if (regions.length > 1) scope = "multi_region";
    else if (regions.length) scope = "region";
    else if (federal) scope = "country";
    else scope = "unknown";
    let country = String(foreign?.country || channel.country || "")
      .trim()
      .toUpperCase();
    if (scope === "country" && !country) scope = "unknown";
    if (scope === "nonlocal") country = "";
    let [places, ambiguous] = scope === "nonlocal" ? [[], false] : resolvePlaces(channel.name || "", channel.region_hint || "");
    if (scope !== "nonlocal" && !places.length)
      [places, ambiguous] = resolvePlaces(
        cleaned,
        channel.region_hint || "",
        true,
      );
    return {
      geography_explanation: {
        nonlocal: "Для тематического канала географическое деление не применяется. Это не неопределённый регион.",
        unknown: "Недостаточно данных или есть конфликт мест. Это не означает всю страну.",
        country: "Содержание ориентировано на указанную страну, без одного конкретного региона.",
        global: "Международная направленность содержания, а не измеренная география подписчиков.",
        region: "Установлена территориальная направленность на один регион.",
        multi_region: "Установлена направленность на несколько регионов.",
      }[scope],
      geography_relevance:
        scope === "nonlocal"
          ? "nonterritorial"
          : scope === "unknown"
            ? "undetermined"
            : "territorial",
      geography_places: places
        .filter((p) => regions.includes(p.region) && !ambiguous)
        .map((p) => ({
          id: p.id,
          label: p.label,
          region: p.region,
          country: p.country,
          kind: p.kind,
        })),
      geography_candidates: ambiguous
        ? [...new Set(places.map((p) => p.region))].sort()
        : [],
      geography_evidence: source !== "none" ? [source] : [],
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
      if (
        (byId[inferred.subcategory].refines || []).includes(declared) &&
        inferred.score >= 2
      ) {
        topic = inferred.subcategory;
        source = "heuristic";
      } else {
        topic = declared;
        source = "declared";
      }
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
    const group =
      source === "local"
        ? byId[topic].group
        : taxonomy.group_overrides?.[username] || byId[topic].group;
    let formats = [],
      formatEvidence = [];
    for (const fmt of taxonomy.formats)
      for (const [field, text] of [
        ["name", normalize(channel.name)],
        ["description", normalize(informativeDescription(channel.description))],
      ]) {
        const found = fmt.keywords.filter((k) => matches(k, text, field));
        if (found.length) {
          formats.push(fmt.id);
          formatEvidence.push(field + ":" + found[0]);
          break;
        }
      }
    if (Object.hasOwn(taxonomy.format_overrides || {}, username)) {
      formats = taxonomy.format_overrides[username];
      formatEvidence = ["manual"];
    }
    const reviewed = taxonomy.review_metadata?.[username];
    const stale = !!(
      reviewed &&
      (reviewed.name !== (channel.name || "") ||
        reviewed.description !== informativeDescription(channel.description))
    );
    return {
      ...channel,
      original_subcategory: original ?? null,
      ...geography(channel, topic === "other" && group === "other_group" ? null : taxonomy.geography_topics.includes(topic) || !!localNewsEvidence(channel.name || "", channel.description || "")),
      classification_review_stale: stale,
      classification_version: 2,
      topic_group: group,
      topic_group_label: groups[group].label,
      topic_path: topic !== "other" ? [group, topic] : [group],
      broader_topics: byId[topic].parent_topics || [],
      topic_granularity:
        topic !== "other"
          ? "topic"
          : group !== "other_group"
            ? "group"
            : "unknown",
      publication_formats: formats,
      format_evidence: formatEvidence,
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
        stale ||
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
    groupsById: groups,
    colorOf,
    classify,
    geography,
    enrich,
    canonical,
    setLocal,
    hasLocal: (username) =>
      Object.hasOwn(local, String(username).replace(/^@/, "").toLowerCase()),
  };
})();
