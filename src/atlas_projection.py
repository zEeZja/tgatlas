"""Frozen, local UMAP/t-SNE comparison with conditional regional distances.

No Telegram, database writes, training labels or model download. Layout quality
metrics describe neighbourhoods, NOT the accuracy of topic/geography predictions.
"""

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from .classification_runtime import profile, taxonomy
from .catalog_collection import suitable_posts
from .file_io import atomic_write_text
from .layout_policy import geography_role, load_policy, policy_fingerprint, row_binding
from .topic_keywords import TOPICS, GROUPS

ROOT = Path(__file__).resolve().parent.parent


def snapshot_rows(path, limit=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("channels") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not 3 <= len(rows) <= 6000:
        raise ValueError("Для сравнения нужен снимок от 3 до 6000 каналов")
    rows = sorted(rows, key=lambda r: str(r.get("id") or r["username"]))
    if len({str(r.get("id") or r["username"]) for r in rows}) != len(rows) or len(
        {r["username"] for r in rows}
    ) != len(rows):
        raise ValueError("Повторные идентификаторы в снимке")
    if limit is not None:
        if not 3 <= limit <= len(rows):
            raise ValueError("Недопустимый размер пилота")
        rows = rows[:limit]
    return rows


def freeze_evidence(rows, cache_dir, output):
    evidence = []
    for row in rows:
        username = row["username"]
        if (
            not isinstance(username, str)
            or not username.replace("_", "").isalnum()
            or "/" in username
            or "\\" in username
        ):
            raise ValueError("Некорректный username снимка")
        sample, posts = {}, []
        file = Path(cache_dir) / (username + ".json")
        if file.exists():
            try:
                sample = json.loads(file.read_text(encoding="utf-8"))
                if (
                    sample.get("username") == username
                    and sample.get("purpose") == "classification_only"
                ):
                    if row.get("classification_engine") == "semantic-v11v2":
                        from .classification_v11v2 import select_posts, settings

                        posts, _ = select_posts(sample, settings())
                    else:
                        posts = suitable_posts(sample, 8)
            except (ValueError, TypeError, KeyError):
                sample = {}
        if len(posts) < 3:
            posts = []
        evidence.append(
            dict(
                username=username,
                fetched_at=sample.get("fetched_at"),
                posts=[
                    dict(
                        text=p["text"][:1000],
                        date=p["date"],
                        message_id=p.get("message_id"),
                        url=p.get("url"),
                    )
                    for p in posts
                ],
            )
        )
    atomic_write_text(
        Path(output) / "evidence.json",
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        Path(output) / "snapshot.json",
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
    )
    return evidence


def embeddings(rows, evidence, encoder):
    import numpy as np

    combined = []
    for start in range(0, len(rows), 64):
        documents, spans = [], []
        for row, sample in zip(rows[start : start + 64], evidence[start : start + 64]):
            if row.get("classification_engine") == "semantic-v11v2":
                from .classification_v11v2 import clean_profile

                p = clean_profile(row)
            else:
                p = profile(row)
            spans.append((len(documents), len(sample["posts"])))
            documents.append(p["name"] + ". " + p["description"])
            documents.extend(post["text"] for post in sample["posts"])
        vectors = encoder.encode(documents)
        for offset, count in spans:
            vector = vectors[offset]
            if count:
                content = vectors[offset + 1 : offset + 1 + count].mean(axis=0)
                content = content / max(float(np.linalg.norm(content)), 1e-12)
                vector = 0.25 * vector + 0.75 * content
                vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
            combined.append(vector)
        print(f"embeddings {min(start + 64, len(rows))}/{len(rows)}", flush=True)
    return np.asarray(combined, dtype=np.float32)


def distance_matrices(vectors, roles, policy):
    import numpy as np

    # Unit-vector Euclidean distance is monotonic in cosine distance. Both
    # reducers receive exactly the same symmetric, conditioned dissimilarities.
    raw = vectors @ vectors.T
    raw *= -2
    raw += 2
    np.maximum(raw, 0, out=raw)
    np.sqrt(raw, out=raw)
    np.fill_diagonal(raw, 0)
    distances = raw.copy()
    regional = [i for i, r in enumerate(roles) if r["kind"] == "region"]
    for i in regional:
        for j in regional:
            if j <= i:
                continue
            strength = roles[i]["weight"] * roles[j]["weight"]
            if set(roles[i]["regions"]) & set(roles[j]["regions"]):
                value = raw[i, j] * (
                    1 + (policy["same_region_distance_scale"] - 1) * strength
                )
            else:
                value = (
                    raw[i, j]
                    * (1 + (policy["different_region_distance_scale"] - 1) * strength)
                    + policy["different_region_distance_offset"] * strength
                )
            distances[i, j] = distances[j, i] = value
    return raw, distances


def reduce(distances, method, policy):
    from threadpoolctl import threadpool_limits

    n = len(distances)
    with threadpool_limits(limits=4):
        if method == "umap":
            import umap

            opts = policy["umap"]
            model = umap.UMAP(
                n_components=2,
                metric="precomputed",
                init="random",
                random_state=policy["seed"],
                n_neighbors=min(opts["n_neighbors"], n - 1),
                min_dist=opts["min_dist"],
                n_epochs=opts["n_epochs"],
                n_jobs=1,
                low_memory=True,
            )
        elif method == "tsne":
            from sklearn.manifold import TSNE

            opts = policy["tsne"]
            model = TSNE(
                n_components=2,
                metric="precomputed",
                init="random",
                random_state=policy["seed"],
                perplexity=min(opts["perplexity"], max(1, (n - 1) / 3)),
                max_iter=opts["max_iter"],
                learning_rate="auto",
                n_jobs=4,
            )
        else:
            raise ValueError("Неизвестный метод проекции")
        return model.fit_transform(distances)


def separate_points(points, policy):
    import numpy as np

    points = np.asarray(points, dtype=np.float64).copy()
    if points.shape[1:] != (2,) or not np.isfinite(points).all():
        raise ValueError("Проекция вернула некорректные координаты")
    points -= points.mean(axis=0)
    extent = max(float(np.percentile(np.linalg.norm(points, axis=1), 95)), 1e-6)
    points *= math.sqrt(len(points)) * 23 / extent
    original = points.copy()
    minimum = policy["geometry"]["minimum_separation"]
    for iteration in range(policy["geometry"]["collision_passes"]):
        if iteration < 10:
            points += (original - points) * 0.01
        grid = {}
        moved = 0
        for i in range(len(points)):
            x, y = points[i]
            cell = (math.floor(x / minimum), math.floor(y / minimum))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((cell[0] + dx, cell[1] + dy), []):
                        delta = points[i] - points[j]
                        distance = float(np.linalg.norm(delta))
                        if distance >= minimum:
                            continue
                        if distance < 1e-9:
                            angle = (i * 2.399963229728653 + j * 0.61803398875) % (
                                2 * math.pi
                            )
                            delta = np.array([math.cos(angle), math.sin(angle)]) * 1e-3
                            distance = 1e-3
                        push = delta / distance * (minimum - distance) * 0.52
                        # Earlier nodes stay in their indexed cells in this pass.
                        points[i] += push * 2
                        moved += 1
            current = tuple(np.floor(points[i] / minimum).astype(int))
            grid.setdefault(current, []).append(i)
        if not moved:
            break
    # Resolve residual collisions against the CURRENT spatial cell of every
    # accepted point. A force pass alone can move a point into an unchecked cell.
    # Search locally around its projected position, never along a global row.
    corrected = 0
    grid = {}
    for i, p in enumerate(points):
        origin = p.copy()

        def available(candidate):
            cell = tuple(np.floor(candidate / minimum).astype(int))
            return all(
                float(np.sum((candidate - points[j]) ** 2)) >= minimum**2
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for j in grid.get((cell[0] + dx, cell[1] + dy), [])
            )

        if not available(p):
            corrected += 1
            found = False
            for ring in range(1, len(points) + 2):
                radius = ring * minimum * 0.5
                samples = max(12, math.ceil(2 * math.pi * radius / (minimum * 0.7)))
                phase = i * 2.399963229728653
                for sample in range(samples):
                    angle = phase + sample * 2 * math.pi / samples
                    candidate = origin + radius * np.array(
                        [math.cos(angle), math.sin(angle)]
                    )
                    if available(candidate):
                        points[i] = candidate
                        found = True
                        break
                if found:
                    break
            if not found:
                raise ValueError("Не удалось устранить пересечения точек")
        cell = tuple(np.floor(points[i] / minimum).astype(int))
        grid.setdefault(cell, []).append(i)
    displacement = np.linalg.norm(points - original, axis=1)
    points -= points.min(axis=0)
    points += 120
    return points, dict(
        collision_passes=iteration + 1,
        residual_corrections=corrected,
        displacement_p95=float(np.percentile(displacement, 95)),
    )


def nearest(matrix, k):
    import numpy as np

    result = np.empty((len(matrix), k), dtype=np.int32)
    for start in range(0, len(matrix), 128):
        batch = matrix[start : start + 128].copy()
        for row in range(len(batch)):
            batch[row, start + row] = np.inf
        result[start : start + len(batch)] = np.argsort(batch, axis=1, kind="stable")[
            :, :k
        ]
    return result


def nearest_points(points, k, block_size=128):
    """2D neighbours in bounded batches, without N×N×2 temporaries."""
    import numpy as np

    points = np.asarray(points, dtype=np.float64)
    result = np.empty((len(points), k), dtype=np.int32)
    norms = np.einsum("ij,ij->i", points, points)
    for start in range(0, len(points), block_size):
        block = points[start : start + block_size]
        distances = (
            norms[start : start + len(block), None]
            + norms[None, :]
            - 2 * (block @ points.T)
        )
        np.maximum(distances, 0, out=distances)
        for i in range(len(block)):
            distances[i, start + i] = np.inf
        result[start : start + len(block)] = np.argsort(
            distances, axis=1, kind="stable"
        )[:, :k]
    return result


def metrics(raw, conditioned, points, rows, roles):
    import numpy as np

    k = min(15, len(rows) - 1)
    current = nearest_points(points, k)
    original = nearest(raw, k)
    target = nearest(conditioned, k)

    def retention(reference):
        return float(
            np.mean([len(set(a) & set(b)) / k for a, b in zip(reference, current)])
        )

    def geo_agreement(indices):
        values = []
        for i in indices:
            neighbours = [j for j in current[i] if roles[j]["kind"] == "region"]
            if neighbours:
                values.append(
                    sum(
                        bool(set(roles[i]["regions"]) & set(roles[j]["regions"]))
                        for j in neighbours
                    )
                    / len(neighbours)
                )
        return dict(
            channels=len(indices), mean=float(np.mean(values)) if values else None
        )

    return dict(
        content_neighbour_retention=retention(original),
        conditioned_neighbour_retention=retention(target),
        same_topic_neighbours=float(
            np.mean(
                [
                    sum(
                        rows[i].get("subcategory") == rows[j].get("subcategory")
                        for j in neighbours
                    )
                    / k
                    for i, neighbours in enumerate(current)
                ]
            )
        ),
        region_agreement=geo_agreement(
            [i for i, r in enumerate(roles) if r["kind"] == "region"]
        ),
        confirmed_region_agreement=geo_agreement(
            [
                i
                for i, r in enumerate(roles)
                if r["kind"] == "region" and not r["provisional"]
            ]
        ),
        aspect_ratio=float(np.ptp(points[:, 0]) / max(float(np.ptp(points[:, 1])), 1)),
        note="Neighbourhood diagnostics against current labels, not classifier accuracy or independent validation.",
    )


def regional_audit(artifact):
    """Geometry-only outliers; never silently relabel a channel from neighbours."""
    import numpy as np

    nodes = [
        n
        for n in artifact["layout"]["nodes"]
        if n["layout_geography"]["kind"] == "region"
    ]
    if len(nodes) < 2:
        return dict(
            channels=len(nodes), eligible=0, nearest_same_region=None, outliers=[]
        )
    points = np.array([[n["x"], n["y"]] for n in nodes])
    region_members = {}
    for i, node in enumerate(nodes):
        for region in node["layout_geography"]["regions"]:
            region_members.setdefault(region, set()).add(i)
    outliers = []
    eligible = correct = 0
    regions = {}
    for i, node in enumerate(nodes):
        own = set(node["layout_geography"]["regions"])
        same = sorted(set().union(*(region_members[r] for r in own)) - {i})
        record = regions.setdefault(
            node["layout_geography"]["key"],
            dict(channels=0, nearest_same=0, eligible=0),
        )
        record["channels"] += 1
        if not same:
            continue
        distances = np.linalg.norm(points - points[i], axis=1)
        distances[i] = np.inf
        nearest = int(distances.argmin())
        eligible += 1
        record["eligible"] += 1
        if nearest in same:
            correct += 1
            record["nearest_same"] += 1
        else:
            closest_same = min(same, key=lambda j: distances[j])
            outliers.append(
                dict(
                    username=node["username"],
                    name=node["name"],
                    region=node["layout_geography"]["key"],
                    nearest_region=nodes[nearest]["layout_geography"]["key"],
                    nearest_username=nodes[nearest]["username"],
                    same_region_distance=float(distances[closest_same]),
                    other_region_distance=float(distances[nearest]),
                )
            )
    outliers.sort(
        key=lambda n: -(n["same_region_distance"] / max(n["other_region_distance"], 1))
    )
    return dict(
        channels=len(nodes),
        eligible=eligible,
        nearest_same_region=correct / eligible if eligible else None,
        regions=regions,
        outliers=outliers,
        note="Геометрические кандидаты на просмотр, не доказанные ошибки геометок. Одиночные регионы исключены из доли.",
    )


def density_anchor(points):
    import numpy as np

    if len(points) == 1:
        return points[0]
    size = max(40, float(np.sqrt(np.var(points, axis=0).sum())) * 0.25)
    cells = {}
    for i, p in enumerate(points):
        cells.setdefault(tuple(np.floor(p / size).astype(int)), []).append(i)
    chosen = sorted(cells.items(), key=lambda item: (-len(item[1]), item[0]))[0][1]
    return np.asarray(points)[chosen].mean(axis=0)


def map_annotations(rows, roles, coords, topics, groups):
    """Labels mark local concentrations, not the entire extent of a topic."""
    import numpy as np

    collections = {}
    for i, (row, role) in enumerate(zip(rows, roles)):
        topic = row.get("subcategory") or "other"
        group = row.get("topic_group") or topics.get(topic, TOPICS["other"])["group"]
        keys = [("category", group)]
        if topic != "other":
            keys.append(("topic", topic))
        if role["kind"] in ("region", "country"):
            keys.append((role["kind"], role["key"]))
        for key in keys:
            collections.setdefault(key, []).append(i)
    annotations = []
    for (kind, key), indices in collections.items():
        if len(indices) < 2:
            continue
        points = coords[indices]
        anchor = density_anchor(points)
        distances = np.linalg.norm(points - anchor, axis=1)
        # A region caption requires a real local group, never one stray point.
        if kind in ("region", "country") and sum(distances < 180) < 2:
            continue
        if kind == "category":
            label = groups.get(key, {}).get("label", key)
        elif kind == "topic":
            label = topics.get(key, {}).get("label", key)
        else:
            role = roles[indices[0]]
            label = (
                role["label"]
                if kind == "region"
                else taxonomy()
                .get("country_labels", {})
                .get(role["country"], role["country"])
            )
        annotations.append(
            dict(
                id=kind + ":" + key,
                kind=kind,
                label=label,
                x=float(anchor[0]),
                y=float(anchor[1]),
                count=len(indices),
                members=[
                    str(rows[i].get("id") or rows[i]["username"]) for i in indices
                ],
                provisional=any(roles[i]["provisional"] for i in indices)
                if kind in ("region", "country")
                else False,
            )
        )
    return annotations


def make_artifact(rows, roles, coords, policy, method, evidence_summary):
    import numpy as np

    compiled = taxonomy()
    topics = {**TOPICS, **{t["id"]: t for t in compiled["topics"]}}
    groups = {**GROUPS, **{g["id"]: g for g in compiled["groups"]}}
    nodes = []
    grouped, clouded = {}, {}
    bindings = {}
    for i, (row, focus, (x, y)) in enumerate(zip(rows, roles, coords)):
        identifier = str(row.get("id") or row["username"])
        topic = row.get("subcategory") or "other"
        group = row.get("topic_group") or topics.get(topic, TOPICS["other"])["group"]
        cluster = "group:" + group
        neighbourhood = (
            cluster + ":" + topic + (":" + focus["key"] if focus["key"] else "")
        )
        name = groups.get(group, {}).get("label", group)
        color = groups.get(group, {}).get("color", "#9bb6a5")
        nodes.append(
            dict(
                id=identifier,
                username=row["username"],
                name=row.get("name") or row["username"],
                subscribers=row.get("subscribers") or 0,
                topic=topic,
                group=row.get("topic_group") or "",
                x=round(float(x), 4),
                y=round(float(y), 4),
                radius=7,
                cluster=cluster,
                neighbourhood=neighbourhood,
                color=color,
                layout_geography=focus,
            )
        )
        grouped.setdefault(cluster, dict(label=name, color=color, ids=[]))[
            "ids"
        ].append(i)
        topic_label = (
            "Подтема не уточнена"
            if row.get("topic_granularity") == "group"
            else "Тема не определена"
            if row.get("topic_granularity") == "abstain"
            else topics.get(topic, {}).get("label", topic)
        )
        geo_label = (
            focus["label"]
            if focus["kind"] == "region"
            else focus["country"]
            if focus["kind"] == "country"
            else ""
        )
        clouded.setdefault(
            neighbourhood,
            dict(
                cluster=cluster,
                label=topic_label
                + (
                    " · " + geo_label + (" ?" if focus["provisional"] else "")
                    if geo_label
                    else ""
                ),
                color=color,
                ids=[],
            ),
        )["ids"].append(i)
        bindings[identifier] = row_binding(row)
    clusters = []
    clouds = []
    for identifier, group in grouped.items():
        points = coords[group["ids"]]
        anchor = density_anchor(points)
        low = points.min(axis=0) - 40
        high = points.max(axis=0) + 40
        clusters.append(
            dict(
                id=identifier,
                label=group["label"],
                color=group["color"],
                count=len(points),
                x=float(low[0]),
                y=float(low[1]),
                width=float(high[0] - low[0]),
                height=float(high[1] - low[1]),
                cx=float(anchor[0]),
                cy=float(anchor[1]),
                labelX=float(anchor[0]),
                labelY=float(anchor[1] - 30),
            )
        )
    for identifier, cloud in clouded.items():
        points = coords[cloud["ids"]]
        anchor = density_anchor(points)
        radius = min(
            180,
            max(
                35,
                float(np.percentile(np.linalg.norm(points - anchor, axis=1), 60)) + 20,
            ),
        )
        clouds.append(
            dict(
                id=identifier,
                cluster=cloud["cluster"],
                label=cloud["label"],
                color=cloud["color"],
                count=len(points),
                x=float(anchor[0]),
                y=float(anchor[1]),
                radius=radius,
                labelY=float(anchor[1] - 15),
                outline=[],
            )
        )
    return dict(
        format="atlas-semantic-layout-v1",
        method=method,
        created_at=datetime.now(timezone.utc).isoformat(),
        policy_sha256=policy_fingerprint(policy),
        bindings=bindings,
        evidence=evidence_summary,
        layout=dict(
            nodes=nodes,
            clusters=clusters,
            clouds=clouds,
            annotations=map_annotations(rows, roles, coords, topics, groups),
            width=float(coords[:, 0].max() + 140),
            height=float(coords[:, 1].max() + 140),
            mode="topic",
        ),
    )


def compare(
    input_path,
    cache_dir,
    output,
    *,
    limit=None,
    methods=("umap", "tsne"),
    frozen_evidence=None,
):
    from .classification_encoder import CachedEncoder, peak_memory_mb
    from .classification_runtime import policy as classifier_policy

    output = Path(output)
    if output.exists():
        raise ValueError(
            "Используйте новую папку сравнения; существующие результаты не заменяются"
        )
    output.mkdir(parents=True)
    policy = load_policy()
    rows = snapshot_rows(input_path, limit)
    if frozen_evidence is None:
        evidence = freeze_evidence(rows, cache_dir, output)
    else:
        evidence = json.loads(Path(frozen_evidence).read_text(encoding="utf-8"))
        if [e["username"] for e in evidence] != [r["username"] for r in rows]:
            raise ValueError("Состав замороженных постов не совпадает со снимком")
        for name, data in (("snapshot.json", rows), ("evidence.json", evidence)):
            atomic_write_text(
                output / name,
                json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            )
    roles = [geography_role(r, policy) for r in rows]
    summary = dict(
        channels=len(rows),
        with_posts=sum(bool(e["posts"]) for e in evidence),
        profile_only=sum(not e["posts"] for e in evidence),
        regional_channels=sum(r["kind"] == "region" for r in roles),
        provisional_regional_channels=sum(
            r["kind"] == "region" and r["provisional"] for r in roles
        ),
        country_weight=policy["country_distance_weight"],
    )
    encoder = CachedEncoder(
        classifier_policy()["encoder"],
        ROOT / "reports/classification-v11/primary-embeddings.sqlite",
    )
    try:
        vectors = embeddings(rows, evidence, encoder)
        encoder_stats = dict(encoder.stats)
    finally:
        encoder.close()
    raw, conditioned = distance_matrices(vectors, roles, policy)
    report = dict(
        format="atlas-layout-comparison-v1",
        policy=policy,
        summary=summary,
        encoder=encoder_stats,
        methods={},
        warning="Обе проекции исследовательские. Метрики соседства не доказывают точность классов и регионов.",
    )
    report["snapshot_sha256"] = hashlib.sha256(
        (output / "snapshot.json").read_bytes()
    ).hexdigest()
    report["evidence_sha256"] = hashlib.sha256(
        (output / "evidence.json").read_bytes()
    ).hexdigest()
    artifacts = {}
    for method in methods:
        started = time.perf_counter()
        try:
            points = reduce(conditioned, method, policy)
            final, collision = separate_points(points, policy)
            diagnostic = metrics(raw, conditioned, final, rows, roles)
            artifact = make_artifact(rows, roles, final, policy, method, summary)
            validate_artifact(artifact, rows, policy)
            artifacts[method] = artifact
            audit = regional_audit(artifact)
            atomic_write_text(
                output / (method + "-regions.json"),
                json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            )
            atomic_write_text(
                output / (method + ".json"),
                json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n",
            )
            report["methods"][method] = dict(
                status="ok",
                seconds=time.perf_counter() - started,
                metrics=diagnostic,
                geometry=collision,
                peak_memory_mb=peak_memory_mb(),
            )
        except Exception as exc:
            report["methods"][method] = dict(
                status="failed", error=type(exc).__name__ + ": " + str(exc)
            )
        atomic_write_text(
            output / "comparison.json",
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
        print(
            method,
            json.dumps(report["methods"][method], ensure_ascii=False),
            flush=True,
        )
    render_comparison(report, artifacts, output / "comparison.html")
    return report


def render_comparison(report, artifacts, path):
    payload = (
        json.dumps(
            dict(report=report, artifacts=artifacts),
            ensure_ascii=False,
            allow_nan=False,
        )
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (ROOT / "web/layout-compare-template.html").read_text(encoding="utf-8")
    renderer = (
        (ROOT / "web/map.js")
        .read_text(encoding="utf-8")
        .replace("</script", "<\\/script")
    )
    atomic_write_text(
        path, template.replace("/*__DATA__*/", payload).replace("/*__MAP__*/", renderer)
    )


def publish(path, *, apply=False):
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    policy = load_policy()
    if artifact.get("format") != "atlas-semantic-layout-v1" or artifact.get(
        "policy_sha256"
    ) != policy_fingerprint(policy):
        raise ValueError("Проекция не соответствует текущей политике карты")
    age = (
        datetime.now(timezone.utc) - datetime.fromisoformat(artifact["created_at"])
    ).total_seconds()
    if not 0 <= age <= 7 * 86400:
        raise ValueError("Снимок проекции устарел")
    current = snapshot_rows(ROOT / "web/data/channels.json")
    bindings = {str(r.get("id") or r["username"]): row_binding(r) for r in current}
    if bindings != artifact.get("bindings"):
        raise ValueError("Каталог изменился: сначала пересоберите сравнение")
    validate_artifact(artifact, current, policy)
    text = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)
    if not apply:
        return len(bindings)
    target = ROOT / "web/data/atlas_layout.json"
    js = target.with_suffix(".js")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    for old in (target, js):
        if old.exists():
            atomic_write_text(
                ROOT
                / "reports/classification-v11/map-layout/backups"
                / stamp
                / old.name,
                old.read_text(encoding="utf-8"),
            )
    atomic_write_text(target, text + "\n")
    atomic_write_text(
        js, "window.TG_ATLAS_LAYOUT = " + text.replace("<", "\\u003c") + ";\n"
    )
    return len(bindings)


def validate_artifact(artifact, rows, policy):
    """Reject incomplete or malformed geometry before any public file is written."""
    if artifact.get("method") not in ("umap", "tsne"):
        raise ValueError("Неизвестный метод проекции")
    layout = artifact.get("layout") or {}

    def finite(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and abs(value) < 1e8
        )

    for key in ("width", "height"):
        if not finite(layout.get(key)) or layout[key] <= 0:
            raise ValueError("Некорректный размер карты")
    source = {str(r.get("id") or r["username"]): r for r in rows}
    annotations = layout.get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError("Некорректные подписи карты")
    annotation_ids = set()
    for item in annotations:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or item["id"] in annotation_ids
            or not isinstance(item.get("label"), str)
            or item.get("kind") not in ("category", "topic", "region", "country")
            or any(not finite(item.get(k)) for k in ("x", "y"))
            or not isinstance(item.get("members"), list)
            or any(i not in source for i in item["members"])
        ):
            raise ValueError("Некорректная подпись карты")
        annotation_ids.add(item["id"])
    nodes = layout.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != len(source):
        raise ValueError("Неполный набор узлов")
    clusters = layout.get("clusters")
    clouds = layout.get("clouds")
    if not isinstance(clusters, list) or not isinstance(clouds, list):
        raise ValueError("Нет групп карты")
    for collection, fields in (
        (clusters, ("cx", "cy", "labelX", "labelY", "x", "y", "width", "height")),
        (clouds, ("x", "y", "radius", "labelY")),
    ):
        seen = set()
        for item in collection:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or item["id"] in seen
            ):
                raise ValueError("Повторная или повреждённая группа")
            seen.add(item["id"])
            if any(not finite(item.get(key)) for key in fields):
                raise ValueError("Некорректная геометрия группы")
    cluster_ids = {c["id"] for c in clusters}
    cloud_ids = {c["id"] for c in clouds}
    seen = set()
    for node in nodes:
        if (
            not isinstance(node, dict)
            or node.get("id") not in source
            or node["id"] in seen
        ):
            raise ValueError("Повторный или неизвестный узел")
        seen.add(node["id"])
        row = source[node["id"]]
        if (
            any(not finite(node.get(key)) for key in ("x", "y", "radius"))
            or node["radius"] <= 0
        ):
            raise ValueError("Некорректные координаты узла")
        if (
            not 0 <= node["x"] <= layout["width"]
            or not 0 <= node["y"] <= layout["height"]
        ):
            raise ValueError("Узел вне карты")
        if (
            node.get("cluster") not in cluster_ids
            or node.get("neighbourhood") not in cloud_ids
        ):
            raise ValueError("Неизвестная группа узла")
        if (
            node.get("topic") != (row.get("subcategory") or "other")
            or node.get("group") != (row.get("topic_group") or "")
            or node.get("layout_geography") != geography_role(row, policy)
        ):
            raise ValueError("Метки узла не соответствуют снимку")
