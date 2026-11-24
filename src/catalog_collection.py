"""Возобновляемая выборка для классификации; сеть только при явном выборе транспорта."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time

from .classification_samples import AD, sample_channel, sample_from_database
from .classification_runtime import fingerprint
from .file_io import atomic_write_text
from .taxonomy_pilot import PilotStopped


def suitable_posts(sample, maximum=8, now=None):
    now = now or datetime.now(timezone.utc)
    fetched = datetime.fromisoformat(sample["fetched_at"])
    if fetched.tzinfo is None or not 0 <= (now - fetched).total_seconds() <= 168 * 3600:
        return []
    posts, seen = [], set()
    for post in sample.get("posts", []):
        text = str(post.get("text") or "").strip()
        key = " ".join(text.casefold().split())
        try:
            date = datetime.fromisoformat(
                str(post.get("date", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if date.tzinfo is None or not 0 <= (now - date).total_seconds() <= 30 * 86400:
            continue
        if len(text) < 40 or AD.search(text) or key in seen:
            continue
        seen.add(key)
        posts.append({**post, "text": text[:4000]})
    # Spread dates before clipping to eight rather than retaining one busy event.
    dates = {}
    for post in posts:
        dates.setdefault(post["date"][:10], []).append(post)
    chosen = []
    while len(chosen) < maximum and any(dates.values()):
        for bucket in dates.values():
            if bucket and len(chosen) < maximum:
                chosen.append(bucket.pop(0))
    return chosen


class CatalogFetcher:
    def __init__(self, state, save, session):
        self.state, self.save, self.session = state, save, session
        self.previous = None

    def __call__(self, username, before=None):
        self.reserve(username, before)
        response = self.session.get(
            "https://t.me/s/" + username, timeout=20, allow_redirects=False
        )
        if response.status_code in (403, 429):
            raise PilotStopped(
                "Ограничение Telegram: HTTP " + str(response.status_code)
            )
        if 300 <= response.status_code < 400:
            raise ValueError("Превью перенаправляет на другую страницу")
        response.raise_for_status()
        return response.text

    def reserve(self, username, before=None):
        if before is not None or self.state["requests_by_channel"].get(username, 0):
            raise ValueError("На канал разрешена только одна страница без повторов")
        if datetime.now(timezone.utc).timestamp() >= self.state["deadline"]:
            raise PilotStopped("Истёк лимит времени")
        if self.state["requests"] >= self.state["request_budget"]:
            raise PilotStopped("Исчерпан лимит запросов")
        previous = self.state.get("last_request_at")
        if previous is not None:
            time.sleep(max(0, 6 - (datetime.now(timezone.utc).timestamp() - previous)))
        if datetime.now(timezone.utc).timestamp() >= self.state["deadline"]:
            raise PilotStopped("Истёк лимит времени")
        self.state["requests"] += 1
        self.state["requests_by_channel"][username] = 1
        self.state["last_request_at"] = datetime.now(timezone.utc).timestamp()
        self.save()
        self.previous = time.monotonic()


class RSSHubFetcher(CatalogFetcher):
    def __init__(self, state, save, session, base_url):
        super().__init__(state, save, session)
        from .rsshub_posts import normalize_base_url

        self.base_url = normalize_base_url(base_url)

    def __call__(self, username, before=None):
        import requests
        from .rsshub_posts import MAX_FEED_BYTES, feed_url, parse_feed

        url = feed_url(self.base_url, username)
        self.reserve(username, before)
        try:
            response = self.session.get(
                url, timeout=(10, 30), allow_redirects=False, stream=True
            )
        except requests.RequestException as exc:
            self.state["stop_kind"] = "rsshub_unavailable"
            raise PilotStopped("RSSHub недоступен: " + type(exc).__name__) from exc
        try:
            if response.status_code in (401, 403, 429):
                from .scraper import _retry_after

                retry_after = _retry_after(response.headers.get("Retry-After", ""))
                self.state["retry_not_before"] = datetime.now(
                    timezone.utc
                ).timestamp() + max(60, retry_after or 0)
                self.state["stop_kind"] = "rsshub_access"
                raise PilotStopped(
                    "Ограничение RSSHub: HTTP " + str(response.status_code)
                )
            if 300 <= response.status_code < 400:
                raise ValueError("RSSHub: перенаправление не разрешено")
            payload = bytearray()
            for chunk in response.iter_content(65536):
                payload.extend(chunk)
                if len(payload) > MAX_FEED_BYTES:
                    raise ValueError("RSSHub: лента превышает 2 МБ")
            if response.status_code >= 400:
                if re.search(
                    rb"Too Many Requests|FLOOD[_ ]?WAIT|403\s+Forbidden|(?:HTTP|status(?:Code)?)\W{0,8}(?:403|429)\b",
                    payload,
                    re.I,
                ):
                    self.state["stop_kind"] = "rsshub_access"
                    self.state["retry_not_before"] = (
                        datetime.now(timezone.utc).timestamp() + 60
                    )
                    raise PilotStopped(
                        "RSSHub сообщил об ограничении upstream Telegram"
                    )
                if response.status_code in (502, 503, 504):
                    self.state["stop_kind"] = "rsshub_unavailable"
                    raise PilotStopped(
                        "RSSHub недоступен: HTTP " + str(response.status_code)
                    )
                raise ValueError("RSSHub: HTTP " + str(response.status_code))
            sample = parse_feed(bytes(payload), username)
            sample["feed_url"] = url
            return sample
        except requests.RequestException as exc:
            self.state["stop_kind"] = "rsshub_unavailable"
            raise PilotStopped(
                "RSSHub прервал передачу ленты: " + type(exc).__name__
            ) from exc
        finally:
            response.close()


@contextmanager
def run_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "collector.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Этот --run уже используется другим сборщиком") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def collect(rows, directory, **kwargs):
    with run_lock(directory):
        return _collect(rows, directory, **kwargs)


def extend_time_window(state, hours, *, now=None):
    """Explicit operator permission: extend time only, never HTTP counters."""
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 8:
        raise ValueError("Продление допускается на 1–8 часов")
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if state.get("stopped") and state.get("stop_reason") != "Истёк лимит времени":
        raise ValueError(
            "Продление времени не снимает ограничение Telegram или остановку по запросам"
        )
    if state["deadline"] > now:
        raise ValueError("Срок запуска ещё не истёк; продление не требуется")
    if state["requests"] >= state["request_budget"]:
        raise ValueError(
            "Бюджет запросов исчерпан; продление времени не увеличивает его"
        )
    previous = state["deadline"]
    state.setdefault("time_extensions", []).append(
        dict(
            at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            previous_deadline=previous,
            new_deadline=now + hours * 3600,
            requests_used=state["requests"],
            hours=hours,
        )
    )
    state.update(deadline=now + hours * 3600, stopped=False)
    state.pop("stop_reason", None)


def resume_rsshub_run(state, base_url, *, now=None):
    """Explicit same-instance recovery, never reset budget or permit backend rotation."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if (
        state.get("transport") != "rsshub"
        or state.get("rsshub_url") != base_url
        or not state.get("stopped")
        or state.get("stop_kind") not in ("rsshub_access", "rsshub_unavailable")
    ):
        raise ValueError(
            "Продолжить можно только остановку RSSHub с тем же экземпляром и --run"
        )
    if now < state.get("retry_not_before", 0):
        raise ValueError("Ещё не истёк Retry-After/защитная пауза RSSHub")
    if state["requests"] >= state["request_budget"]:
        raise ValueError("Бюджет исчерпан; продолжение RSSHub его не увеличивает")
    state.setdefault("rsshub_resumptions", []).append(
        dict(
            at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            reason=state.get("stop_reason"),
            requests_used=state["requests"],
        )
    )
    state["stopped"] = False
    for key in ("stop_reason", "stop_kind", "retry_not_before"):
        state.pop(key, None)


def change_rsshub_url(state, base_url, *, now=None):
    """Explicit migration after unavailability only; access blocks forbid rotation."""
    from .rsshub_posts import normalize_base_url

    base_url = normalize_base_url(base_url)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if (
        state.get("transport") != "rsshub"
        or not state.get("rsshub_url")
        or not state.get("stopped")
        or state.get("stop_kind") != "rsshub_unavailable"
    ):
        raise ValueError(
            "Смена адреса разрешена только после недоступности RSSHub, не после 401/403/429 или ограничения Telegram"
        )
    if state["rsshub_url"] == base_url:
        raise ValueError(
            "Адрес RSSHub не изменился; используйте только --resume-rsshub"
        )
    if now < state.get("retry_not_before", 0):
        raise ValueError("Ещё не истёк Retry-After/защитная пауза RSSHub")
    if state["requests"] >= state["request_budget"]:
        raise ValueError("Бюджет исчерпан; смена адреса его не увеличивает")
    state.setdefault("rsshub_url_changes", []).append(
        dict(
            at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            previous=state["rsshub_url"],
            current=base_url,
            reason=state.get("stop_reason"),
            requests_used=state["requests"],
        )
    )
    state["rsshub_url"] = base_url


def _collect(
    rows,
    directory,
    *,
    http=False,
    database=True,
    maximum=8,
    request_budget=3000,
    hours=8,
    cache_directories=(),
    session=None,
    extend_hours=None,
    rsshub_url=None,
    switch_to_rsshub=False,
    resume_rsshub=False,
    change_rsshub=False,
):
    if rsshub_url is not None:
        from .rsshub_posts import normalize_base_url

        rsshub_url = normalize_base_url(rsshub_url)
    if http and rsshub_url:
        raise ValueError("Выберите прямой --http или --rsshub-url, не оба сразу")
    if switch_to_rsshub and not rsshub_url:
        raise ValueError("--switch-to-rsshub требует --rsshub-url")
    if resume_rsshub and not rsshub_url:
        raise ValueError("--resume-rsshub требует --rsshub-url")
    if change_rsshub and not (rsshub_url and resume_rsshub):
        raise ValueError(
            "--change-rsshub-url требует новый --rsshub-url и --resume-rsshub"
        )
    network = http or bool(rsshub_url)
    if not 3 <= maximum <= 8 or not 1 <= request_budget <= 3000 or not 1 <= hours <= 8:
        raise ValueError("Допустимо 3–8 постов, до 3000 запросов и до 8 часов")
    if not 1 <= len(rows) <= 6000 or len({r["username"] for r in rows}) != len(rows):
        raise ValueError("Нужен снимок до 6000 уникальных каналов")
    if any(
        not isinstance(r["username"], str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", r["username"])
        for r in rows
    ):
        raise ValueError("Некорректный публичный username в снимке")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    state_path, snapshot = directory / "collection.json", directory / "snapshot.json"
    identity = fingerprint(rows)
    if (
        snapshot.exists()
        and fingerprint(json.loads(snapshot.read_text(encoding="utf-8"))) != identity
    ):
        raise ValueError("В этом запуске уже зафиксирован другой каталог")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            state["snapshot_sha256"] != identity
            or state["maximum_posts"] != maximum
            or state["request_budget"] != request_budget
        ):
            raise ValueError("При продолжении нельзя менять снимок или лимиты")
        if change_rsshub:
            change_rsshub_url(state, rsshub_url)
        if resume_rsshub:
            resume_rsshub_run(state, rsshub_url)
        if extend_hours is not None:
            extend_time_window(state, extend_hours)
        if state.get("stopped") and network:
            raise ValueError(
                "Сбор остановлен: "
                + state["stop_reason"]
                + ". Лимиты не сбрасываются повторным запуском."
            )
    else:
        if resume_rsshub:
            raise ValueError("Продолжить RSSHub можно только в существующем запуске")
        if extend_hours is not None:
            raise ValueError("Продлить можно только уже существующий запуск")
        state = dict(
            format="atlas-catalog-collection-v11",
            snapshot_sha256=identity,
            maximum_posts=maximum,
            request_budget=request_budget,
            requests=0,
            requests_by_channel={},
            deadline=datetime.now(timezone.utc).timestamp() + hours * 3600,
            channels={},
            stopped=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        atomic_write_text(
            snapshot, json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n"
        )

    if network:
        requested = "rsshub" if rsshub_url else "telegram"
        previous = state.get("transport", "telegram")
        if previous != requested and state_path.exists():
            if not (
                switch_to_rsshub and requested == "rsshub" and previous == "telegram"
            ):
                raise ValueError(
                    "Для перехода существующего запуска укажите --switch-to-rsshub; счётчики сохраняются"
                )
            state.setdefault("transport_changes", []).append(
                dict(
                    at=datetime.now(timezone.utc).isoformat(),
                    previous=previous,
                    current=requested,
                    requests_used=state["requests"],
                )
            )
        if previous == "rsshub" and state.get("rsshub_url") != rsshub_url:
            raise ValueError(
                "Для смены недоступного RSSHub нужны --change-rsshub-url и --resume-rsshub; после ограничений доступа смена запрещена"
            )
        state["transport"] = requested
        if rsshub_url:
            state["rsshub_url"] = rsshub_url

    def save():
        atomic_write_text(
            state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )

    save()
    connection = None
    if database:
        try:
            from . import db

            connection = db.psycopg2.connect(**{**db.config.PG, "connect_timeout": 5})
            connection.set_session(readonly=True)
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '15s'")
            connection.commit()
        except Exception:
            if connection:
                connection.close()
            connection = None
            state["database_status"] = "unavailable_no_write_attempted"
    fetcher = (
        RSSHubFetcher(state, save, session, rsshub_url)
        if rsshub_url
        else CatalogFetcher(state, save, session)
        if http
        else None
    )
    try:
        for index, row in enumerate(rows):
            if datetime.now(timezone.utc).timestamp() >= state["deadline"]:
                state.update(stopped=True, stop_reason="Истёк лимит времени")
                break
            username = row["username"]
            target = directory / "posts" / (username + ".json")
            sample, posts, source = None, [], None
            for folder in [directory / "posts", *map(Path, cache_directories)]:
                path = folder / (username + ".json")
                if not path.exists():
                    continue
                try:
                    candidate = json.loads(path.read_text(encoding="utf-8"))
                    if (
                        candidate.get("username") != username
                        or candidate.get("purpose") != "classification_only"
                    ):
                        continue
                    found = suitable_posts(candidate, maximum)
                    if len(found) >= 3:
                        sample, posts, source = candidate, found, "cache"
                        break
                except (ValueError, KeyError, TypeError):
                    pass
            if not posts and connection:
                try:
                    candidate = sample_from_database(
                        username,
                        connection,
                        directory / "database-samples",
                        limit=maximum,
                        days=30,
                    )
                    found = suitable_posts(candidate, maximum)
                    if len(found) >= 3:
                        sample, posts, source = candidate, found, "database"
                    connection.rollback()
                except Exception:
                    connection.rollback()
            if not posts and fetcher and not state["requests_by_channel"].get(username):
                try:
                    candidate = (
                        fetcher(username)
                        if rsshub_url
                        else sample_channel(
                            username,
                            directory / "http-samples",
                            max_pages=1,
                            limit=maximum,
                            refresh=True,
                            fetcher=fetcher,
                        )
                    )
                    sample, posts, source = (
                        candidate,
                        suitable_posts(candidate, maximum),
                        "rsshub" if rsshub_url else "http",
                    )
                except PilotStopped as exc:
                    state.update(stopped=True, stop_reason=str(exc))
                    save()
                    break
                except Exception as exc:
                    state["channels"][username] = dict(
                        status="unavailable",
                        error=type(exc).__name__,
                        detail=str(exc)[:300],
                    )
            if sample is not None:
                result = dict(
                    sample,
                    posts=posts,
                    post_limit=maximum,
                    distinct_dates=len({p["date"][:10] for p in posts}),
                    low_diversity=len({p["date"][:10] for p in posts}) < 2,
                    classification_collection_sha256=identity,
                )
                atomic_write_text(
                    target, json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
                state["channels"][username] = dict(
                    status="usable" if len(posts) >= 3 else "insufficient_posts",
                    posts=len(posts),
                    source=source,
                )
            elif username not in state["channels"]:
                state["channels"][username] = dict(status="missing", posts=0)
            save()
            if (index + 1) % 25 == 0 or index + 1 == len(rows):
                print(
                    f"[{index + 1}/{len(rows)}] requests={state['requests']} usable={sum(x['status'] == 'usable' for x in state['channels'].values())}",
                    flush=True,
                )
    finally:
        if connection:
            connection.close()
        save()
    return state
