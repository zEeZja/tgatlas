# Установка и собственные данные

> **Обновление старой копии:** перед `git pull` сохраните отдельно `.env`,
> `web/data/`, `reports/` и свежий дамп БД. В 0.8.0 рабочие выгрузки сняты с Git-
> отслеживания; на других клонах pull может удалить прежние отслеживаемые файлы.
> После обновления восстановите свои локальные данные. `.gitignore` не является
> резервной копией. В установке, где готовился релиз, файлы оставлены на диске.

Для просмотра опубликованной карты достаточно Python и `python -m http.server 4173 --directory web`.
Дальнейшие шаги нужны только для собственного каталога. Команды запускаются из корня.

## Базовая установка

Нужны Python 3.10+, PostgreSQL и отдельная база (например, `tg_parser`).
Node.js 20+ нужен для разработки/браузерных тестов, но не для просмотра карты.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements/locked-base.txt
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements/locked-base.txt
```

Если `.env` ещё нет, скопируйте `.env.example` в `.env` и укажите свои
`PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DATABASE`.
**Существующий `.env` не заменяйте.** Создайте базу через pgAdmin или
`createdb -U postgres tg_parser`, затем:

```bash
python main.py init-db
python main.py db-status
```

Перед миграциями уже существующей базы сделайте свежую резервную копию.
[Подробности миграций и восстановления](DATABASE.md).

## Первый каталог

Следующие команды обращаются к публичным профилям Telegram и записывают БД:

```bash
python main.py refresh-metadata --from-config --workers 2
python main.py export
python -m http.server 4173 --directory web
```

Это первоначальное заполнение: название, описание и аудитория, без публикаций.
Telegram-аккаунт и `api_id/api_hash` для него не нужны. Существующие каналы не
заменяются устаревшими записями начального конфига.

**Для уже классифицированной карты** не используйте обычный экспорт БД как замену
insert-only слиянию. Следуйте [маршруту роста](GROWTH_6000.md), затем отдельным
командам 11v2 и t-SNE. Они сохраняют ручные приоритеты и архивируют прежние файлы.

## Необязательный API

```bash
python -m pip install --require-hashes -r requirements/locked-api.txt
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

В `web/api.js` настройте `CONFIG.mode: 'http'`; пустой `httpBase` означает тот же
сервер. `/api/health` проверяет процесс, `/api/ready` — БД и миграции. Модель не
запускается при запросе: используются готовые кеши и запасные правила.

## Необязательные ML-зависимости

11v2 и t-SNE устанавливаются отдельно от базового сборщика:
`requirements-classification.txt`, `requirements-layout.txt`. Полный маршрут,
загрузка весов и параметры CPU описаны в
[11v2](classification/CLASSIFICATION_11V2.md) и
[раскладке](classification/ATLAS_LAYOUT.md). Не загружайте веса в Git.
