# Проверенные снимки зависимостей

В корневых `requirements*.txt` остаются допустимые диапазоны версий. Для воспроизводимой установки используются файлы здесь:

| Файл | Назначение |
|---|---|
| `locked-base.txt` | Публичный HTML-сбор, PostgreSQL и экспорт |
| `locked-api.txt` | Базовый режим + FastAPI/Uvicorn |
| `locked-dev.txt` | API + HTTP-клиент тестов и Ruff |

```bash
python -m pip install --require-hashes -r requirements/locked-dev.txt
python -m pip check
```

Каждая зависимость закреплена точной версией и SHA-256 допустимых дистрибутивов. Условные записи учитывают Python и ОС: например, uvloop не устанавливается на Windows. Разрешение универсальное с минимумом Python 3.10; CI проверяет Linux/Python 3.10 и 3.12. Установка на Windows учтена маркерами, но в текущем Linux-прогоне непосредственно не проверялась. Одинаковое окружение требует новой виртуальной среды: pip не удаляет ранее установленные посторонние пакеты.

Не смешивайте воспроизводимую установку с последующим `pip install -U -r requirements.txt`: это снимает фиксацию версий. Необязательные Telethon/ML-зависимости в эти снимки не входят.

## Обновление

Снимки сгенерированы uv 0.13.5. Для изменения сначала обновите входные диапазоны при необходимости, затем выполните из корня проекта:

```bash
uv pip compile --python-version 3.10 --universal --generate-hashes --default-index https://pypi.org/simple --no-header requirements-dev.txt -o requirements/locked-dev.txt
uv pip compile --python-version 3.10 --universal --generate-hashes --default-index https://pypi.org/simple --no-header -c requirements/locked-dev.txt requirements.txt -o requirements/locked-base.txt
uv pip compile --python-version 3.10 --universal --generate-hashes --default-index https://pypi.org/simple --no-header -c requirements/locked-dev.txt requirements-api.txt -o requirements/locked-api.txt
```

Существующий lock учитывается при повторной генерации. Для намеренного обновления версий добавьте `--upgrade` к первой команде, затем пересоздайте остальные снимки с её ограничениями. Проверьте diff, чистую установку, `pip check`, модульные и интеграционные тесты. Входные файлы не должны содержать URL с токенами; файлы блокировки коммитятся вместе с изменением зависимостей.
