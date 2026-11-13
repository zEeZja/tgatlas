"""Возобновляемое расширение каталога: приоритеты, публичная проверка, insert-only."""

from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode, urlparse

from bs4 import BeautifulSoup
from psycopg2.extras import Json
import requests

from . import config, db, exporter
from .file_io import atomic_write_text
from .identity import normalize_username
from .parser import parse_channel_meta
from .public_catalogues import (
    PublicHTMLClient,
    SourceBudgetExhausted,
    SourceError,
    page_url,
    parse_official_links,
    parse_tgramsearch_detail,
    parse_tgramsearch_listing,
    parse_tlgrm_listing,
    public_username,
)
from .scraper import ScrapeError, fetch_channel_html
from .topic_keywords import load_classification_rules, resolve_topic

log = logging.getLogger(__name__)
PLAN = config.ROOT / "config/catalog_growth.json"
LOCK_ID = 0x41544C47524F57
STATE_VERSION = 2


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def build_jobs(plan, allow_other=False):
    jobs = [
        dict(source="official_website", url=url, pages=1, phase="priority")
        for url in dict.fromkeys(plan["official_pages"])
    ]
    jobs += [
        dict(
            source="tgramsearch",
            url="https://tgramsearch.com/search?" + urlencode({"query": q}),
            pages=plan.get("query_pages", 3),
            phase="priority",
            query=q,
        )
        for q in dict.fromkeys(plan["queries"])
    ]
    jobs += [
        dict(item)
        for item in plan["catalogues"]
        if allow_other or item["phase"] == "priority"
    ]
    for job in jobs:
        if (
            job["phase"] not in ("priority", "other")
            or not isinstance(job["pages"], int)
            or job["pages"] < 1
        ):
            raise ValueError("План содержит некорректную фазу или число страниц")
        if job["source"] not in ("official_website", "tgramsearch", "tlgrm"):
            raise ValueError("План содержит неизвестный источник")
    return sorted(jobs, key=lambda j: j["phase"] != "priority")


def priority_tier(prediction, plan):
    topic = prediction["subcategory"]
    return (
        0
        if topic in plan["priority_topics"]
        else 1
        if topic in plan["adjacent_topics"]
        else 2
    )


def decision(meta, plan, phase, rules):
    """Рубрика/запрос источника не подменяют содержимое актуального профиля."""
    if (
        not meta.get("name")
        or meta.get("subscribers") is None
        or meta.get("description") is None
    ):
        return "unverified", None
    if meta["subscribers"] < config.MIN_CHANNEL_SUBSCRIBERS:
        return "below_minimum", None
    text = str(meta["name"]) + " " + str(meta["description"])
    if re.search(
        r"many users reported.*(?:scam|fake)|(?:продаж\w*|аренд\w*|куплю).{0,50}(?:банковских карт|аккаунтов банков|дропов)",
        text,
        re.I,
    ):
        return "deferred_risk", None
    prediction = resolve_topic(meta, **rules)
    if prediction["subcategory"] == "other" or prediction["topic_review_required"]:
        return "deferred_review", prediction
    if phase == "priority" and priority_tier(prediction, plan) > 1:
        return "deferred_topic", prediction
    return "eligible", prediction


def store_verified_candidate(conn, result, card, outcome, prediction, run_id, phase):
    """Не использовать общий upsert: он умеет обновлять уже импортированный канал.

    Этот SQL никогда не меняет channels или историческую imported-связь, даже
    если другой импортёр успел добавить канал между запросом Telegram и записью.
    Отложенные темы не становятся validated для обхода приоритетного фильтра.
    """
    username = result["username"]
    meta = result.get("meta", {})
    status = (
        "validated"
        if outcome == "eligible"
        else "rejected"
        if outcome == "below_minimum"
        else "pending"
    )
    raw = dict(
        growth_run_id=run_id,
        growth_phase=phase,
        growth_decision=outcome,
        growth_verified_at=utcnow(),
        growth_source=card["source"],
        growth_topic=prediction["subcategory"] if prediction else None,
        catalog_name=card.get("name"),
        catalog_subscribers=card.get("catalog_subscribers"),
    )
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO channel_candidates AS c
            (username, name, description, subscribers, status, validation_note, validated_at, raw_data)
            SELECT %s,%s,%s,%s,%s,%s,CASE WHEN %s='validated' THEN NOW() ELSE NULL END,%s
             WHERE NOT EXISTS (SELECT 1 FROM channels WHERE username=%s)
            ON CONFLICT (username) DO UPDATE SET
                name=EXCLUDED.name, description=EXCLUDED.description,
                subscribers=EXCLUDED.subscribers, status=EXCLUDED.status,
                validation_note=EXCLUDED.validation_note, validated_at=EXCLUDED.validated_at,
                raw_data=c.raw_data || EXCLUDED.raw_data, updated_at=NOW(), last_seen_at=NOW()
             WHERE c.imported_channel_id IS NULL AND c.status <> 'imported'
               AND NOT EXISTS (SELECT 1 FROM channels WHERE username=EXCLUDED.username)
            RETURNING username""",
            (
                username,
                meta.get("name"),
                meta.get("description"),
                meta.get("subscribers"),
                status,
                None if outcome == "eligible" else "growth:" + outcome,
                status,
                Json(raw),
                username,
            ),
        )
        if not cursor.fetchone():
            return False
        cursor.execute(
            """INSERT INTO channel_candidate_sources
            (username, source_name, source_ref, source_url, raw_data)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (username,source_name,source_ref) DO UPDATE SET
                last_seen_at=NOW(), raw_data=channel_candidate_sources.raw_data || EXCLUDED.raw_data""",
            (
                username,
                card["source"],
                card.get("detail_url") or card["source_url"],
                card["source_url"],
                Json(raw),
            ),
        )
    return True


def import_verified_new(conn, username, minimum):
    """Только новая запись, свежая проверка и положительное решение фильтра."""
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO channels
            (username, name, description, subscribers, source, last_meta_at)
            SELECT username, name, description, subscribers, 'catalog_growth', NOW()
              FROM channel_candidates
             WHERE username=%s AND status='validated' AND imported_channel_id IS NULL
               AND subscribers >= %s AND name IS NOT NULL AND description IS NOT NULL
               AND validated_at >= NOW() - INTERVAL '1 hour'
               AND raw_data->>'growth_decision'='eligible'
            ON CONFLICT DO NOTHING RETURNING id""",
            (username, minimum),
        )
        row = cursor.fetchone()
        if not row:
            return False
        cursor.execute(
            """INSERT INTO channel_sources
            (channel_id, source_name, source_ref, source_url, raw_data)
            SELECT %s, source_name, source_ref, source_url, raw_data
              FROM channel_candidate_sources WHERE username=%s
            ON CONFLICT DO NOTHING""",
            (row[0], username),
        )
        cursor.execute(
            """UPDATE channel_candidates SET status='imported',
            imported_at=NOW(), imported_channel_id=%s, validation_note=NULL, updated_at=NOW()
            WHERE username=%s""",
            (row[0], username),
        )
        return True


def _channel_count(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM channels WHERE is_active")
        return cursor.fetchone()[0]


def revisit(record, phase):
    return bool(
        record
        and (
            (record.get("outcome") == "eligible" and record.get("phase") == phase)
            or (
                phase == "other"
                and record.get("phase") == "priority"
                and record.get("outcome") == "deferred_topic"
            )
        )
    )


class ProfileClaims:
    """Один запрос профиля на username/фазу, включая дубли разных карточек."""

    def __init__(self, known, processed, maximum):
        self.known = set(known)
        self.processed = dict(processed)
        self.maximum = maximum
        self.used = 0
        self.claimed = set()
        self.lock = threading.Lock()

    def claim(self, username, phase):
        with self.lock:
            if username in self.known:
                return "existing"
            record = self.processed.get(username)
            if (record and not revisit(record, phase)) or (
                phase,
                username,
            ) in self.claimed:
                return "duplicate"
            if self.used >= self.maximum:
                return "budget_deferred"
            self.claimed.add((phase, username))
            self.used += 1
            return None

    def finish(self, username, record, known=False):
        with self.lock:
            self.processed[username] = record
            if known:
                self.known.add(username)


def _verify(card, client, claims, phase):
    result = dict(card)
    try:
        if not card.get("username"):
            detail = parse_tgramsearch_detail(
                client.get(card["detail_url"]), card["detail_url"]
            )
            if not detail:
                return {**result, "outcome": "no_public_username"}
            result.update(detail)
        username = normalize_username(result.get("username"))
        if not username or username.endswith("bot"):
            return {**result, "outcome": "not_public_channel"}
        result["username"] = username
        outcome = claims.claim(username, phase)
        if outcome:
            return {**result, "outcome": outcome}
        html = fetch_channel_html(username, profile=True)
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one(".tgme_badge_scam, .tgme_badge_fake"):
            return {**result, "outcome": "deferred_risk"}
        canonical = soup.select_one('meta[property="og:url"]')
        actual = public_username(canonical.get("content")) if canonical else ""
        if actual and actual != username:
            return {**result, "outcome": "identity_mismatch"}
        meta = parse_channel_meta(html)
        if meta.get("subscribers") is None:
            return {**result, "outcome": "not_public_channel"}
        return {
            **result,
            "meta": {**meta, "username": username},
            "outcome": "verified_response",
        }
    except ScrapeError as exc:
        return {
            **result,
            "outcome": "unavailable" if exc.permanent else "retry",
            "error": str(exc)[:250],
        }
    except (requests.RequestException, SourceError) as exc:
        return {**result, "outcome": "retry", "error": str(exc)[:250]}


class GrowthRun:
    def __init__(
        self,
        conn,
        plan,
        jobs,
        state,
        state_file,
        *,
        target,
        workers,
        max_profiles,
        max_requests,
        max_minutes,
    ):
        self.conn, self.plan, self.jobs = conn, plan, jobs
        self.state, self.state_file = state, state_file
        self.target, self.workers = target, workers
        self.current = _channel_count(conn)
        self.claims = ProfileClaims(
            db.get_known_usernames(conn), state["processed"], max_profiles
        )
        self.rules = load_classification_rules()
        self.client = PublicHTMLClient(
            {urlparse(j["url"]).hostname for j in jobs},
            state_file.parent / "cache",
            max_requests=max_requests,
        )
        self.started, self.max_seconds = time.monotonic(), max_minutes * 60
        self.counts, self.topic_counts = Counter(), Counter()
        self.since_save = 0
        self.attempted_cards = set()

    def budget(self):
        return (
            self.current >= self.target
            or self.claims.used >= self.claims.maximum
            or self.client.requests >= self.client.max_requests
            or time.monotonic() - self.started >= self.max_seconds
        )

    def save(self, status=None):
        state = self.state
        if status:
            state["status"] = status
        self.current = _channel_count(self.conn)
        with self.conn.cursor() as cursor:
            cursor.execute(
                """SELECT c.raw_data->>'growth_topic',c.raw_data->>'growth_source',count(*)
                FROM channel_candidates c JOIN channels ch ON ch.id=c.imported_channel_id
                WHERE c.status='imported' AND c.raw_data->>'growth_run_id'=%s
                GROUP BY 1,2""",
                (state["run_id"],),
            )
            topics, sources = Counter(), Counter()
            for topic, source, number in cursor.fetchall():
                topics[topic or "unknown"] += number
                sources[source or "unknown"] += number
        state.update(
            current_count=self.current,
            target=self.target,
            updated_at=utcnow(),
            source_requests_this_run=self.client.requests,
            profile_requests_this_run=self.claims.used,
            this_run=dict(self.counts),
            imported_topics_this_run=dict(self.topic_counts),
            total_imported=sum(topics.values()),
            imported_topics=dict(topics),
            imported_sources=dict(sources),
        )
        self.conn.commit()
        atomic_write_text(
            self.state_file, json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )
        self.since_save = 0

    def skip_card(self, card, phase):
        key = card.get("detail_url") or card.get("username")
        if (phase, key) in self.attempted_cards:
            return True
        prior = self.state["cards"].get(key, {})
        username = card.get("username") or prior.get("username")
        record = self.state["processed"].get(username)
        if username in self.claims.known:
            return True
        if revisit(record, phase):
            return False
        return bool(record or prior)

    def consume(self, result, card, phase):
        outcome, username = result["outcome"], result.get("username")
        key = card.get("detail_url") or username
        if outcome == "budget_deferred":
            return False
        if outcome == "retry":
            self.state["retry_cards"][key] = {
                **card,
                "username": username,
                "phase": phase,
            }
            self.counts["temporary_errors"] += 1
            log.warning("Повтор позже: %s (%s)", key, result.get("error", ""))
            return True
        prediction = None
        if outcome == "verified_response":
            outcome, prediction = decision(result["meta"], self.plan, phase, self.rules)
            with db.transaction(self.conn):
                stored = store_verified_candidate(
                    self.conn,
                    result,
                    card,
                    outcome,
                    prediction,
                    self.state["run_id"],
                    phase,
                )
                if not stored:
                    outcome = "existing_or_historical"
                elif outcome == "eligible" and self.current < self.target:
                    if import_verified_new(
                        self.conn, username, config.MIN_CHANNEL_SUBSCRIBERS
                    ):
                        self.current += 1
                        outcome = "imported"
                        self.topic_counts[prediction["subcategory"]] += 1
                        log.info(
                            "Добавлен @%s · %s · %d подписчиков · %d/%d",
                            username,
                            prediction["subcategory"],
                            result["meta"]["subscribers"],
                            self.current,
                            self.target,
                        )
                    else:
                        outcome = "existing_or_historical"
        record = dict(
            outcome=outcome,
            phase=phase,
            at=utcnow(),
            username=username,
            topic=prediction["subcategory"] if prediction else None,
            card={**card, "username": username},
        )
        self.state["cards"][key] = {k: v for k, v in record.items() if k != "card"}
        # A duplicate finishing last must not replace an imported/deferred result.
        if username and outcome != "duplicate":
            self.state["processed"][username] = record
            self.claims.finish(
                username,
                record,
                known=outcome in ("imported", "existing", "existing_or_historical"),
            )
        self.state["retry_cards"].pop(key, None)
        self.counts[outcome] += 1
        self.since_save += 1
        if self.since_save >= 10:
            self.save()
        if outcome == "imported" and self.counts["imported"] % 50 == 0:
            exporter.export_channels(self.conn, db)
            self.conn.commit()
        return True

    def process_cards(self, cards, phase):
        queue, pending = deque(cards), {}
        complete = True
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            while queue or pending:
                while queue and len(pending) < self.workers:
                    card = queue[0]
                    if self.skip_card(card, phase):
                        queue.popleft()
                        continue
                    if self.budget():
                        break
                    queue.popleft()
                    self.attempted_cards.add(
                        (phase, card.get("detail_url") or card.get("username"))
                    )
                    pending[
                        pool.submit(_verify, card, self.client, self.claims, phase)
                    ] = card
                if not pending:
                    break
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    card = pending.pop(future)
                    complete &= self.consume(future.result(), card, phase)
        return complete and not queue

    def priority_complete(self):
        return all(
            j["url"] in self.state["completed_jobs"]
            for j in self.jobs
            if j["phase"] == "priority"
        ) and not any(
            c["phase"] == "priority" for c in self.state["retry_cards"].values()
        )

    def collect_job(self, job):
        state, base, phase = self.state, job["url"], job["phase"]
        if base in state["completed_jobs"]:
            return
        for page in range(1, job["pages"] + 1):
            url = page_url(base, page)
            if url in state["completed_pages"]:
                continue
            if self.budget():
                return
            try:
                html = self.client.get(url, max_age_hours=24)
                parser = {
                    "tgramsearch": parse_tgramsearch_listing,
                    "tlgrm": parse_tlgrm_listing,
                    "official_website": parse_official_links,
                }[job["source"]]
                listing = (
                    parser(html, url)
                    if job["source"] == "official_website"
                    else parser(html, url, config.MIN_CHANNEL_SUBSCRIBERS)
                )
                log.info(
                    "%s · %s · страница %d · кандидатов %d",
                    phase,
                    base,
                    page,
                    len(listing.cards),
                )
                cards = [{**c, "job_url": base} for c in listing.cards]
                if not self.process_cards(cards, phase):
                    self.save()
                    return
                state["completed_pages"][url] = True
                if page >= listing.last_page or page == job["pages"]:
                    state["completed_jobs"][base] = dict(last_page=page, at=utcnow())
                    state["job_errors"].pop(base, None)
                    self.save()
                    return
                self.save()
            except SourceBudgetExhausted:
                self.save()
                return
            except (requests.RequestException, SourceError) as exc:
                log.warning("Источник недоступен: %s (%s)", url, str(exc)[:200])
                state["job_errors"][base] = dict(
                    url=url, phase=phase, at=utcnow(), error=str(exc)[:250]
                )
                self.save()
                return

    def execute(self):
        self.save("running")
        for phase in ("priority", "other"):
            jobs = [j for j in self.jobs if j["phase"] == phase]
            if not jobs or self.budget():
                break
            if phase == "other" and not self.priority_complete():
                log.warning("Остальные темы отложены: приоритетный этап не завершён")
                break
            self.state["phase"] = phase
            # Retry by phase, not by pagination URL: search URLs contain &page=.
            retries = [
                c for c in self.state["retry_cards"].values() if c["phase"] == phase
            ]
            self.process_cards(retries, phase)
            deferred = [
                r["card"] for r in self.state["processed"].values() if revisit(r, phase)
            ]
            self.process_cards(deferred, phase)
            for job in jobs:
                if self.budget():
                    break
                self.collect_job(job)
        exporter.export_channels(self.conn, db)
        self.conn.commit()
        status = (
            "target_reached"
            if self.current >= self.target
            else "budget_exhausted"
            if self.budget()
            else "paused_for_retry"
            if self.state["retry_cards"] or self.state["job_errors"]
            else "sources_exhausted"
        )
        self.save(status)
        return {
            k: self.state[k]
            for k in (
                "status",
                "initial_count",
                "current_count",
                "target",
                "total_imported",
                "this_run",
                "imported_topics",
                "imported_sources",
                "updated_at",
            )
        }


def run(
    *,
    target=3000,
    state_path="reports/catalog-growth/state.json",
    plan_path=PLAN,
    apply=False,
    allow_other=False,
    workers=4,
    max_profiles=6000,
    max_requests=12000,
    max_minutes=180,
):
    if (
        not 1 <= workers <= 8
        or target < 1
        or max_profiles < 1
        or max_requests < 1
        or not 1 <= max_minutes <= 1440
    ):
        raise ValueError("Некорректные ограничения запуска")
    plan_bytes = Path(plan_path).read_bytes()
    plan = json.loads(plan_bytes)
    jobs = build_jobs(plan, allow_other)
    state_file = Path(state_path)
    if not state_file.is_absolute():
        state_file = config.ROOT / state_file
    conn, locked, runner = db.connect(), False, None
    try:
        current = _channel_count(conn)
        if not apply:
            return dict(
                applied=False,
                current=current,
                target=target,
                missing=max(0, target - current),
                jobs=len(jobs),
                priority_topics=plan["priority_topics"],
                adjacent_topics=plan["adjacent_topics"],
                allow_other=allow_other,
                minimum_subscribers=config.MIN_CHANNEL_SUBSCRIBERS,
            )
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
            locked = cursor.fetchone()[0]
        conn.commit()
        if not locked:
            raise ValueError("Другой процесс расширения каталога уже работает")
        fingerprint = hashlib.sha256(plan_bytes).hexdigest()
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if (
                state.get("version") != STATE_VERSION
                or state.get("plan_sha256") != fingerprint
                or state.get("minimum_subscribers") != config.MIN_CHANNEL_SUBSCRIBERS
            ):
                raise ValueError(
                    "План, формат состояния или порог изменились: используйте новый --state"
                )
        else:
            state = dict(
                version=STATE_VERSION,
                run_id=utcnow(),
                started_at=utcnow(),
                initial_count=current,
                plan_sha256=fingerprint,
                minimum_subscribers=config.MIN_CHANNEL_SUBSCRIBERS,
                completed_jobs={},
                completed_pages={},
                processed={},
                cards={},
                retry_cards={},
                job_errors={},
            )
        runner = GrowthRun(
            conn,
            plan,
            jobs,
            state,
            state_file,
            target=target,
            workers=workers,
            max_profiles=max_profiles,
            max_requests=max_requests,
            max_minutes=max_minutes,
        )
        return runner.execute()
    except BaseException:
        conn.rollback()
        if runner is not None:
            runner.save("interrupted")
        raise
    finally:
        if locked:
            conn.rollback()
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()
        conn.close()
