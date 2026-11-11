"""Read-only connector for a dedicated Telegram user account.

Используется отдельная локальная Telethon-сессия. Модуль читает список
диалогов, импортирует публичные broadcast-каналы и получает рекомендованные
Telegram публичные каналы. Он не вступает в каналы/чаты, не отправляет
сообщения, не ставит реакции и не скачивает историю постов.
"""

from __future__ import annotations

import csv
import logging
import struct
import time
from pathlib import Path
from typing import Iterable, Optional

from . import config
from .identity import normalize_username

log = logging.getLogger(__name__)


def _make_proxy():
    if not config.TG_PROXY_HOST and not config.TG_PROXY_PORT:
        return None
    if not config.TG_PROXY_HOST or not config.TG_PROXY_PORT:
        raise ValueError(
            "Для Telegram-прокси нужно указать и TG_PROXY_HOST, и TG_PROXY_PORT"
        )
    if not 1 <= config.TG_PROXY_PORT <= 65535:
        raise ValueError("TG_PROXY_PORT должен быть от 1 до 65535")
    if config.TG_PROXY_TYPE not in {"socks5", "http"}:
        raise ValueError("TG_PROXY_TYPE должен быть socks5 или http")
    try:
        import socks
    except ImportError as exc:
        raise RuntimeError(
            "Для заданного прокси установите PySocks: pip install PySocks"
        ) from exc
    proxy_type = socks.SOCKS5 if config.TG_PROXY_TYPE == "socks5" else socks.HTTP
    return (
        proxy_type,
        config.TG_PROXY_HOST,
        config.TG_PROXY_PORT,
        True,
        config.TG_PROXY_USER,
        config.TG_PROXY_PASS,
    )


def _build_client():
    if not config.TG_API_ID or not config.TG_API_HASH:
        raise RuntimeError(
            "Не заданы TG_API_ID/TG_API_HASH. Получите собственные данные приложения "
            "на https://my.telegram.org → API development tools и внесите их в .env."
        )
    try:
        from telethon.sync import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "Telethon не установлен. Выполните: python -m pip install -r requirements-mtproto.txt"
        ) from exc

    session_name = config.TG_SESSION_NAME
    if Path(session_name).name != session_name or session_name in {".", ".."}:
        raise ValueError("TG_SESSION_NAME должен быть простым именем файла без пути")
    session_dir = config.ROOT / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / session_name
    return TelegramClient(
        str(session_path),
        config.TG_API_ID,
        config.TG_API_HASH,
        proxy=_make_proxy(),
        flood_sleep_threshold=60,
        request_retries=2,
        connection_retries=2,
        retry_delay=2,
    )


def _verify_expected_account_id(account_id: int) -> None:
    """Не позволять тихо использовать сессию не того аккаунта."""
    expected = int(config.TG_EXPECTED_USER_ID or 0)
    if expected <= 0:
        raise RuntimeError(
            "Сначала выполните `telegram-auth`, возьмите показанный Telegram ID "
            "резервного аккаунта и укажите его в `.env` как TG_EXPECTED_USER_ID. "
            "До этого чтение через MTProto заблокировано."
        )
    if int(account_id) != expected:
        raise RuntimeError(
            f"Активная Telegram-сессия принадлежит user_id={account_id}, "
            f"а в TG_EXPECTED_USER_ID указан {expected}. Проверьте файл сессии; "
            "операция остановлена."
        )


def authorize() -> None:
    """Интерактивная первая авторизация; повторно использует локальную сессию."""
    client = _build_client()
    try:
        client.start()
        me = client.get_me()
        if not me:
            raise RuntimeError(
                "Telegram не вернул профиль авторизованного пользователя"
            )
        if config.TG_EXPECTED_USER_ID:
            _verify_expected_account_id(me.id)
        log.info(
            "Подключён аккаунт: id=%s, username=@%s",
            me.id,
            me.username or "без username",
        )
        log.info(
            "Убедись, что это именно резервный аккаунт. Сессия хранится локально: "
            "sessions/%s.session",
            config.TG_SESSION_NAME,
        )
    finally:
        client.disconnect()


def _connect_authorized():
    client = _build_client()
    client.connect()
    if not client.is_user_authorized():
        client.disconnect()
        raise RuntimeError(
            "Telegram-аккаунт ещё не авторизован. Сначала выполните: python main.py telegram-auth"
        )
    try:
        me = client.get_me()
        if not me:
            raise RuntimeError("Telegram не вернул профиль активной сессии")
        _verify_expected_account_id(me.id)
    except Exception:
        client.disconnect()
        raise
    return client


def _public_broadcast_candidate(entity) -> Optional[dict]:
    """Извлечь только публичный broadcast-канал из entity Telegram."""
    if not getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        return None
    username = getattr(entity, "username", None)
    username = normalize_username(username or "")
    if not username:
        return None
    return {
        "username": username,
        "telegram_id": getattr(entity, "id", None),
        "name": getattr(entity, "title", None) or username,
        "subscribers": getattr(entity, "participants_count", None),
        "country": None,
        "category": None,
        "region_hint": None,
    }


def _preview_path(filename: str) -> Path:
    reports_dir = config.ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir / filename


def _write_candidate_preview(path: Path, rows: list[dict], extra_fields=()) -> None:
    fieldnames = list(
        dict.fromkeys(
            [
                "username",
                "telegram_id",
                "name",
                "subscribers",
                "category",
                "region_hint",
                *extra_fields,
            ]
        )
    )
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def import_subscriptions(
    limit: int = 500,
    offset: int = 0,
    apply: bool = False,
) -> int:
    """Просмотреть публичные каналы из списка диалогов резервного аккаунта.

    limit/offset задают окно среди публичных broadcast-каналов, а не среди
    всех диалогов. Это позволяет импортировать большие списки партиями.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("--limit должен быть от 1 до 1000")
    if offset < 0:
        raise ValueError("--offset не может быть отрицательным")

    client = _connect_authorized()
    candidates = []
    skipped_without_username = 0
    scanned = 0
    eligible_seen = 0
    try:
        from telethon.tl.types import Channel as TelegramChannel

        for dialog in client.iter_dialogs(limit=None):
            scanned += 1
            entity = dialog.entity
            if not isinstance(entity, TelegramChannel):
                continue
            candidate = _public_broadcast_candidate(entity)
            if candidate is None:
                if getattr(entity, "broadcast", False):
                    skipped_without_username += 1
                continue
            if eligible_seen < offset:
                eligible_seen += 1
                continue
            candidates.append(candidate)
            eligible_seen += 1
            if len(candidates) >= limit:
                break
    finally:
        client.disconnect()

    preview_name = (
        "telegram_subscriptions_preview.csv"
        if offset == 0
        else f"telegram_subscriptions_preview_{offset}_{offset + limit}.csv"
    )
    preview_path = _preview_path(preview_name)
    _write_candidate_preview(preview_path, candidates)

    if apply and candidates:
        from . import db

        conn = db.connect()
        try:
            with db.transaction(conn):
                for candidate in candidates:
                    db.upsert_candidate(
                        conn,
                        candidate,
                        source_name="telegram_subscriptions",
                        source_ref=str(
                            candidate.get("telegram_id") or candidate["username"]
                        ),
                        source_url="https://web.telegram.org/",
                        status="validated",
                    )
        finally:
            conn.close()

    log.info(
        "Диалогов просмотрено: %d; окно публичных каналов: %d–%d",
        scanned,
        offset,
        offset + len(candidates),
    )
    log.info(
        "Публичных каналов-кандидатов: %d; broadcast-каналов без username пропущено: %d",
        len(candidates),
        skipped_without_username,
    )
    log.info("Предпросмотр: %s", preview_path)
    if apply:
        log.info(
            "Кандидаты добавлены в PostgreSQL; они ещё не включены в рабочую карту."
        )
    else:
        log.info("Только предпросмотр. Для записи повторите команду с --apply.")
    return len(candidates)


def _build_recommendations_request(channel=None):
    """Создать TL-запрос; fallback поддерживает Telethon без сгенерированного метода.

    Конструктор 0x25A71742 и схема flags/channel взяты из официального TL schema.
    Ответом является обычный messages.Chats, который Telethon умеет читать.
    """
    try:
        from telethon.tl.functions.channels import GetChannelRecommendationsRequest

        return GetChannelRecommendationsRequest(channel=channel)
    except ImportError:
        try:
            from telethon.tl.tlobject import TLRequest
        except ImportError as exc:
            raise RuntimeError(
                "Установленная версия MTProto-клиента не поддерживает TLRequest; "
                "переустановите requirements-mtproto.txt"
            ) from exc

        class GetChannelRecommendationsRaw(TLRequest):
            CONSTRUCTOR_ID = 0x25A71742

            def __init__(self, input_channel=None):
                self.channel = input_channel

            def _bytes(self):
                flags = 1 if self.channel is not None else 0
                body = struct.pack("<Ii", self.CONSTRUCTOR_ID, flags)
                if self.channel is not None:
                    body += bytes(self.channel)
                return body

        return GetChannelRecommendationsRaw(channel)


def _input_channel(client, seed: str):
    """Преобразовать разрешённый аккаунту канал в TL InputChannel."""
    try:
        from telethon.tl.types import InputChannel
    except ImportError as exc:
        raise RuntimeError("Не удалось импортировать Telethon InputChannel") from exc

    peer = client.get_input_entity(seed)
    if hasattr(peer, "channel_id") and hasattr(peer, "access_hash"):
        return InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)
    if hasattr(peer, "id") and hasattr(peer, "access_hash"):
        return InputChannel(channel_id=peer.id, access_hash=peer.access_hash)
    raise ValueError(f"@{seed} не разрешился в публичный Telegram-канал")


def _extract_recommendation_candidates(result) -> list[dict]:
    """Оставить в ответе Telegram только публичные broadcast-каналы."""
    chats = getattr(result, "chats", None) or []
    candidates = []
    for entity in chats:
        candidate = _public_broadcast_candidate(entity)
        if candidate:
            candidates.append(candidate)
    return candidates


def discover_recommendations(
    seeds: Optional[Iterable[str]] = None,
    *,
    from_subscriptions: bool = False,
    global_recommendations: bool = False,
    max_seeds: int = 5,
    limit: int = 100,
    apply: bool = False,
) -> int:
    """Получить рекомендации Telegram и добавить их в реестр кандидатов.

    Telegram поддерживает один ответ без пагинации. Рекомендации для seed
    возможны только если аккаунт уже состоит в этом канале; функция никогда
    не вступает в него автоматически. Все результаты сначала пишутся в CSV.
    """
    if config.TG_RECOMMENDATION_DELAY < 1.0:
        raise ValueError("TG_RECOMMENDATION_DELAY не должна быть меньше 1 секунды")
    if not 1 <= max_seeds <= config.TG_MAX_RECOMMENDATION_SEEDS:
        raise ValueError(
            f"--max-seeds должен быть от 1 до {config.TG_MAX_RECOMMENDATION_SEEDS}"
        )
    if not 1 <= limit <= 500:
        raise ValueError("--limit должен быть от 1 до 500")
    clean_seeds = []
    for seed in seeds or []:
        username = normalize_username(seed)
        if username and username not in clean_seeds:
            clean_seeds.append(username)
    if (
        sum(
            bool(x)
            for x in (from_subscriptions, global_recommendations, bool(clean_seeds))
        )
        != 1
    ):
        raise ValueError(
            "Выберите один режим: --seed, --from-subscriptions или --global"
        )
    if clean_seeds and len(clean_seeds) > max_seeds:
        raise ValueError(
            f"Передано {len(clean_seeds)} seed-каналов, максимум {max_seeds}"
        )

    client = _connect_authorized()
    collected: list[dict] = []
    request_count = 0
    returned_total = None
    try:
        from telethon.errors import FloodWaitError

        targets: list[tuple[str, Optional[str]]] = []
        if clean_seeds:
            targets = [(seed, seed) for seed in clean_seeds]
        elif from_subscriptions:
            from telethon.tl.types import Channel as TelegramChannel

            seen = set()
            for dialog in client.iter_dialogs(limit=None):
                entity = dialog.entity
                if not isinstance(entity, TelegramChannel):
                    continue
                candidate = _public_broadcast_candidate(entity)
                if not candidate or candidate["username"] in seen:
                    continue
                seen.add(candidate["username"])
                targets.append((candidate["username"], candidate["username"]))
                if len(targets) >= max_seeds:
                    break
            if not targets:
                log.warning(
                    "В подписках аккаунта не найдено публичных broadcast-каналов"
                )
                return 0
        else:
            targets = [("__global__", None)]

        for index, (source_ref, seed) in enumerate(targets, start=1):
            try:
                input_channel = _input_channel(client, seed) if seed else None
                request = _build_recommendations_request(input_channel)
                response = client(request)
                count = getattr(response, "count", None)
                if count is not None:
                    returned_total = (
                        count if returned_total is None else max(returned_total, count)
                    )
                rows = _extract_recommendation_candidates(response)
                for row in rows:
                    if seed and row["username"] == seed:
                        continue
                    row["recommended_from"] = seed or "global_account_recommendations"
                    collected.append(row)
                    if len(collected) >= limit:
                        break
                request_count += 1
                log.info(
                    "[%d/%d] seed=%s — публичных рекомендаций: %d",
                    index,
                    len(targets),
                    seed or "global",
                    len(rows),
                )
                if len(collected) >= limit:
                    break
            except FloodWaitError as exc:
                seconds = getattr(exc, "seconds", None)
                log.error(
                    "Telegram запросил FloodWait%s. Останавливаю поиск без обхода ограничения.",
                    f" на {seconds} сек." if seconds is not None else "",
                )
                break
            except Exception as exc:
                # Ошибка одного seed не скрывает результаты остальных; ошибки
                # доступа к seed обычно означают, что аккаунт в канал не вступал.
                log.warning(
                    "seed=%s — рекомендации не получены: %s", seed or "global", exc
                )

            if index < len(targets):
                time.sleep(config.TG_RECOMMENDATION_DELAY)
    finally:
        client.disconnect()

    # Внутри одного ответа Telegram могут повторяться те же каналы для разных
    # seed. Сохраняем source_ref для каждого seed, чтобы не терять происхождение.
    preview_path = _preview_path("telegram_recommendations_preview.csv")
    _write_candidate_preview(
        preview_path, collected, extra_fields=("recommended_from",)
    )

    if apply and collected:
        from . import db

        conn = db.connect()
        try:
            with db.transaction(conn):
                for row in collected:
                    seed = row.get("recommended_from") or "global"
                    db.upsert_candidate(
                        conn,
                        row,
                        source_name="telegram_recommendations",
                        source_ref=seed,
                        source_url=(
                            f"https://t.me/{seed}"
                            if seed != "global_account_recommendations"
                            else "https://web.telegram.org/"
                        ),
                        status="validated",
                    )
        finally:
            conn.close()

    log.info(
        "MTProto recommendations: запросов выполнено %d, каналов-кандидатов %d",
        request_count,
        len(collected),
    )
    if returned_total is not None and returned_total > len(collected):
        log.info(
            "Telegram сообщил count=%d, но в этом ответе доступно %d; у обычных аккаунтов "
            "Telegram может возвращать сокращённый набор без пагинации.",
            returned_total,
            len(collected),
        )
    log.info("Предпросмотр: %s", preview_path)
    if apply:
        log.info(
            "Кандидаты добавлены в PostgreSQL; импортировать в channels можно отдельно."
        )
    else:
        log.info("Только предпросмотр. Для записи повторите команду с --apply.")
    return len(collected)
