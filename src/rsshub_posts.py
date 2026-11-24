"""RSSHub Telegram feeds → existing classification-only samples; no media fetches."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from lxml import etree

MAX_FEED_BYTES = 2_000_000
# Suppress quoted/forwarding metadata, service messages and external expansions.
# A routeParams segment explicitly selects RSSHub's public web-preview backend.
ROUTE_OPTIONS = (
    "showLinkPreview=0&showViaBot=0&showReplyTo=0&showFwdFrom=0&"
    "showFwdFromAuthor=0&showInlineButtons=0&showMediaTagInTitle=0&"
    "includeServiceMsg=0&includeTelegraph=0"
)


def normalize_base_url(value):
    value = str(value).strip().rstrip("/")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Некорректный адрес RSSHub") from exc
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.scheme not in ("http", "https")
        or parts.scheme == "http"
        and parts.hostname not in ("localhost", "127.0.0.1", "::1")
        or not re.fullmatch(r"/[a-zA-Z0-9_/-]*|", parts.path)
        or port == 0
    ):
        raise ValueError(
            "RSSHub: нужен HTTPS URL или HTTP localhost, без пароля, query и fragment"
        )
    return value


def feed_url(base_url, username):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
        raise ValueError("Нужен публичный username без пути")
    return (
        normalize_base_url(base_url)
        + "/telegram/channel/"
        + username
        + "/"
        + ROUTE_OPTIONS
    )


def message_identity(url, username):
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or parts.hostname
            not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me")
            or parts.username is not None
            or parts.port is not None
        ):
            return None
        match = re.fullmatch(
            r"/(?:s/)?([a-zA-Z0-9_]+)/([1-9][0-9]{0,15})/?", parts.path
        )
        if match and match[1].lower() == username:
            return int(match[2])
    except ValueError:
        pass
    return None


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def parse_feed(payload, username, *, now=None):
    """RSS 2.0/Atom only. Never resolve entities or fetch linked content."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
        raise ValueError("Некорректный username ленты")
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("RSSHub: лента превышает 2 МБ")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        root = etree.fromstring(payload, parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError("RSSHub: ответ не является корректной XML-лентой") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError("RSSHub: DTD и XML-сущности запрещены")
    atom = root.tag == "{http://www.w3.org/2005/Atom}feed"
    if atom:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
    elif root.tag == "rss" and root.find("channel") is not None:
        entries = root.findall("channel/item")
    else:
        raise ValueError("RSSHub: ожидается RSS 2.0 или Atom, а не страница ошибки")
    if len(entries) > 500:
        raise ValueError("RSSHub: в одной ленте допускается до 500 записей")
    posts, seen = [], set()
    for entry in entries:
        if atom:
            links = entry.findall("a:link", ns)
            link = next(
                (
                    e.get("href", "")
                    for e in links
                    if e.get("rel", "alternate") == "alternate"
                ),
                "",
            )
            date = entry.findtext("a:published", namespaces=ns) or ""
            body = entry.find("a:content", ns)
            if body is None:
                body = entry.find("a:summary", ns)
        else:
            link = entry.findtext("link") or entry.findtext("guid") or ""
            date = (
                entry.findtext("pubDate")
                or entry.findtext("{http://purl.org/dc/elements/1.1/}date")
                or ""
            )
            body = entry.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            if body is None:
                body = entry.find("description")
        message_id, posted_at = (
            message_identity(link.strip(), username),
            _date(date.strip()),
        )
        if (
            message_id is None
            or posted_at is None
            or message_id in seen
            or body is None
        ):
            continue
        markup = (body.text or "") + "".join(
            etree.tostring(child, encoding="unicode") for child in body
        )
        if atom and body.get("type", "text") == "text":
            text = markup.strip()
        else:
            soup = BeautifulSoup(markup, "html.parser")
            for unwanted in soup.select("script,style,iframe,noscript,.rsshub-quote"):
                unwanted.decompose()
            text = soup.get_text("\n", strip=True)
        if not text:
            continue
        seen.add(message_id)
        posts.append(
            dict(
                message_id=message_id,
                text=text[:4000],
                truncated=len(text) > 4000,
                date=posted_at.isoformat(),
                url=f"https://t.me/{username}/{message_id}",
                text_hash=hashlib.sha256(
                    " ".join(text.casefold().split()).encode("utf-8")
                ).hexdigest(),
            )
        )
    posts.sort(key=lambda p: (p["date"], p["message_id"]), reverse=True)
    return dict(
        version=1,
        purpose="classification_only",
        source="rsshub",
        username=username,
        fetched_at=(now or datetime.now(timezone.utc)).isoformat(),
        page_budget=1,
        pages_requested=1,
        posts=posts,
        cache_hit=False,
    )
