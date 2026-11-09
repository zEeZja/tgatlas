-- Безопасная проверка кандидатов, полученных из demo-fixture tgden.
-- Сначала выполните только этот SELECT и убедитесь, что видите ожидаемые записи.
SELECT
    c.username,
    c.name,
    c.status,
    c.imported_channel_id,
    s.source_name,
    s.source_ref,
    s.raw_data ->> 'catalog_id' AS catalog_id
FROM channel_candidates AS c
JOIN channel_candidate_sources AS s USING (username)
WHERE s.source_name LIKE 'tgden_%'
  AND s.raw_data ->> 'catalog_id' LIKE 'demo-%'
ORDER BY c.username, s.source_name;

-- Очистка намеренно выключена.
-- После проверки SELECT и резервного копирования можно выполнить этот блок.
-- Он удалит только связи с tgden demo-источником и только те неимпортированные
-- candidate-записи, у которых больше не осталось других источников.
/*
BEGIN;

DELETE FROM channel_candidate_sources AS s
WHERE s.source_name LIKE 'tgden_%'
  AND s.raw_data ->> 'catalog_id' LIKE 'demo-%';

DELETE FROM channel_candidates AS c
WHERE c.imported_channel_id IS NULL
  AND c.status <> 'imported'
  AND NOT EXISTS (
      SELECT 1
      FROM channel_candidate_sources AS s
      WHERE s.username = c.username
  );

COMMIT;
*/
