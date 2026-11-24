# Расширение карты до 6000: новости и военная аналитика

Все команды — из корня проекта, PowerShell, с вашим рабочим Python и настроенным
`.env`. Нужны PostgreSQL, обычные зависимости сборщика и уже установленные
локальные зависимости/веса 11v2 и t-SNE. Платные API, GPU, Telegram-аккаунт не нужны.
Не заменяйте `.env` и не переустанавливайте рабочую среду без необходимости.

## Что проверено и изменено

Переписывать сборщик не понадобилось: `grow_catalog.py` уже проверяет публичные
профили, исключает дубликаты, добавляет только новые записи и сохраняет состояние.
Две страницы каталогов проверены через robots-aware HTTP-клиент: TgramSearch
`search?query=news` и tlgrm `/channels/economics` разобраны текущими адаптерами.
Это не проверка всей выдачи и не гарантия доступности при следующем запуске.

Новый план `config/catalog_growth_news_military.json` ищет новости и военную
аналитику, включая войну в Украине, на русском, украинском и английском.
Порог **50 000 подписчиков относится только к новым каналам**. Старые записи
не отсеиваются. Рубрика каталога/запрос — лишь способ обнаружения; тематический
допуск по профилю предварительный, не окончательная 11v2-классификация, не проверка
достоверности публикаций и не определение страны по стороне конфликта.

В новом плане отключён автоматический экспорт. Рост меняет БД, но **не файлы
карты**. Позже отдельное слияние добавляет только импорты выбранного запуска,
сохраняя старые записи JSON целиком и архивируя прежние JSON и JS независимо.
Классификация, сбор постов и проекция поддерживают до 6000 каналов.

## 1. Свежая резервная копия

Подставьте свои параметры PostgreSQL. Не перезаписывайте старые дампы:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
pg_dump -h 127.0.0.1 -U postgres -d tg_parser -Fc -f "atlas-before-growth-6000-$stamp.dump"
pg_restore --list "atlas-before-growth-6000-$stamp.dump"
python main.py db-status
```

Остановитесь, если дамп не создан/не читается. `pg_dump` не читает проектный `.env`;
пароль — через штатный запрос/pgpass, не в командной строке. Проверка списка
не заменяет пробное восстановление в отдельную БД. Если нужны миграции, сначала
прочтите [DATABASE.md](DATABASE.md); сборщик их автоматически не применяет.

## 2. Проверка плана без сети и записи

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_news_military.json --target 6000 --state reports/catalog-growth-6000/news/state.json
```

Ожидаемые параметры: `minimum_subscribers: 50000`, `auto_export: false`.
При нынешних 3000 активных каналах будет `missing: 3000`. Без `--apply` это только
план, а не пробный поиск. Указывайте новый план **во всех** командах роста:
старый план ориентирован на отрасли и автоматически экспортирует карту.

## 3. Небольшая настоящая проба

Эта команда уже обращается к источникам и добавляет новые каналы в БД:

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_news_military.json --target 3050 --state reports/catalog-growth-6000/news/state.json --workers 2 --max-profiles 100 --max-requests 200 --max-minutes 20 --apply
python -B scripts/grow_catalog.py --state reports/catalog-growth-6000/news/state.json --status
```

`3050` подходит при исходных 3000: это общий размер, не число добавлений.
При другом исходном размере возьмите текущий + 50. Бюджет 100 профилей не обещает
50 импортов: встречаются дубли, неподходящие темы и малая аудитория.
Посмотрите журнал и `processed` в state: подходят ли добавленные каналы.
Для просмотра добавлений без изменения карты можно выполнить шаг 5 `prepare`
в отдельную новую папку и открыть его `channels.json`.

## 4. Основной рост и продолжение

После проверки пробы — **тот же state**, больший общий target:

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_news_military.json --target 6000 --state reports/catalog-growth-6000/news/state.json --workers 2 --max-profiles 6000 --max-requests 12000 --max-minutes 180 --apply
python -B scripts/grow_catalog.py --state reports/catalog-growth-6000/news/state.json --status
```

После обычного исчерпания бюджета повторите ту же команду с тем же state:
лимиты роста `max-*` относятся к одному запуску процесса, прогресс сохраняется.
`target_reached` — цель достигнута; `budget_exhausted` — закончился бюджет;
`sources_exhausted` — план исчерпан, а не достигнуто 6000;
`paused_for_retry` — временные ошибки, изучите журнал перед продолжением.

Для сохранения журнала добавьте к команде роста
`--log reports/catalog-growth-6000/news/growth.log`: без `--log` сообщения
выводятся только в консоль. Последние строки:
`Get-Content reports/catalog-growth-6000/news/growth.log -Tail 80 -Encoding UTF8`.
Карточки источников с HTTP 404/410 отмечаются `source_not_found`, а не остаются
в бесконечной очереди повторов; это не отказ доступа к Telegram.

При `telegram_blocked` (403/429) новые Telegram-запросы прекращаются; уже
отправленные могут завершиться. Не обходите отказ прокси, новым state или
сбросом счётчиков. После восстановления доступа и окончания Retry-After
продолжение требует явного флага:

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_news_military.json --target 6000 --state reports/catalog-growth-6000/news/state.json --workers 2 --max-profiles 6000 --max-requests 12000 --max-minutes 180 --resume-after-block --apply
```

Не запускайте повторно в цикле при сохраняющемся отказе. Ограничения каталогов,
robots.txt и CAPTCHA также не обходятся.

Если уникальных подходящих каналов ≥50k недостаточно, можно остановиться на
меньшем числе. Снижение порога — отдельное осознанное решение: например,
`--minimum-subscribers 20000` с **новым** state после устранения ограничений
доступа. Эффективный порог не ниже `MIN_CHANNEL_SUBSCRIBERS` из `.env`.
Не редактируйте старый state или план посреди обхода. Слияние ниже принимает
несколько `--state`, если понадобятся разные этапы.

## 5. Добавление в карту без потери старых каналов

Если первый план исчерпан, сначала можно выполнить
[второй этап с другими источниками](#второй-этап-другие-открытые-источники).
После него обязательно включите **оба state** в `prepare`, как показано ниже
в разделе второго этапа. Один state включает только импорты своего запуска.

Не используйте здесь `python main.py export`: обычная выгрузка БД может заменить
текущие поля карты. Остановите рост и другие процессы публикации на время слияния.
Используйте новую папку предпросмотра:

```powershell
python -B scripts/merge_catalog_growth.py prepare --state reports/catalog-growth-6000/news/state.json --output reports/catalog-growth-6000/merge-1
python -B scripts/merge_catalog_growth.py publish --input reports/catalog-growth-6000/merge-1
```

Проверьте `manifest.json` (`before`, `added`, `after`, `run_ids`) и новый
`channels.json`. Это read-only чтение БД и локальный предпросмотр. При нескольких
этапах повторите `--state <другой state>` в команде `prepare`.
Лишние старые записи БД автоматически в карту не возвращаются.

После проверки:

```powershell
python -B scripts/merge_catalog_growth.py publish --input reports/catalog-growth-6000/merge-1 --apply
```

Старые строки сохраняются без изменения; новые добавляются до общего лимита
6000. Превышение лимита — ошибка, а не удаление части карты. Если исходные JSON/JS
изменились после подготовки, нужен новый предпросмотр. Архивы:
`reports/catalog-growth-6000/map-backups/<время>/`.
Слияние ещё не классифицирует новые записи. Прежняя t-SNE-проекция не подходит
к расширенному составу: до шага 8 браузер использует резервную раскладку.

## 6. Посты — в новый запуск с повторным использованием кеша

**Альтернатива прямому HTTP:** [сбор через RSSHub](RSSHUB_POSTS.md) — внешний
HTTPS-сервер не требует Docker/виртуализации на вашем компьютере.
Если этот шаг уже выполняется, можно явно переключить **тот же run**, сохранив
посты, счётчики и дедлайн. Не создавайте новый запуск ради смены транспорта.

Не меняйте snapshot старого запуска на 6000 и не сбрасывайте его счётчики.
Выберите новую папку, здесь `posts-run-1`:

```powershell
python -B scripts/classify_catalog.py collect --run reports/catalog-growth-6000/posts-run-1 --reuse-posts reports/classification-v11/catalog-run/posts --http --requests 3000 --hours 8
python -B scripts/classify_catalog.py status --run reports/catalog-growth-6000/posts-run-1
```

Сначала используются БД и готовые локальные посты. Старый кеш не изменяется.
Если предыдущие посты у вас в другой папке, замените путь `--reuse-posts`.
Без `--http` команда работает только с БД и локальным кешем.
Бюджет сохранён: **до 3000 HTTP-запросов и 8 часов**, а не 6000 запросов.
Он не гарантирует посты для всех каналов; при нехватке 11v2 использует профиль.
Продолжение — тот же `--run`, без сброса collection.json; увеличение времени
и остановки по 403/429 описаны в [PRIMARY_V11.md](classification/PRIMARY_V11.md).

## 7. Классификация 11v2 и экспорт

Важно: и здесь, и в t-SNE используйте **один и тот же новый** `posts-run-1/posts`,
а не прежний кеш по умолчанию. Полный запуск — без `--limit` и `--allow-partial`:

```powershell
python -B scripts/classify_catalog_v11v2.py classify --post-cache reports/catalog-growth-6000/posts-run-1/posts --output reports/classification-v11v2/catalog-6000-1 --seconds 28800
python -B scripts/classify_catalog_v11v2.py view --run reports/classification-v11v2/catalog-6000-1
python -B scripts/classify_catalog_v11v2.py publish --run reports/classification-v11v2/catalog-6000-1
```

Откройте созданный HTML, проверьте новости, военную аналитику, отказы и географию.
Не публикуйте незавершённый расчёт (`remaining` должен быть 0). Сходство модели и
диагностические метрики не являются независимой точностью или вероятностью
правильности. После проверки:

```powershell
python -B scripts/classify_catalog_v11v2.py publish --run reports/classification-v11v2/catalog-6000-1 --apply
python -B scripts/classify_catalog_v11v2.py export
python -B scripts/classify_catalog_v11v2.py export --apply
```

Экспорт сохраняет ручные приоритеты и архивирует прежние файлы карты; V11 и
прежние результаты опытов остаются отдельно. Подробности —
[CLASSIFICATION_11V2.md](classification/CLASSIFICATION_11V2.md).

## 8. Новый t-SNE и просмотр

```powershell
python -B scripts/build_atlas_layout.py compare --methods tsne --post-cache reports/catalog-growth-6000/posts-run-1/posts --output reports/atlas-layout/tsne-6000-1
python -B scripts/build_atlas_layout.py publish --input reports/atlas-layout/tsne-6000-1/tsne.json
```

Посмотрите `comparison.html`; метод должен иметь `status: ok`. После проверки:

```powershell
python -B scripts/build_atlas_layout.py publish --input reports/atlas-layout/tsne-6000-1/tsne.json --apply
python -m http.server 4173 --directory web
```

Откройте http://localhost:4173 и обновите страницу. Проверьте число каналов,
метку TSNE, поиск и карточки. Не запускайте параллельно ML-процессы на машине
с ограниченной памятью. В t-SNE исправлено лишнее удержание квадратных сортировок
и убраны массивы N×N×2 из диагностики; матрицы расстояний N×N остаются.
Подробнее — [ATLAS_LAYOUT.md](classification/ATLAS_LAYOUT.md).

Все имена `*-1` — примеры новых папок. При повторном полном маршруте выбирайте
новые имена, не удаляйте прежние эксперименты ради повторного запуска.

## Границы проверки

Локальная проверка: 338 Python-тестов, из них 38 пропущены; Ruff без ошибок.
SQL-интеграционные тесты записи требуют отдельной тестовой PostgreSQL-базы:
рабочая БД для них не использовалась. План без `--apply` показал 3000 активных
каналов, 29 заданий, порог 50k и отключённый автоэкспорт.

В Chrome проверен интерфейс на 6000 синтетических записях по HTTP и `file://`:
загрузка артефакта координат, поиск, карточки, ручная правка/отмена, мобильная
ширина. Это проверка вместимости и контрактов, не реальной тематической карты.

Синтетический замер геометрии: 6000 нормированных случайных float32-векторов
по 384 измерения, seed 42, без региональных весов; матрицы расстояний → t-SNE
(perplexity 30, 1000 итераций, 4 потока) → раздвижение точек → диагностика соседей.
На этой машине: **33,95 с, пик процесса 705,8 МиБ**, координаты конечны.
Encoder/torch, реальная классификация и региональный аудит в этот замер
**не входили**. Это не гарантия полного ML-маршрута до 2 ГБ и не оценка времени
на другой машине. Память полного запуска смотрите в его собственном отчёте.

Массовый сбор, импорт, публикация и замена рабочих данных при подготовке
инструкции не выполнялись. Сохранённые результаты прежних моделей не заменены.

## Второй этап: другие открытые источники

Этот маршрут нужен после `sources_exhausted` у первого плана, а не при блокировке
доступа. Исходный `news/state.json` и старый план оставьте без изменений.

Добавлен отдельный `config/catalog_growth_multisource_news_military.json`:

- tlgrm: `/channels/news` (новости), `/channels/svo` (конфликт на Украине),
  `/channels/economics` (политика — это фактическое название рубрики на сайте,
  а не экономическая рубрика, несмотря на URL);
- Zenodo: фиксированный открытый `channels.csv` из
  [исследовательского набора](https://doi.org/10.5281/zenodo.16949193),
  66 260 строк метаданных, 16 110 693 байта, CC BY 4.0.

Ограниченная проверка 23.11.2026:

| Источник | Результат |
|---|---|
| TGStat (Россия и Украина) | HTTP 403 уже при проверке robots.txt; не подключён |
| Telemetrio, каталог Украины | Страница открыта, но username в карточках помечены `Username for Pro`; не извлекаем их из slug или скрытого состояния |
| tlgrm | Три страницы разобраны: 23/24/24 кандидата с известной аудиторией ≥50k или без известной аудитории; между страницами возможны дубли |
| Zenodo | CSV открыт; размер и MD5 совпали с метаданными опубликованной записи |

В Zenodo после удаления fake, ботов, невалидных username, дублей и известных
аудиторий ниже 50k осталось 16 915 кандидатов; у 14 338 аудитория неизвестна.
Чтобы не проверять тысячи посторонних каналов, **новый план дополнительно
отбирает кандидатов по новостным/военным словам в историческом названии и
описании**. Среди отсутствующих в известных username рабочей БД найдено около
950 таких кандидатов, у 616 историческая аудитория ≥50k. Числа относятся к
моменту проверки, не к количеству будущих импортов.

Это исторические метаданные, а не актуальная аудитория или готовая классификация.
Старые малые каналы могли вырасти, сменить имя или владельца, старые описания
могут не отражать нынешнюю тематику. Допуск и окончательные поля формируются
из текущего Telegram-профиля и затем 11v2, не из исследовательских меток.
Сведения о стороне конфликта, токсичности и кластерах из набора не используются.
Подробнее об авторстве и изменениях: [уведомление об источнике](licenses/ZENODO_WAR_CHANNELS.md).

При `--apply` скачивается только CSV метаданных, **не гигабайтные архивы постов**.
Для него задан отдельный предел 20 МБ и проверка фиксированного MD5 до обработки;
остальные HTML-страницы по-прежнему ограничены 2 МБ. CSV кешируется рядом с новым
state; при несовпадении контрольной суммы обработка отклоняется.

### Запуск второго этапа

Перед записью сделайте свежую резервную копию БД по шагу 1. Проверьте план:

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_multisource_news_military.json --target 6000 --state reports/catalog-growth-6000/multisource/state.json
```

Ожидаются `jobs: 4`, `minimum_subscribers: 50000`, `auto_export: false`.
Небольшая проба при текущих 3463 каналах (целевой размер = текущий + 50):

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_multisource_news_military.json --target 3513 --state reports/catalog-growth-6000/multisource/state.json --workers 2 --max-profiles 100 --max-requests 100 --max-minutes 20 --log reports/catalog-growth-6000/multisource/growth.log --apply
python -B scripts/grow_catalog.py --state reports/catalog-growth-6000/multisource/state.json --status
```

После проверки журнала и пробных импортов:

```powershell
python -B scripts/grow_catalog.py --plan config/catalog_growth_multisource_news_military.json --target 6000 --state reports/catalog-growth-6000/multisource/state.json --workers 2 --max-profiles 1500 --max-requests 100 --max-minutes 180 --log reports/catalog-growth-6000/multisource/growth.log --apply
```

При обычном исчерпании бюджета продолжайте тем же планом и тем же **новым**
`multisource/state.json`; блокировки и Retry-After соблюдаются как в первом этапе.
Ровно 6000 этот источник также не гарантирует; порог автоматически не снижается.

### Слияние результатов обоих этапов

После остановки роста подготовьте **новую** папку предпросмотра:

```powershell
python -B scripts/merge_catalog_growth.py prepare --state reports/catalog-growth-6000/news/state.json --state reports/catalog-growth-6000/multisource/state.json --output reports/catalog-growth-6000/merge-multisource-1
python -B scripts/merge_catalog_growth.py publish --input reports/catalog-growth-6000/merge-multisource-1
```

Проверьте `manifest.json` и `channels.json`. Только после проверки:

```powershell
python -B scripts/merge_catalog_growth.py publish --input reports/catalog-growth-6000/merge-multisource-1 --apply
```

Если импорты первого этапа уже есть в карте, они не дублируются и не заменяются.
Затем выполните шаги 6–8: новый сбор постов с повторным использованием прежнего
кеша → 11v2 → экспорт → t-SNE, везде с одним и тем же новым кешем постов.

После добавления источника: полный Python-набор — 345 тестов, 38 пропусков;
Ruff по затронутым Python-файлам без ошибок. Проверялись парсер CSV,
контрольная сумма, тематический допуск обнаружения, точное сохранение переносов
строк в кеше, ограничения размера и совместимость прежних источников.
