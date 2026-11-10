/**
 * Поиск с токенизацией и приоритетами.
 *
 * Каждый токен запроса должен найтись хотя бы в одном поле, иначе канал
 * отбрасывается. Минимальный итоговый score — 50 (одно точное слово в
 * названии уже проходит, случайное совпадение в описании — нет).
 */
(function () {
  const MIN_SCORE = 50;

  function normalize(s) {
    return String(s ?? "")
      .toLowerCase()
      .replace(/ё/g, "е")
      .replace(/\s+/g, " ")
      .trim();
  }

  function scoreToken(token, ch) {
    const name = normalize(ch.name);
    const user = normalize(ch.username);
    const desc = normalize(ch.description || "");
    const region = normalize(ch.region || "");
    const topic = normalize(window.Topics?.byId[ch.subcategory]?.label || "");
    const section = normalize(
      window.Topics?.taxonomy.sections.find((s) => s.id === ch.topic_category)
        ?.label || "",
    );

    // 1. Точное слово в названии
    const nameWords = name.split(/[\s\-_.,:;!?()/]+/).filter(Boolean);
    if (nameWords.includes(token)) return 120;

    // 2. Префикс слова в названии
    for (const w of nameWords) {
      if (w.length >= 2 && w.startsWith(token)) return 100;
    }

    // 3. Префикс username
    if (user.startsWith(token)) return 110;

    // 4. Подстрока в username
    if (user.includes(token)) return 80;

    // 5. Подстрока в названии
    if (name.includes(token)) return 60;

    if (topic.includes(token) || section.includes(token)) return 70;

    // 6. Регион
    if (region.includes(token)) return 55;

    // 7. Описание — только если токен длинный
    if (token.length >= 4 && desc.includes(token)) return 25;

    return 0;
  }

  function scoreChannel(query, ch) {
    const q = normalize(query).replace(/(^|\s)@/g, "$1");
    if (!q) return 1;
    const tokens = q.split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return 1;

    let total = 0;
    for (const token of tokens) {
      const s = scoreToken(token, ch);
      if (s === 0) return 0;
      total += s;
    }
    return total;
  }

  function searchChannels(channels, query) {
    const q = query.trim();
    if (!q) return [...channels];

    return channels
      .map((ch) => ({ ch, score: scoreChannel(q, ch) }))
      .filter((x) => x.score >= MIN_SCORE)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.ch);
  }

  window.Search = { searchChannels, scoreChannel };
})();
