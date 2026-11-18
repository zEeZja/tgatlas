"""Profile → bounded post evidence → semantic prediction; never write news state."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .classification_text import informative_description
from .semantic_model import combine, head_predict, profile_hash, profile_text
from .topic_keywords import TOPICS, GROUPS, load_classification_rules, canonical
from .geography import resolve_geography
from .classification_samples import AD, sample_channel, sample_from_database
from .file_io import atomic_write_text

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "reports/semantic-v9/catalog.json"


def cleaned_profile(row):
    """Founder/registration references describe relations, not the channel subject."""
    lines = []
    for line in informative_description(row.get("description")).splitlines():
        line = re.split(r"(?i)учредител\w*\s*(?:организации)?\s*[-—:]", line)[0]
        if re.fullmatch(
            r"(?i)[\W_]*(?:наш сайт|главный канал|контакты|чат|бот|админ|реклама|связь)[\W_]*",
            line,
        ):
            continue
        if line.strip():
            lines.append(line.strip())
    return {**row, "description": "\n".join(lines)}


def needs_posts(row, prediction=None):
    reasons = []
    desc = cleaned_profile(row)["description"]
    if len(desc) < 100 or len(re.findall(r"[а-яa-z]{3,}", desc.lower())) < 12:
        reasons.append("sparse_profile")
    if re.search(r"(?i)официальн|учредител|корпоративн", profile_text(row)):
        reasons.append("organization_context")
    if re.search(r"(?i)пост|публикаци", row.get("note", "")):
        reasons.append("owner_requested_posts")
    if prediction and (
        prediction[0]["score"] < 0.60
        or prediction[0]["score"] - prediction[1]["score"] < 0.15
    ):
        reasons.append("uncertain_profile_model")
    return reasons


def usable_posts(sample):
    """Do not trust cache diversity flags: recompute dates and duplicate content."""
    seen = set()
    posts = []
    dates = set()
    for p in sample.get("posts", []):
        text = str(p.get("text") or "").strip()
        key = " ".join(text.casefold().split())
        if len(text) < 40 or AD.search(text) or key in seen:
            continue
        try:
            date = (
                datetime.fromisoformat(str(p.get("date") or "").replace("Z", "+00:00"))
                .date()
                .isoformat()
            )
        except ValueError:
            continue
        seen.add(key)
        dates.add(date)
        posts.append({**p, "text": text[:4000]})
    return (posts[:12] if len(posts) >= 3 and len(dates) >= 2 else []), dict(
        usable=len(posts), dates=len(dates)
    )


class PostProvider:
    def __init__(
        self,
        cache_dir="reports/classification-samples",
        conn=None,
        allow_http=False,
        max_http_channels=0,
        pages=2,
    ):
        self.cache_dir = Path(cache_dir)
        self.conn = conn
        self.allow_http = allow_http
        self.maximum = max_http_channels
        self.pages = pages
        self.http_used = 0

    def __call__(self, row):
        username = str(row["username"]).lower().lstrip("@")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
            raise ValueError("Некорректный username")
        detail = dict(requested=True, source=None, status="unavailable", errors=[])

        def inspect(sample, source):
            if (
                sample.get("username") != username
                or sample.get("purpose") != "classification_only"
            ):
                raise ValueError("Кеш не соответствует каналу")
            fetched = datetime.fromisoformat(sample["fetched_at"])
            if (
                fetched.tzinfo is None
                or not 0
                <= (datetime.now(timezone.utc) - fetched).total_seconds()
                <= 86400
            ):
                detail["status"] = "stale_cache"
                return []
            posts, quality = usable_posts(sample)
            detail.update(
                source=source,
                fetched_at=sample["fetched_at"],
                **quality,
                status="usable" if posts else "insufficient_diversity",
            )
            return posts

        path = self.cache_dir / (username + ".json")
        if path.exists():
            try:
                posts = inspect(json.loads(path.read_text(encoding="utf-8")), "cache")
                if posts:
                    return posts, detail
            except (ValueError, KeyError, OSError):
                detail["errors"].append("invalid_cache")
        if self.conn is not None:
            try:
                posts = inspect(
                    sample_from_database(
                        username, self.conn, str(self.cache_dir), limit=12, days=30
                    ),
                    "database",
                )
                if posts:
                    return posts, detail
            except Exception as exc:
                self.conn.rollback()
                detail["errors"].append("database:" + type(exc).__name__)
        if self.allow_http and self.http_used < self.maximum:
            self.http_used += 1
            try:
                posts = inspect(
                    sample_channel(
                        username, str(self.cache_dir), max_pages=self.pages, limit=12
                    ),
                    "http",
                )
                return posts, detail
            except Exception as exc:
                detail["errors"].append("http:" + type(exc).__name__)
        elif self.allow_http:
            detail["status"] = "http_budget_exhausted"
        else:
            detail["http_disabled"] = True
        return [], detail


def geography(
    row, posts=None, overrides=None, scope_prediction=None, scope_support=None
):
    # No V8 topic→nonlocal policy, no stale DB region and no default RU inference.
    channel = {k: row.get(k) for k in ("username", "name", "description")}
    channel["country"] = ""
    result = resolve_geography(
        channel, overrides or {}, federal_channels=set(), territorial=None
    )
    from .region_aliases import REGION_ALIASES

    if (
        result["geography_regions"]
        and not result["geography_country"]
        and all(r in REGION_ALIASES for r in result["geography_regions"])
    ):
        result["geography_country"] = "RU"
    result["geography_evidence"] = (
        [dict(source="profile", text=profile_text(row)[:1500])]
        if result["geography_scope"] != "unknown"
        else []
    )
    macro = re.search(
        r"(?i)(?:на\s+всю|новости|события|жизнь)\s+(сибир[ьи])", profile_text(row)
    )
    if macro and result["geography_scope"] == "unknown":
        result["geography_candidates"] = ["Сибирь"]
        result["geography_explanation"] = (
            "В профиле указана макротерритория Сибирь. Это не отдельный субъект; уточните охват."
        )
        result["geography_evidence"] = [dict(source="profile", text=macro.group(0))]
    if scope_prediction:
        result["geography_scope_candidates"] = scope_prediction[:3]
        top = scope_prediction[0]
        margin = top["score"] - scope_prediction[1]["score"]
        if (
            result["geography_scope"] == "unknown"
            and not macro
            and top["id"] == "nonlocal"
            and top["score"] >= 0.60
            and margin >= 0.15
            and (scope_support or {}).get("nonlocal", 0) >= 3
        ):
            result.update(
                geography_scope="nonlocal",
                geography_regions=[],
                geography_country="",
                geography_source="semantic-v9",
                geography_relevance="nonterritorial",
                geography_review_required=True,
                geography_explanation="Предложение пилотной модели охвата; требуется проверка.",
            )
    if result["geography_scope"] != "unknown" or not posts:
        return result
    counts = Counter()
    evidence = {}
    for p in posts:
        g = resolve_geography(
            dict(name="", description=p["text"], country=""),
            {},
            federal_channels=set(),
            territorial=None,
        )
        if g["geography_scope"] == "region":
            region = tuple(g["geography_regions"])
            counts[region] += 1
            evidence.setdefault(region, []).append(
                dict(
                    source="post",
                    message_id=p.get("message_id"),
                    date=p.get("date"),
                    url=p.get("url"),
                    text=p["text"][:1000],
                )
            )
    if counts:
        region, n = counts.most_common(1)[0]
        if n >= 3 and n / len(posts) >= 0.6 and len(counts) == 1:
            # Mentions remain a reviewable proposal, never silently become scope.
            result["geography_candidates"] = list(region)
            result["geography_evidence"] = evidence[region]
    return result


def predict_channel(row, model, encoder, provider=None, rules=None):
    original = dict(row)
    rules = rules or load_classification_rules()
    row = cleaned_profile(row)
    v = combine(encoder, row)
    profile_prediction = head_predict(model["heads"]["topic"], [v])[0]
    routing = needs_posts(row, profile_prediction)
    if (
        "owner_requested_posts"
        in model.get("post_routing", {})
        .get(row["username"].lower().lstrip("@"), {})
        .get("reasons", [])
        and "owner_requested_posts" not in routing
    ):
        routing.append("owner_requested_posts")
    posts = []
    post_info = dict(
        requested=bool(routing), status="not_needed" if not routing else "unavailable"
    )
    if routing and provider:
        posts, post_info = provider(row)
    prediction = head_predict(model["heads"]["topic"], [combine(encoder, row, posts)])[
        0
    ]
    group = (
        head_predict(model["heads"]["group"], [combine(encoder, row, posts)])[0]
        if model["heads"].get("group")
        else []
    )
    top = prediction[0]
    margin = top["score"] - prediction[1]["score"]
    topic = top["id"]
    username = row["username"].lower().lstrip("@")
    manual = rules["overrides"].get(username)
    if manual is not None:
        topic = canonical(manual)
    # Every pilot output is a proposal. No threshold can certify 50-example training.
    reasons = [] if manual else ["pilot_model_requires_review"]
    if routing and not posts:
        reasons.append("required_post_evidence_missing")
    if model["heads"]["topic"]["support"].get(top["id"], 0) < 3:
        reasons.append("rare_training_class")
    if top["score"] < 0.60 or margin < 0.15:
        reasons.append("uncertain_model")
    post_predictions = (
        head_predict(
            model["heads"]["topic"], encoder.encode([p["text"] for p in posts])
        )
        if posts
        else []
    )
    votes = Counter(p[0]["id"] for p in post_predictions)
    if posts and votes.most_common(1)[0][1] / len(posts) < 0.6:
        reasons.append("mixed_post_topics")
    group_id = (
        rules.get("group_overrides", {}).get(username, TOPICS[topic]["group"])
        if manual is not None
        else TOPICS[topic]["group"]
    )
    if (
        manual is None
        and group
        and (
            top["score"] < 0.60
            or model["heads"]["topic"]["support"].get(top["id"], 0) < 3
        )
    ):
        if group[0]["score"] >= 0.60 and group[0]["score"] - group[1]["score"] >= 0.15:
            topic, group_id = "other", group[0]["id"]
    tags = [
        p["id"]
        for p in prediction[1:4]
        if votes.get(p["id"], 0) >= 3
        and p["id"] not in TOPICS[topic].get("refines", [])
    ]
    if username in rules["tag_overrides"]:
        tags = [t for t in rules["tag_overrides"][username] if t != topic]
    scope_head = model["heads"].get("scope")
    scope_prediction = (
        head_predict(scope_head, [combine(encoder, row, posts)])[0]
        if scope_head
        else None
    )
    g = geography(
        row,
        posts,
        rules["geography_overrides"],
        scope_prediction,
        scope_head["support"] if scope_head else None,
    )
    return dict(
        classification_engine="semantic-v9",
        classification_version=9,
        classification_model=dict(
            encoder=model["encoder"],
            revision=model["revision"],
            artifact_sha256=model.get("artifact_sha256"),
            training_fingerprint=model.get("training_fingerprint"),
        ),
        classification_profile=dict(
            name=original.get("name") or "",
            description=informative_description(original.get("description")),
        ),
        classification_input_hash=profile_hash(original),
        subcategory=topic,
        primary_category=topic,
        topic_group=group_id,
        topic_group_label=GROUPS[group_id]["label"],
        topic_category=TOPICS[topic]["section"],
        topic_path=[group_id, topic] if topic != "other" else [group_id],
        topic_granularity="topic"
        if topic != "other"
        else "group"
        if group_id != "other_group"
        else "unknown",
        topic_source="manual" if manual is not None else "semantic-v9",
        topic_score=None if manual is not None else top["score"],
        topic_candidates=prediction[:5],
        topic_group_candidates=group[:3],
        topic_margin=margin,
        topic_tags=[topic] + tags,
        secondary_topics=tags,
        topic_review_required=bool(reasons),
        classification_review_reasons=reasons,
        topic_evidence=[dict(source="profile", text=profile_text(row)[:2000])]
        + [
            dict(
                source="post",
                message_id=p.get("message_id"),
                date=p.get("date"),
                url=p.get("url"),
                text=p["text"][:1000],
            )
            for p in posts
        ],
        post_routing=dict(reasons=routing, **post_info),
        post_topic_votes=dict(votes),
        **g,
    )


def load_results(path=RESULTS):
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "atlas-semantic-results-v9":
        raise ValueError("Некорректный файл semantic-v9")
    return data.get("channels", {})


def merge_result(row, results):
    r = results.get(str(row.get("username") or "").lower().lstrip("@"))
    if not r:
        return row
    post_info = r.get("post_routing", {})
    if post_info.get("status") == "usable" and post_info.get("fetched_at"):
        try:
            checked = datetime.fromisoformat(post_info["fetched_at"])
            fresh = (
                checked.tzinfo is not None
                and 0 <= (datetime.now(timezone.utc) - checked).total_seconds() <= 86400
            )
        except (ValueError, TypeError):
            fresh = False
        if not fresh:
            return {**row, "semantic_result_stale": True}
    # User profile remains the canonical freshness guard even if features were cleaned.
    snapshot = r.get("classification_profile", {})
    if snapshot != {
        "name": row.get("name") or "",
        "description": informative_description(row.get("description")),
    }:
        return {**row, "semantic_result_stale": True}
    if r.get("subcategory") not in TOPICS or r.get("topic_group") not in GROUPS:
        raise ValueError("Неизвестная тема semantic-v9")
    if (
        r["subcategory"] != "other"
        and TOPICS[r["subcategory"]]["group"] != r["topic_group"]
    ):
        raise ValueError("Группа semantic-v9 не соответствует теме")
    merged = {**row, **r}
    if r.get("topic_review_required") and row.get("topic_source") != "manual":
        merged["semantic_proposal"] = {
            k: r.get(k)
            for k in ("subcategory", "topic_group", "topic_score", "topic_margin")
        }
        for key in (
            "subcategory",
            "primary_category",
            "topic_group",
            "topic_group_label",
            "topic_category",
            "topic_path",
            "topic_granularity",
            "topic_tags",
            "secondary_topics",
            "broader_topics",
        ):
            if key in row:
                merged[key] = row[key]
        merged["topic_source"] = "legacy_until_review"
    if r.get("geography_review_required") and row.get("geography_source") != "manual":
        merged["semantic_geography_proposal"] = {
            k: r.get(k)
            for k in ("geography_scope", "geography_regions", "geography_country")
        }
        for key in (
            "geography_scope",
            "geography_regions",
            "geography_country",
            "geography_relevance",
        ):
            if key in row:
                merged[key] = row[key]
    if row.get("topic_source") == "manual":
        for key, value in row.items():
            if (
                key.startswith(("topic_", "secondary_", "primary_", "broader_"))
                or key == "subcategory"
            ):
                merged[key] = value
        merged["geography_source"] = "legacy_until_review"
        merged["geography_explanation"] = (
            "Текущий охват сохранён; предложение V9 требует проверки."
        )
    if row.get("geography_source") == "manual":
        merged.update({k: v for k, v in row.items() if k.startswith("geography_")})
    return merged


def publish_results(rows, path=RESULTS):
    current = load_results(path)
    current.update(rows)
    atomic_write_text(
        path,
        json.dumps(
            dict(
                format="atlas-semantic-results-v9",
                updated_at=datetime.now(timezone.utc).isoformat(),
                channels=current,
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )
