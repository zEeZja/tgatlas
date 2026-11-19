"""Ограниченный исследовательский пилот: не обучение и не рабочий сборщик."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from .classification_samples import sample_channel
from .file_io import atomic_write_text


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def select_pilot(rows, predictions, limit=120):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 120:
        raise ValueError("Пилот ограничен 120 каналами")
    buckets = defaultdict(list)
    seen = set()
    for row in rows:
        username = row["username"]
        if username in seen:
            raise ValueError("Дубликат канала")
        seen.add(username)
        if username not in predictions:
            continue
        buckets[row["subcategory"]].append(row)
    # Each existing topic first; alternate agreement/disagreement when available.
    for topic, bucket in buckets.items():
        changed = [
            r for r in bucket if predictions[r["username"]]["subcategory"] != topic
        ]
        same = [r for r in bucket if predictions[r["username"]]["subcategory"] == topic]
        changed.sort(key=lambda r: digest([42, r["username"]]))
        same.sort(key=lambda r: digest([42, r["username"]]))
        ordered = []
        while changed or same:
            if changed:
                ordered.append(changed.pop(0))
            if same:
                ordered.append(same.pop(0))
        buckets[topic] = ordered
    selected = []
    while len(selected) < limit and any(buckets.values()):
        for topic in sorted(buckets):
            if buckets[topic] and len(selected) < limit:
                row = dict(buckets[topic].pop(0))
                row["v10_proposal"] = predictions[row["username"]]["subcategory"]
                selected.append(row)
    return selected


class PilotStopped(Exception):
    pass


class BoundedFetcher:
    """No retries or redirects; reserve each request before sending it."""

    def __init__(self, state, save, session, interval=6.0):
        self.state, self.save, self.session = state, save, session
        self.interval = max(6.0, interval)
        self.previous = None

    def __call__(self, username, before=None):
        if not isinstance(username, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,31}", username
        ):
            raise ValueError("Некорректный публичный username")
        if self.state["requests"] >= self.state["request_budget"]:
            raise PilotStopped("Исчерпан бюджет запросов")
        per_channel = self.state.setdefault("requests_by_channel", {})
        if per_channel.get(username, 0) >= 2:
            raise ValueError("Исчерпан бюджет двух страниц канала")
        if self.previous is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.previous)))
        self.state["requests"] += 1
        per_channel[username] = per_channel.get(username, 0) + 1
        self.save()
        self.previous = time.monotonic()
        response = self.session.get(
            "https://t.me/s/" + username,
            params={"before": before} if before else None,
            timeout=20,
            allow_redirects=False,
        )
        if response.status_code in (403, 429):
            raise PilotStopped(
                "Telegram ограничил доступ: HTTP " + str(response.status_code)
            )
        if 300 <= response.status_code < 400:
            raise ValueError("Публичное превью перенаправляет на другую страницу")
        response.raise_for_status()
        return response.text


def collect_pilot(plan, directory, session):
    if not 1 <= len(plan) <= 120 or len({r["username"] for r in plan}) != len(plan):
        raise ValueError("Пилот должен содержать до 120 уникальных каналов")
    directory = Path(directory)
    state_path = directory / "collection.json"
    identity = digest(plan)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["plan_sha256"] != identity:
            raise ValueError("Нельзя продолжать сбор с другим планом")
        if state.get("stopped"):
            raise ValueError(
                "Пилот остановлен; для нового сбора нужно отдельное согласование"
            )
        if state["requests"] and "requests_by_channel" not in state:
            raise ValueError(
                "Старый журнал не учитывает запросы по каналам; автоматическое возобновление запрещено"
            )
    else:
        if not 1 <= len(plan) <= 120:
            raise ValueError("Недопустимый размер пилота")
        state = dict(
            format="atlas-taxonomy-pilot-v1",
            plan_sha256=identity,
            requests=0,
            request_budget=len(plan) * 2,
            channels={},
            started_at=datetime.now(timezone.utc).isoformat(),
            stopped=False,
        )

    def save():
        atomic_write_text(
            state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )

    save()
    fetcher = BoundedFetcher(state, save, session)
    for row in plan:
        username = row["username"]
        if username in state["channels"]:
            continue
        try:
            result = sample_channel(
                username,
                directory / "posts",
                max_pages=2,
                limit=20,
                refresh=True,
                fetcher=fetcher,
            )
            state["channels"][username] = dict(
                status="ok",
                posts=len(result["posts"]),
                dates=result["distinct_dates"],
                low_diversity=result["low_diversity"],
            )
        except PilotStopped as exc:
            state.update(stopped=True, stop_reason=str(exc))
            save()
            break
        except Exception as exc:
            # Never retry failed channels within the agreed pilot budget.
            state["channels"][username] = dict(
                status="unavailable", error=str(exc)[:500]
            )
        save()
        print(
            f"[{len(state['channels'])}/{len(plan)}] @{username}: {state['channels'][username]['status']}; requests={state['requests']}",
            flush=True,
        )
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save()
    return state
