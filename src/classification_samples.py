"""Bounded public post samples for classification. Never import or write the DB."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .file_io import atomic_write_text
from .parser import parse_channel_page, parse_channel_meta
from .scraper import fetch_channel_html, ScrapeError
from .topic_keywords import classify_topic, TOPICS
from .title_region import find_title_regions

AD = re.compile(
    r"(?:^\s*#реклама\b|^\s*реклама\s*[:👉]|erid\s*[:=]|на правах рекламы)", re.I
)


def sample_channel(
    username,
    cache_dir="reports/classification-samples",
    max_pages=2,
    limit=12,
    max_age_hours=24,
    refresh=False,
):
    username = str(username).strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
        raise ValueError("Нужен публичный username без пути")
    if not 1 <= max_pages <= 3 or not 3 <= limit <= 20 or not 0 <= max_age_hours <= 168:
        raise ValueError("Допустимо 1–3 страницы, 3–20 постов, кеш 0–168 часов")
    target = Path(cache_dir) / f"{username}.json"
    now = datetime.now(timezone.utc)
    if target.exists() and not refresh:
        cached = json.loads(target.read_text(encoding="utf-8"))
        age = (
            now - datetime.fromisoformat(cached["fetched_at"])
        ).total_seconds() / 3600
        if (
            cached.get("username") == username
            and cached.get("page_budget") == max_pages
            and cached.get("post_limit") == limit
            and 0 <= age <= max_age_hours
        ):
            return {**cached, "cache_hit": True}
    before = None
    seen_ids = set()
    seen_text = set()
    candidates = []
    pages = 0
    excluded_ads = duplicates = 0
    for _ in range(max_pages):
        html = fetch_channel_html(username, before=before)
        pages += 1
        posts = parse_channel_page(html, username, 0)
        if not posts:
            if not parse_channel_meta(html).get("name"):
                raise ScrapeError(
                    "Ответ не похож на публичный канал; предыдущая выборка сохранена"
                )
            break
        oldest = min(post.tg_message_id for post in posts)
        if before is not None and oldest >= before:
            break
        for post in posts:
            text = (post.text or "").strip()
            if post.tg_message_id in seen_ids:
                continue
            seen_ids.add(post.tg_message_id)
            if len(text) < 40:
                continue
            if AD.search(text):
                excluded_ads += 1
                continue
            fingerprint = hashlib.sha256(
                re.sub(r"\s+", " ", text).casefold().encode()
            ).hexdigest()
            if fingerprint in seen_text:
                duplicates += 1
                continue
            seen_text.add(fingerprint)
            candidates.append(
                dict(
                    message_id=post.tg_message_id,
                    text=text[:4000],
                    truncated=len(text) > 4000,
                    date=post.posted_at.isoformat() if post.posted_at else None,
                    url=post.url,
                    text_hash=fingerprint,
                )
            )
        before = oldest
    if not candidates:
        raise ValueError("Содержательных сообщений не найдено; старый кеш не заменён")
    # Round-robin across dates within the bounded window; do not pretend that
    # three pages necessarily cover weeks in a frequently updated channel.
    buckets = {}
    for post in sorted(candidates, key=lambda p: p["message_id"], reverse=True):
        buckets.setdefault((post["date"] or "")[:10], []).append(post)
    chosen = []
    while len(chosen) < limit and any(buckets.values()):
        for bucket in buckets.values():
            if bucket and len(chosen) < limit:
                chosen.append(bucket.pop(0))
    days = len({p["date"][:10] for p in chosen if p["date"]})
    result = dict(
        version=1,
        purpose="classification_only",
        username=username,
        fetched_at=now.isoformat(),
        page_budget=max_pages,
        post_limit=limit,
        pages_requested=pages,
        posts=chosen,
        distinct_dates=days,
        low_diversity=days < 2,
        excluded_ads=excluded_ads,
        duplicate_texts=duplicates,
        cache_hit=False,
    )
    atomic_write_text(target, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def evidence(sample):
    votes = Counter()
    mentions = Counter()
    unique = set()
    dates = set()
    for post in sample.get("posts", []):
        text = post.get("text", "")
        fingerprint = hashlib.sha256(
            re.sub(r"\s+", " ", text).casefold().encode()
        ).hexdigest()
        if len(text.strip()) < 40 or fingerprint in unique or AD.search(text):
            continue
        unique.add(fingerprint)
        try:
            dates.add(
                datetime.fromisoformat(
                    str(post.get("date") or "").replace("Z", "+00:00")
                )
                .date()
                .isoformat()
            )
        except ValueError:
            pass
        result = classify_topic(description=text)
        if result["subcategory"] != "other" and not result["review_required"]:
            votes[result["subcategory"]] += 1
        for region in find_title_regions(text, True):
            mentions[region] += 1
    best, count = votes.most_common(1)[0] if votes else ("other", 0)
    agreement = count / len(unique) if unique else 0
    return dict(
        topic=best,
        votes=count,
        agreement=agreement,
        usable_posts=len(unique),
        distinct_dates=len(dates),
        suggestion_only=True,
        eligible=count >= 3
        and agreement >= 0.6
        and len(dates) >= 2
        and not sample.get("low_diversity", True),
        mentioned_regions=dict(mentions),
        note="Упоминание места в посте не определяет территориальную направленность канала",
    )


def sample_from_database(username, conn, cache_dir="reports/classification-samples", limit=12, days=30):
    """Read a bounded local sample; never modify posts, watermarks or session settings."""
    username = str(username).strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username) or not 3 <= limit <= 20 or not 1 <= days <= 90:
        raise ValueError("Нужен username, 3–20 постов и период 1–90 дней")
    with conn.cursor() as cur:
        cur.execute("""SELECT p.tg_message_id, p.text, p.posted_at, p.url
            FROM posts p JOIN channels c ON c.id = p.channel_id
            WHERE lower(c.username) = %s AND p.posted_at >= NOW() - %s * INTERVAL '1 day'
            ORDER BY p.posted_at DESC, p.tg_message_id DESC LIMIT 500""", (username, days))
        rows = cur.fetchall()
    buckets, seen = {}, set()
    excluded_ads = duplicates = 0
    for message_id, text, date, url in rows:
        text = (text or "").strip()
        if len(text) < 40:
            continue
        if AD.search(text):
            excluded_ads += 1
            continue
        fingerprint = hashlib.sha256(re.sub(r"\s+", " ", text).casefold().encode()).hexdigest()
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        buckets.setdefault(date.date().isoformat(), []).append(dict(
            message_id=message_id, text=text[:4000], truncated=len(text) > 4000,
            date=date.isoformat(), url=url, text_hash=fingerprint))
    if not buckets:
        raise ValueError("В БД нет пригодных постов за выбранный период. Для отдельного HTTP-сбора явно укажите --source http.")
    # Evenly sample available dates, not only the latest high-volume event.
    chosen = []
    while len(chosen) < limit and any(buckets.values()):
        for bucket in buckets.values():
            if bucket and len(chosen) < limit:
                chosen.append(bucket.pop(0))
    dates = {p["date"][:10] for p in chosen}
    result = dict(version=2, purpose="classification_only", source="database", username=username,
                  fetched_at=datetime.now(timezone.utc).isoformat(), lookback_days=days,
                  post_limit=limit, rows_examined=len(rows), posts=chosen,
                  distinct_dates=len(dates), low_diversity=len(dates) < 2,
                  excluded_ads=excluded_ads, duplicate_texts=duplicates, cache_hit=False)
    atomic_write_text(Path(cache_dir) / f"{username}.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def compare(review_path, cache_dir):
    from .classification_review import read_review, automatic_prediction, metrics

    rows = [r for r in read_review(review_path, dimension="topic") if r["split"] == "test"]
    if not rows:
        raise ValueError("Для сравнения нужны подтверждённые test-строки")
    baseline = [automatic_prediction(r) for r in rows]
    alternative = []
    used = 0
    details = []
    for row, prediction in zip(rows, baseline):
        proposal = dict(prediction)
        path = Path(cache_dir) / f"{row['username']}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                data.get("username") != row["username"]
                or data.get("purpose") != "classification_only"
            ):
                raise ValueError("Кеш не соответствует каналу или назначению")
            extra = evidence(data)
            details.append(dict(username=row["username"], **extra))
            if (
                prediction["topic_review_required"]
                and extra["eligible"]
                and extra["topic"] != prediction["subcategory"]
            ):
                proposal["subcategory"] = extra["topic"]
                proposal["topic_group"] = TOPICS[extra["topic"]]["group"]
                used += 1
        alternative.append(proposal)
    return dict(
        active=False,
        metadata=metrics(rows, baseline),
        metadata_with_post_suggestions=metrics(rows, alternative),
        changed_suggestions=used,
        details=details,
        warning="Это предложения действующих правил по постам, не обученная смысловая модель. Упоминания территорий не применяются к географии канала.",
        applied=0,
    )
