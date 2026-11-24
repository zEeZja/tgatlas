# Разработка

Изменения сохраняют ручные ответы, исходные выгрузки и совместимость старых CLI.
Сетевой сбор, запись рабочей БД и публикация данных не должны запускаться тестами.

## Проверки

```bash
python -m pip install --require-hashes -r requirements/locked-dev.txt -r requirements/locked-training.txt
python -m ruff check .
python -m unittest discover -s tests
npm ci
npx playwright install chromium
npm run test:layout
npm run test:freshness
npm run test:product-map
npm run test:frontend
npm run test:review
```

Можно использовать установленный Chrome/Chromium через `CHROMIUM_PATH`.
Полный набор CPU-тестов требует NumPy/scikit-learn из `locked-training.txt`,
но не требует скачивания encoder-весов или запуска Telegram-сбора.
Безопасные фронтенд-тесты генерируют синтетические данные или используют демо;
рабочий каталог для CI не нужен. `test:published-layout` — дополнительная локальная
проверка ваших опубликованных JSON, не обязательная команда для чистой копии.

SQL-интеграция требует **отдельной временной PostgreSQL-базы**, указанной через
`ATLAS_TEST_DSN`. Не указывайте рабочую БД. Команда
`python scripts/run_postgres_tests.py` не допускает пропуски; подробности в
[DATABASE.md](docs/DATABASE.md). Обычный unit-прогон без DSN пропускает эти тесты.

## Перед pull request

- Не добавляйте `.env`, cookies, Telegram-сессии, дампы, посты и содержимое `reports/`.
- Не коммитьте рабочие `web/data/`, веса моделей, encoder-кеши и локальные эксперименты.
- Новую выгрузку сначала проверяйте локально; не меняйте файлы карты в тестах.
- Сходство модели и согласие с текущими метками не называйте независимой точностью.
- Обновите документацию и `CHANGELOG.md`; приложите воспроизводимый тест или скриншот.

Старые экспериментальные модули оставлены для совместимости и воспроизводимости.
Удаление интерфейса/CLI должно быть отдельным согласованным изменением, а не
побочным эффектом визуальной уборки репозитория.
