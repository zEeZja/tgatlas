"""Единая политика минимального размера аудитории для каталога."""

from __future__ import annotations

from .metrics import parse_count


def meets_minimum_subscribers(value, minimum: int) -> bool:
    """True только когда число подписчиков известно и не ниже порога."""
    count = parse_count(value)
    return count is not None and count >= minimum


def normalize_candidate_state(status: str, subscribers, minimum: int) -> tuple[str, str | None]:
    """Не позволяет подтвердить/импортировать канал без достаточной аудитории."""
    if status not in {"pending", "validated", "rejected"}:
        raise ValueError(f"Недопустимый статус кандидата: {status}")

    count = parse_count(subscribers)
    if count is not None and count < minimum:
        return "rejected", f"below_min_subscribers:{count}<{minimum}"

    if status == "validated" and count is None:
        return "pending", "subscriber_count_unknown"

    if status == "pending" and count is None:
        return "pending", "subscriber_count_unknown"

    if count is not None and count >= minimum:
        return status, None

    return status, None
