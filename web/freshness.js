/* Successful metadata checks, not export dates or post polling, define freshness. */
(function () {
  const maxAgeHours = 24;
  function date(value) {
    const result = typeof value === "string" ? Date.parse(value) : NaN;
    return Number.isFinite(result) ? result : null;
  }
  function status(channel, now = Date.now()) {
    const checked = date(channel.last_meta_at);
    if (checked === null || checked > now + 5 * 60 * 1000) return "unknown";
    return now - checked >= maxAgeHours * 3600000 ? "stale" : "fresh";
  }
  function counts(channels, now = Date.now()) {
    const result = { fresh: 0, stale: 0, unknown: 0 };
    for (const channel of channels) result[status(channel, now)]++;
    return result;
  }
  const errors = {
    timeout: "истекло время ожидания",
    network_error: "ошибка сети",
    unavailable: "канал недоступен",
    invalid_or_limited_response: "неполный ответ или ограничение Telegram",
    update_failed: "не удалось обновить данные",
    incomplete_metadata: "получены не все метаданные",
  };
  window.Freshness = { maxAgeHours, date, status, counts,
    errorLabel: (code) => errors[code] || "не удалось обновить данные" };
})();
