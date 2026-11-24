"""11v2: separate profile/post decisions, evidence gates and coverage intent.

Offline research engine. Does not publish, change V11, train on current model
labels or treat similarity/heuristic margins as confidence probabilities.
"""

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import json
import re
import time

from .catalog_collection import suitable_posts
from .classification_runtime import (
    ROOT,
    fingerprint,
    manual_overrides,
    policy,
    profile,
    taxonomy,
)
from .classification_text import informative_description
from .geography_v11 import empty_geography, manual_geography, mentions
from .topic_keywords import TOPICS
from .region_classifier import normalize_text


def settings():
    return json.loads(
        (ROOT / "config/classification_v11v2.json").read_text(encoding="utf-8")
    )


def clean_profile(row):
    description = informative_description(row.get("description"))
    lines = [
        line
        for line in description.splitlines()
        if not re.search(
            r"(?i)^(?:реестр|страница в реестре|сотрудничество|связь|автор|чат|max|rutube|сайт|реклама через|для агентов|присоединяйтесь к команде)\b",
            line.strip(),
        )
        and not re.search(
            r"(?i)не\s+(?:(?:явля\w+|представля\w+)\s+)?(?:индивидуальн\w*\s+)?инвестиционн\w*\s+рекомендац",
            line,
        )
        and not re.fullmatch(r"\s*(?:\d{1,2}\+|#\w+)\s*", line)
        and not re.search(
            r"(?i)ркн.{0,45}(?:реестр|перечень)|(?:реестр|перечень).{0,30}ркн", line
        )
    ]
    return dict(name=row.get("name") or "", description="\n".join(lines))


def normalize(text):
    return " ".join(re.findall(r"[\w]+", text.casefold().replace("ё", "е")))


def select_posts(sample, config):
    """Filter model input only; never rewrite the collector's cached posts."""
    accepted, excluded, wordsets = [], [], []
    for post in suitable_posts(sample, config["posts"]):
        text = informative_description(post["text"])
        words = normalize(text).split()
        reason = None
        if len(words) < 70 and re.search(
            r"(?i)(присылайте|прислать|скидывайте|предложить).{0,60}(новост|фото|видео|бот)|для срочных вопросов|подписывайтесь на наш",
            text[:250],
        ):
            reason = "service_message"
        elif len(words) < config["minimum_post_words"] or len(set(words)) < 9:
            reason = "low_information"
        tokens = set(words)
        if not reason and any(
            len(tokens & old) / max(len(tokens | old), 1)
            >= config["near_duplicate_jaccard"]
            for old in wordsets
        ):
            reason = "near_duplicate"
        if reason:
            excluded.append(dict(message_id=post.get("message_id"), reason=reason))
            continue
        wordsets.append(tokens)
        accepted.append(dict(post, text=text[: config["post_characters"]]))
    return accepted, excluded


@lru_cache(maxsize=1)
def geography_policy():
    return json.loads(
        (ROOT / "config/geography_v11v2.json").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def extra_place_patterns():
    return [
        (
            place,
            re.compile(
                r"(?<!\w)(?:"
                + "|".join(
                    re.escape(normalize_text(alias)) for alias in place["aliases"]
                )
                + r")(?!\w)"
            ),
        )
        for place in geography_policy()["places"]
    ]


def extra_places(text):
    normalized = normalize_text(text)
    return [
        place for place, pattern in extra_place_patterns() if pattern.search(normalized)
    ]


def safe_mentions(text):
    # Names of people and ambiguous everyday words are not place evidence.
    cleaned = re.sub(r"(?i)владимир(?:а|у|ом|е)?\s+путин\w*", "", text)
    for pattern in geography_policy()["ignored_word_patterns"]:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I)
    found = set(mentions(cleaned))
    found.update("region:" + place["region"] for place in extra_places(cleaned))
    if "country:TG" in found and not (
        re.search(r"(?i)(?:республик\w*|стран\w*)\s+того\b", cleaned)
        or re.search(r"(?:\bв|\bиз|\bпо)\s+Того\b|\bTogo\b", cleaned)
    ):
        found.remove("country:TG")
    if "country:WS" in found and not re.search(r"(?i)\b(?:самоа|samoa)\b", cleaned):
        found.remove("country:WS")
    return found


def geography(row, posts, topic, identity=False, override=None):
    if override is not None:
        return manual_geography(override)
    from .geography_v11 import reference

    entities = {e["id"]: e for e in reference()[0]}
    p = clean_profile(row)
    # Registration, biography and contacts do not establish local purpose.
    description = "\n".join(
        line
        for line in p["description"].splitlines()
        if not re.search(
            r"(?i)родил|родом|окончил|учредител|регистрац|реклам|контакт", line
        )
    )
    text = p["name"] + ". " + description
    profile_ids = safe_mentions(text)
    post_ids = [safe_mentions(post["text"]) for post in posts]
    counts = Counter(identifier for found in post_ids for identifier in found)
    countries = {entities[key]["country"] for key in counts}
    candidates = [
        dict(
            id=key,
            label=entities[key]["label"],
            country=entities[key]["country"],
            region=entities[key]["region"],
            post_support=counts[key],
            title_match=key in safe_mentions(p["name"]),
            description_match=key in safe_mentions(description),
        )
        for key in sorted(
            profile_ids | set(counts),
            key=lambda key: (key in profile_ids, counts[key], key),
            reverse=True,
        )
    ]
    local_intent = bool(
        re.search(
            r"(?i)местн|для жителей|сообщество|жизнь в|жизнь города|городск|новост|афиша|куда пойти|бизнес.?инсайд|события|переезд|релокац|хроник",
            text,
        )
    )
    local_intent = (
        local_intent
        or bool(profile_ids)
        and bool(
            re.search(r"(?i)подслушано|типичный|жизнь района|новости района", text)
        )
    )
    geo_policy = geography_policy()
    title_ids = safe_mentions(p["name"])
    local_service = (
        topic in geo_policy["local_service_topics"]
        and bool(title_ids)
        and (
            bool(re.search(geo_policy["service_profile_pattern"], text, re.I))
            or any(
                bool(found & title_ids)
                and re.search(geo_policy["service_post_pattern"], post["text"], re.I)
                for post, found in zip(posts, post_ids)
            )
        )
    )
    local_intent = local_intent or local_service
    global_intent = bool(
        re.search(
            r"(?i)международн|мировая повестка|мировые новости|со всего мира|world news",
            text,
        )
    )
    selected = [entities[key] for key in profile_ids]
    selected_regions = sorted({e["region"] for e in selected if e["region"]})
    selected_countries = {e["country"] for e in selected}
    scope, country, regions, reason = "unknown", "", [], "coverage_not_established"
    if local_intent and not global_intent and len(selected_countries) == 1:
        regions = selected_regions
        country = next(iter(selected_countries))
        scope = (
            "multi_region" if len(regions) > 1 else "region" if regions else "country"
        )
        reason = (
            "local_service_profile_location"
            if local_service
            else "explicit_profile_territorial_purpose"
        )
    elif (
        global_intent
        or topic in ("v11_military_affairs", "v11_international_relations")
        and len(countries) >= 2
    ):
        scope, reason = "global", "international_analysis_not_country_mentions"
    elif identity and not local_intent and not profile_ids:
        scope, reason = "nonlocal", "subject_identity_without_territorial_purpose"
    result = empty_geography(reason)
    result.update(
        geography_scope=scope,
        geography_country=country,
        geography_regions=regions,
        geography_source="semantic-v11v2-proposal",
        geography_relevance="territorial"
        if scope in ("region", "country", "multi_region")
        else "nonterritorial"
        if scope == "nonlocal"
        else "international"
        if scope == "global"
        else "undetermined",
        geography_candidates=candidates[:8],
        geography_places=[
            dict(
                label=place["label"],
                kind="city",
                country=place["country"],
                region=place["region"],
            )
            for place in extra_places(text)
        ],
        geography_evidence=[dict(source="profile", text=text)],
        geography_explanation="Направленность определяется отдельно от мест событий. Частота упоминаний стран сама по себе не назначает территорию.",
        geography_review_reasons=[reason, "geography_not_independently_validated"],
    )
    return result


class TopicModel:
    """E5 ensemble + an independent lexical prototype signal, no model downloads."""

    def __init__(self, encoder, config=None):
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.config = config or settings()
        self.encoder = encoder
        self.topics = sorted(taxonomy()["topics"], key=lambda t: t["id"])
        self.by_id = {t["id"]: i for i, t in enumerate(self.topics)}
        self.signals = {
            ("v11_" + key): value for key, value in self.config["signals"].items()
        }
        documents, self.spans = [], []
        lexical = []
        for topic in self.topics:
            extra = self.signals.get(topic["id"], {})
            examples = [
                topic["label"] + ". " + topic["definition"],
                "Канал посвящён теме: " + topic["definition"],
                *extra.get("examples", []),
            ]
            self.spans.append((len(documents), len(examples)))
            documents.extend(examples)
            # Legacy keywords are vocabulary, never old predicted training labels.
            keywords = [
                word
                for old in topic.get("legacy_hints", [])
                for word in TOPICS.get(old, {}).get("keywords", [])
            ]
            lexical.append(
                " ".join(
                    [
                        topic["label"],
                        topic["definition"],
                        *extra.get("examples", []),
                        *keywords,
                    ]
                )
            )
        self.anchors = encoder.encode(documents)
        self.lexical = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            lowercase=True,
            sublinear_tf=True,
            max_features=40000,
            dtype=np.float32,
        )
        self.lexical_anchors = self.lexical.fit_transform(lexical)
        self.groups = sorted(taxonomy()["groups"], key=lambda g: g["id"])
        self.group_ids = [g["id"] for g in self.groups]
        source = json.loads((ROOT / policy()["taxonomy"]).read_text(encoding="utf-8"))
        definitions = {"v11_" + g["id"]: g["definition"] for g in source["groups"]}
        group_documents = []
        self.group_spans = []
        for group in self.groups:
            examples = [
                group["label"] + ". " + definitions[group["id"]],
                group["label"]
                + ". "
                + ". ".join(
                    t["label"] for t in self.topics if t["group"] == group["id"]
                ),
                *self.config.get("group_examples", {}).get(group["id"], []),
            ]
            self.group_spans.append((len(group_documents), len(examples)))
            group_documents.extend(examples)
        self.group_anchors = encoder.encode(group_documents)
        self.lexical_groups = self.lexical.transform(group_documents)

    def scores(self, texts, vectors):
        import numpy as np

        similarities = vectors @ self.anchors.T
        semantic = np.stack(
            [
                similarities[:, start : start + count].mean(axis=1)
                for start, count in self.spans
            ],
            axis=1,
        )
        # Per-document discrimination; high shared cosine baseline is not confidence.
        scale = np.maximum(semantic.std(axis=1, keepdims=True), 0.01)
        semantic = np.clip(
            (semantic - semantic.mean(axis=1, keepdims=True)) / scale, -3, 3
        )
        lexical = (self.lexical.transform(texts) @ self.lexical_anchors.T).toarray()
        lexical = lexical / np.maximum(lexical.max(axis=1, keepdims=True), 0.05)
        weight = self.config["lexical_weight"]
        return (1 - weight) * semantic + weight * lexical * 3

    def coarse_scores(self, texts, vectors):
        """A direct 18-way head, not max(topic score) disguised as hierarchy."""
        import numpy as np

        similarities = vectors @ self.group_anchors.T
        semantic = np.stack(
            [
                similarities[:, start : start + count].mean(axis=1)
                for start, count in self.group_spans
            ],
            axis=1,
        )
        semantic = np.clip(
            (semantic - semantic.mean(axis=1, keepdims=True))
            / np.maximum(semantic.std(axis=1, keepdims=True), 0.01),
            -3,
            3,
        )
        lexical = (self.lexical.transform(texts) @ self.lexical_groups.T).toarray()
        lexical = np.stack(
            [
                lexical[:, start : start + count].mean(axis=1)
                for start, count in self.group_spans
            ],
            axis=1,
        )
        lexical /= np.maximum(lexical.max(axis=1, keepdims=True), 0.05)
        weight = self.config["lexical_weight"]
        return (1 - weight) * semantic + weight * lexical * 3

    def decide(self, row, posts, vectors):
        import numpy as np

        p = clean_profile(row)
        profile_text = p["name"] + ". " + p["description"]
        texts = [profile_text, *[post["text"] for post in posts]]
        scores = self.scores(texts, vectors)
        cues = {}
        identities = []
        for topic, signal in self.signals.items():
            patterns = signal["cues"]
            profile_hits = sum(
                bool(re.search(pattern, profile_text, re.I)) for pattern in patterns
            )
            post_hits = [
                sum(bool(re.search(pattern, text, re.I)) for pattern in patterns)
                for text in texts[1:]
            ]
            identity = bool(
                signal.get("identity")
                and re.search(signal["identity"], profile_text, re.I)
            )
            # Naming the owner alone is not a topic: require content/profile purpose.
            if identity and (
                profile_hits >= 2
                or signal.get("identity")
                and topic == "v11_banking_payments"
                or sum(hit >= 1 for hit in post_hits) >= 2
            ):
                identities.append(topic)
            cues[topic] = dict(
                profile=profile_hits,
                post_support=sum(hit >= 1 for hit in post_hits),
                identity=identity,
            )
            column = self.by_id[topic]
            scores[0, column] += min(profile_hits, 2) * 0.45
            for index, hits in enumerate(post_hits, 1):
                scores[index, column] += min(hits, 2) * 0.25
            if signal.get("guard"):
                # A passing story about children, food or a company is not identity.
                strong_posts = sum(hit >= 2 for hit in post_hits)
                if profile_hits < 2 and strong_posts < max(2, len(posts) * 0.5):
                    scores[:, column] -= 1.4
        identity_conflicts = []
        if len(posts) >= 3:
            for topic in list(identities):
                if cues[topic]["post_support"] == 0 and any(
                    other != topic
                    and sum(
                        all(
                            re.search(pattern, post["text"], re.I)
                            for pattern in signal["cues"]
                        )
                        for post in posts
                    )
                    >= len(posts) * 0.75
                    for other, signal in self.signals.items()
                ):
                    identities.remove(topic)
                    identity_conflicts.append(topic)
        group_evidence = []
        for identity_name, signal in self.config.get("group_identities", {}).items():
            if all(
                re.search(pattern, profile_text, re.I) for pattern in signal["cues"]
            ) and (
                not posts
                or sum(
                    bool(re.search(signal["post_pattern"], post["text"], re.I))
                    for post in posts
                )
                >= min(2, len(posts))
            ):
                group_evidence.append(dict(id=identity_name, **signal))
        weight = (
            self.config["identity_profile_weight"]
            if identities or group_evidence
            else self.config["profile_weight"]
        )
        if len(posts) < 3:
            weight = max(weight, 0.75)
        content = scores[1:].mean(axis=0) if posts else scores[0]
        final = weight * scores[0] + (1 - weight) * content
        for topic in identities:
            final[self.by_id[topic]] += 0.55
        if any(item["id"] == "corporate_finance" for item in group_evidence):
            # Company cash flows are not a household budget or securities tips.
            for topic in ("v11_personal_money", "v11_investments"):
                final[self.by_id[topic]] -= 1.0
        # Explicit local community + heterogeneous subjects is local news, not
        # a narrow subject inferred from a child/recipe mentioned in one story.
        local = bool(safe_mentions(p["name"])) and bool(
            re.search(
                r"(?i)сообщество|присылайте новости|местные новости|городские новости|новости города",
                profile_text,
            )
        )
        if local and not identities:
            winners = (
                {self.topics[int(i)]["group"] for i in np.argmax(scores[1:], axis=1)}
                if posts
                else set()
            )
            if not posts or len(winners) >= 3:
                final[self.by_id["v11_general_news"]] += 1.5
                final[self.by_id["v11_urban_life"]] += 0.6
        order = np.argsort(-final, kind="stable")
        winner, runner = int(order[0]), int(order[1])
        margin = float(final[winner] - final[runner])
        selected = self.topics[winner]
        reasons = ["not_independently_calibrated"]
        if identity_conflicts:
            reasons.append("profile_identity_not_supported_by_posts")
        supported = (
            selected["id"] in identities
            or selected["id"] in cues
            and (
                cues[selected["id"]]["profile"] >= 2
                or cues[selected["id"]]["post_support"] >= max(2, len(posts) * 0.5)
            )
            or local
            and selected["id"] == "v11_general_news"
        )
        profile_group = self.topics[int(np.argmax(scores[0]))]["group"]
        content_group = self.topics[int(np.argmax(content))]["group"]
        if profile_group != content_group:
            reasons.append("profile_post_disagreement")
        if len(posts) < 3:
            reasons.append("insufficient_informative_posts")
        narrow = margin >= self.config["minimum_topic_margin"] and (
            supported or profile_group == content_group
        )
        coarse = self.coarse_scores(texts, vectors)
        coarse_final = weight * coarse[0] + (1 - weight) * (
            coarse[1:].mean(axis=0) if posts else coarse[0]
        )
        group_scores = {}
        coarse_weight = self.config.get("coarse_group_weight", 0.65)
        for index, group in enumerate(self.groups):
            alternatives = sorted(
                (
                    float(final[i])
                    for i, t in enumerate(self.topics)
                    if t["group"] == group["id"]
                ),
                reverse=True,
            )
            group_scores[group["id"]] = coarse_weight * float(coarse_final[index]) + (
                1 - coarse_weight
            ) * float(np.mean(alternatives[:2]))
        ranked_groups = sorted(group_scores, key=lambda g: (-group_scores[g], g))
        group_margin = group_scores[ranked_groups[0]] - group_scores[ranked_groups[1]]
        fine_groups = {
            g["id"]: max(
                float(final[i])
                for i, t in enumerate(self.topics)
                if t["group"] == g["id"]
            )
            for g in self.groups
        }
        fine_group_order = sorted(fine_groups, key=lambda g: (-fine_groups[g], g))
        concordant_group = (
            ranked_groups[0] == fine_group_order[0]
            and ranked_groups[0] in (profile_group, content_group)
            and fine_groups[fine_group_order[0]] - fine_groups[fine_group_order[1]]
            >= self.config["minimum_group_margin"]
        )
        coarse_has_evidence = (
            bool(posts or identities or group_evidence)
            or len(normalize(profile_text).split()) >= 4
        )
        status = (
            "topic"
            if narrow
            else "group"
            if coarse_has_evidence
            and (
                group_margin >= self.config["minimum_group_margin"] or concordant_group
            )
            else "abstain"
        )
        assigned_group = (
            selected["group"]
            if status == "topic"
            else ranked_groups[0]
            if status == "group"
            else None
        )
        for signal in group_evidence:
            votes = (
                sum(
                    self.topics[int(i)]["id"] == selected["id"]
                    for i in np.argmax(scores[1:], axis=1)
                )
                if posts
                else 0
            )
            if selected["id"] not in signal["compatible_topics"] or votes < max(
                2, len(posts) * signal["minimum_topic_support_fraction"]
            ):
                status = "group"
                assigned_group = signal["group"]
                reasons.append("supported_professional_group_keep_group")
        group_identity = self.signals.get(selected["id"], {}).get("group_only_identity")
        if group_identity and re.search(group_identity, profile_text, re.I):
            # Business reporting is not automatically advice on entrepreneurship.
            status = "group"
            assigned_group = selected["group"]
            reasons.append("broad_editorial_identity_keep_group")
        if status != "topic":
            reasons.append("ambiguous_topic_keep_broad_or_abstain")
        return dict(
            subcategory=selected["id"] if status == "topic" else None,
            suggested_topic=selected["id"],
            topic_group=assigned_group,
            topic_granularity=status,
            topic_margin=margin,
            topic_score=float(final[winner]),
            topic_score_kind="hybrid_ranking_score_not_probability",
            topic_candidates=[
                dict(id=self.topics[int(i)]["id"], score=float(final[i]))
                for i in order[:5]
            ],
            topic_review_required=True,
            topic_assignment_status="proposal_" + status,
            classification_review_reasons=reasons,
            topic_diagnostics=dict(
                profile_weight=weight,
                identity_topics=identities,
                identity_conflicts=identity_conflicts,
                group_identity_evidence=[g["id"] for g in group_evidence],
                group_candidates=[
                    dict(id=g, score=group_scores[g]) for g in ranked_groups[:4]
                ],
                group_margin=group_margin,
                group_topic_head_agreement=concordant_group,
                profile_group=self.group_ids[int(np.argmax(coarse[0]))],
                post_group=self.group_ids[int(np.argmax(coarse[1:].mean(axis=0)))]
                if posts
                else None,
                cues=cues,
                profile_candidates=[
                    self.topics[int(i)]["id"] for i in np.argsort(-scores[0])[:3]
                ],
                post_winners=[
                    self.topics[int(i)]["id"] for i in np.argmax(scores[1:], axis=1)
                ]
                if posts
                else [],
            ),
        )


def classify(rows, evidence, encoder, *, seconds=3600, config=None):
    if len(rows) != len(evidence):
        raise ValueError("Состав снимка и постов не совпадает")
    if len({r["username"] for r in rows}) != len(rows):
        raise ValueError("Повторный username")
    config = config or settings()
    started = time.perf_counter()
    model = TopicModel(encoder, config)
    overrides = manual_overrides()
    output = {}
    for row, sample in zip(rows, evidence):
        if time.perf_counter() - started >= seconds:
            break
        posts, excluded = select_posts(sample, config) if sample else ([], [])
        p = clean_profile(row)
        vectors = encoder.encode(
            [p["name"] + ". " + p["description"], *[post["text"] for post in posts]]
        )
        decision = model.decide(row, posts, vectors)
        manual = overrides.get(row["username"], {})
        manual_topic = manual.get("topic") or (
            row.get("subcategory")
            if row.get("topic_source") in ("manual", "local")
            else None
        )
        if manual_topic:
            known = {t["id"]: t["group"] for t in model.topics}
            if manual_topic not in known and manual_topic not in TOPICS:
                raise ValueError("Неизвестная ручная тема: " + manual_topic)
            decision.update(
                subcategory=manual_topic,
                suggested_topic=manual_topic,
                topic_group=known.get(manual_topic) or TOPICS[manual_topic]["group"],
                topic_granularity="topic",
                topic_assignment_status="manual_preserved",
                topic_score=None,
                topic_review_required=False,
                classification_review_reasons=[],
            )
        geo_override = manual.get("geography")
        if geo_override is None and row.get("geography_source") == "manual":
            geo_override = dict(
                scope=row["geography_scope"],
                country=row.get("geography_country", ""),
                regions=row.get("geography_regions", []),
            )
        geo = geography(
            row,
            posts,
            decision["suggested_topic"],
            bool(decision["topic_diagnostics"]["identity_topics"]),
            geo_override,
        )
        output[row["username"]] = dict(
            decision,
            classification_engine="semantic-v11v2",
            classification_version="11v2",
            classification_manual_dimensions=list(manual),
            classification_profile=profile(row),
            classification_input_profile=p,
            classification_created_at=datetime.now(timezone.utc).isoformat(),
            topic_source="manual" if manual_topic else "semantic-v11v2-proposal",
            post_routing=dict(
                usable=len(posts),
                excluded=excluded,
                fetched_at=sample.get("fetched_at") if sample else None,
            ),
            topic_evidence=[
                dict(source="profile", text=p["name"] + ". " + p["description"]),
                *[dict(post, source="post") for post in posts],
            ],
            **geo,
        )
        if len(output) % 25 == 0:
            print(f"11v2 classified {len(output)}/{len(rows)}", flush=True)
    return dict(
        format="atlas-results-v11v2",
        classification_version="11v2",
        implementation_sha256=fingerprint(
            {
                name: (ROOT / "src" / name).read_text(encoding="utf-8")
                for name in (
                    "classification_v11v2.py",
                    "classification_text.py",
                    "geography_v11.py",
                )
            }
        ),
        vocabulary_sha256=fingerprint(TOPICS),
        geography_policy_sha256=fingerprint(geography_policy()),
        taxonomy_sha256=taxonomy()["fingerprint"],
        policy_sha256=fingerprint(config),
        input_sha256=fingerprint(rows),
        created_at=datetime.now(timezone.utc).isoformat(),
        model=dict(
            encoder=encoder.settings,
            scoring="multi_prototype_e5_lexical_tfidf_evidence_gates",
            group_scoring="direct_group_prototypes_and_topic_aggregation",
            supervised_training=False,
            calibrated=False,
        ),
        channels=output,
        remaining=len(rows) - len(output),
        benchmark=dict(
            seconds=time.perf_counter() - started, encoder=dict(encoder.stats)
        ),
    )


def evaluate(result, labels):
    if labels.get("split") not in ("development", "holdout"):
        raise ValueError("Укажите development или holdout явно")
    if labels["split"] == "holdout":
        development = json.loads(
            (ROOT / "config/classification_v11v2_development.json").read_text(
                encoding="utf-8"
            )
        )
        if {c["username"] for c in labels["cases"]} & {
            c["username"] for c in development["cases"]
        }:
            raise ValueError("Примеры разработки нельзя включать в независимый holdout")
    records = []
    for case in labels["cases"]:
        row = result["channels"].get(case["username"])
        if row is None:
            continue
        topic_ok = row.get("subcategory") in case.get("topics", [])
        group_ok = row.get("topic_group") in case.get("groups", [])
        geo_fields = [key for key in ("scope", "country", "regions") if key in case]
        geo_ok = (
            all(
                row.get("geography_" + key) == case[key]
                for key in ("scope", "country", "regions")
                if key in case
            )
            if geo_fields
            else None
        )
        decision_ok = (
            topic_ok
            or case.get("allow_group_only", False)
            and row.get("topic_granularity") == "group"
            and group_ok
        )
        records.append(
            dict(
                username=case["username"],
                topic=topic_ok,
                group=group_ok,
                geography=geo_ok,
                decision_acceptable=decision_ok,
                granularity=row["topic_granularity"],
            )
        )
    return dict(
        split=labels["split"],
        evaluated=len(records),
        expected=len(labels["cases"]),
        missing=[
            c["username"]
            for c in labels["cases"]
            if c["username"] not in result["channels"]
        ],
        topic_correct=sum(r["topic"] for r in records),
        acceptable_decisions=sum(r["decision_acceptable"] for r in records),
        group_correct=sum(r["group"] for r in records),
        geography_evaluated=sum(r["geography"] is not None for r in records),
        geography_correct=sum(r["geography"] is True for r in records),
        records=records,
        warning="Development-примеры использованы при разработке и не измеряют независимую точность."
        if labels["split"] == "development"
        else "Отложенность меток должна обеспечиваться процедурой разметки, а не названием файла.",
    )


def render_view(rows, result):
    # Reuse a safe escaped, offline viewer, but never pass the new result through
    # the V11 publication/validation path. Group/abstain are explicit view labels.
    from .classification_runtime import policy

    tx = taxonomy()
    topics = [
        dict(id=k, label=v["label"] + " · прежняя сетка") for k, v in TOPICS.items()
    ]
    topics += [dict(id=t["id"], label=t["label"]) for t in tx["topics"]]
    topics += [
        dict(id="group:" + g["id"], label=g["label"] + " · только раздел")
        for g in tx["groups"]
    ]
    topics.append(dict(id="abstain", label="Недостаточно оснований для выбора темы"))
    payload_rows = []
    for row in rows:
        prediction = result["channels"].get(row["username"])
        if prediction is None:
            continue
        predicted = prediction["subcategory"] or (
            "group:" + prediction["topic_group"]
            if prediction["topic_group"]
            else "abstain"
        )
        payload_rows.append(
            dict(
                username=row["username"],
                name=row.get("name") or row["username"],
                description=row.get("description") or "",
                current=row.get("subcategory") or "other",
                current_source=row.get("topic_source") or "unknown",
                predicted=predicted,
                candidates=prediction["topic_candidates"],
                reasons=prediction["classification_review_reasons"],
                review_required=prediction["topic_review_required"],
                evidence=prediction["topic_evidence"],
                routing=prediction["post_routing"],
                diagnostics=prediction["topic_diagnostics"],
                geography_current={
                    k: v for k, v in row.items() if k.startswith("geography_")
                },
                geography_proposal={
                    k: v for k, v in prediction.items() if k.startswith("geography_")
                },
            )
        )
    countries = json.loads(
        (ROOT / "config/countries_v11.json").read_text(encoding="utf-8")
    )
    payload = dict(
        format="atlas-v11v2-view-v1",
        engine_label="11v2",
        fingerprint=fingerprint(result),
        model_sha256=fingerprint(policy()["encoder"]),
        rows=payload_rows,
        snapshot_channels=len(rows),
        topics=topics,
        country_labels={c["country"]: c["label"] for c in countries["countries"]},
    )
    data = (
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (ROOT / "web/classification-v10-template.html").read_text(
        encoding="utf-8"
    )
    script = (
        (ROOT / "web/classification-v10-view.js")
        .read_text(encoding="utf-8")
        .replace("</script", "<\\/script")
    )
    return template.replace("/*__DATA__*/", data).replace("/*__SCRIPT__*/", script)
