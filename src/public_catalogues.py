"""Bounded, robots-aware public HTML discovery. No login or private API calls."""

from dataclasses import dataclass
import hashlib
import ipaddress
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests

from .file_io import atomic_write_text
from .identity import normalize_username
from .metrics import parse_count

USER_AGENT = "TGAtlasCatalogBot/1.0 (public Telegram channel directory)"


class SourceError(RuntimeError):
    pass


class SourceBlocked(SourceError):
    pass


class SourceBudgetExhausted(SourceError):
    pass


@dataclass
class Listing:
    cards: list
    last_page: int = 1


def public_username(href):
    parsed = urlparse(str(href or ""))
    if parsed.scheme == "tg" and parsed.netloc == "resolve":
        return normalize_username(parse_qs(parsed.query).get("domain", [""])[0])
    if parsed.scheme in ("http", "https") or (not parsed.scheme and parsed.netloc):
        return normalize_username("https:" + href if not parsed.scheme else href)
    return ""


def parse_tgramsearch_listing(html, url, minimum=1000):
    soup = BeautifulSoup(html, "html.parser")
    cards = {}
    elements = soup.select("div.tg-channel")
    if (
        not elements
        and "ничего не найдено" not in soup.get_text(" ", strip=True).lower()
    ):
        raise SourceError(
            "TgramSearch: неизвестная разметка списка, страница не завершена"
        )
    for card in elements:
        link = card.select_one('.tg-channel__link a[href^="/join/"]')
        if not link or not re.fullmatch(r"/join/\d+", link.get("href", "")):
            continue
        if "приватный" in card.get_text(" ", strip=True).lower():
            continue
        categories = [
            a.get("href", "") for a in card.select(".tg-channel__categories a")
        ]
        if any(x in categories for x in ("/categories/chats", "/categories/bots")):
            continue
        count = card.select_one(".tg-stat__user-count")
        subscribers = parse_count(count.get_text()) if count else None
        if subscribers is not None and subscribers < minimum:
            continue
        description = card.select_one(".tg-channel__description")
        detail = urljoin("https://tgramsearch.com", link["href"])
        cards[detail] = dict(
            detail_url=detail,
            source="tgramsearch",
            source_url=url,
            name=link.get_text(" ", strip=True),
            description=description.get_text(" ", strip=True) if description else "",
            catalog_subscribers=subscribers,
        )
    pages = [1]
    current = urlparse(url)
    for a in soup.select('a[href*="page="]'):
        parsed = urlparse(urljoin(url, a["href"]))
        if parsed.netloc != current.netloc or parsed.path != current.path:
            continue
        query = parse_qs(parsed.query)
        if query.get("query") != parse_qs(current.query).get("query"):
            continue
        value = query.get("page", [""])[0]
        if value.isdigit():
            pages.append(int(value))
    return Listing(list(cards.values()), max(pages))


def parse_tgramsearch_detail(html, url):
    soup = BeautifulSoup(html, "html.parser")
    # Only the primary channel heading. Never scrape contact handles or ads.
    link = soup.select_one("h1.tg-channel__header a[href]")
    if not link and not soup.select_one("h1.tg-channel__header"):
        raise SourceError("TgramSearch: не найдена основная карточка канала")
    username = public_username(link.get("href")) if link else ""
    if not username or username.endswith("bot"):
        return None
    return dict(username=username, name=link.get_text(" ", strip=True), source_url=url)


def parse_tlgrm_listing(html, url, minimum=1000):
    soup = BeautifulSoup(html, "html.parser")
    rows = {}
    elements = soup.select(".channel-card")
    if not elements:
        raise SourceError("tlgrm: неизвестная разметка списка, страница не завершена")
    for card in elements:
        if "channel-card--promoted" in card.get("class", []):
            continue
        link = card.select_one("a.channel-card__username[href]")
        if not link:
            continue
        parsed = urlparse(urljoin(url, link["href"]))
        if parsed.netloc not in ("tlgrm.ru", "www.tlgrm.ru"):
            continue
        match = re.fullmatch(r"/channels/@([A-Za-z0-9_]+)", parsed.path)
        username = normalize_username(match[1]) if match else ""
        if not username or username.endswith("bot"):
            continue
        count = card.select_one(".channel-card__subscribers")
        subscribers = parse_count(count.get_text()) if count else None
        if subscribers is not None and subscribers < minimum:
            continue
        name = card.select_one(".channel-card__title")
        description = card.select_one(".channel-card__description")
        rows[username] = dict(
            username=username,
            source="tlgrm",
            source_url=url,
            name=name.get_text(" ", strip=True) if name else username,
            description=description.get_text(" ", strip=True) if description else "",
            catalog_subscribers=subscribers,
        )
    return Listing(list(rows.values()))


def parse_official_links(html, url):
    soup = BeautifulSoup(html, "html.parser")
    rows = {}
    for link in soup.select("a[href]"):
        username = public_username(link.get("href"))
        if username and not username.endswith("bot"):
            rows[username] = dict(
                username=username,
                source="official_website",
                source_url=url,
                name=link.get_text(" ", strip=True) or username,
                description="",
            )
    return Listing(list(rows.values()))


def page_url(base, page):
    parsed = urlparse(base)
    args = parse_qs(parsed.query)
    args.pop("page", None)
    if page > 1:
        args["page"] = [str(page)]
    return parsed._replace(query=urlencode(args, doseq=True)).geturl()


class PublicHTMLClient:
    def __init__(self, allowed_hosts, cache_dir, *, interval=1.1, max_requests=10000):
        self.allowed_hosts = set(allowed_hosts)
        self.cache_dir = Path(cache_dir)
        self.interval = max(1.0, float(interval))
        self.max_requests = max_requests
        self.requests = 0
        self.lock = threading.Lock()
        self.robot_lock = threading.Lock()
        self.robots = {}
        self.next_at = {}
        self.host_intervals = {}
        self.blocked = set()
        self.local = threading.local()

    def _validate_url(self, url):
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or host not in self.allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise SourceError(
                "Источник или перенаправление не входят в разрешённый HTTPS-список"
            )
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host in ("localhost",) or host.endswith((".local", ".internal")):
                raise SourceError("Локальные адреса не являются публичными источниками")
        else:
            raise SourceError("IP-адреса не допускаются в качестве источников")
        return host

    def _request(self, url):
        host = self._validate_url(url)
        while True:
            with self.lock:
                if host in self.blocked:
                    raise SourceBlocked(
                        f"Источник {host} остановлен после ограничения доступа"
                    )
                if self.requests >= self.max_requests:
                    raise SourceBudgetExhausted(
                        "Достигнут бюджет HTTP-запросов источников"
                    )
                delay = self.next_at.get(host, 0) - time.monotonic()
                if delay <= 0:
                    self.next_at[host] = time.monotonic() + self.host_intervals.get(
                        host, self.interval
                    )
                    self.requests += 1
                    break
            time.sleep(delay)
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers.update({"User-Agent": USER_AGENT})
        response = self.local.session.get(
            url, timeout=(10, 20), allow_redirects=False, stream=True
        )
        if response.status_code in (401, 403, 429):
            with self.lock:
                self.blocked.add(host)
            status = response.status_code
            response.close()
            raise SourceBlocked(
                f"{host}: HTTP {status}; обход ограничения не выполняется"
            )
        data = bytearray()
        try:
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise SourceError("HTML источника превышает 2 MB")
        finally:
            response.close()
        return (
            response.status_code,
            response.headers,
            data.decode("utf-8", errors="replace"),
        )

    def get(self, url, *, max_age_hours=168):
        host = self._validate_url(url)
        # Check robots even for cached pages; no public/private API guessing.
        with self.robot_lock:
            if host not in self.robots:
                status, _, text = self._request(f"https://{host}/robots.txt")
                robot = RobotFileParser()
                if status == 404:
                    robot.parse(["User-agent: *", "Allow: /"])
                elif status == 200 and "<html" not in text.lower():
                    robot.parse(text.splitlines())
                else:
                    raise SourceBlocked(
                        f"Не удалось безопасно проверить robots.txt: {host}"
                    )
                self.robots[host] = robot
                delay = robot.crawl_delay(USER_AGENT) or robot.crawl_delay("*") or 0
                rate = robot.request_rate(USER_AGENT) or robot.request_rate("*")
                if rate and rate.requests > 0:
                    delay = max(delay, rate.seconds / rate.requests)
                with self.lock:
                    self.host_intervals[host] = max(self.interval, delay)
                    self.next_at[host] = max(
                        self.next_at.get(host, 0), time.monotonic() + delay
                    )
            robot = self.robots[host]
        if not robot.can_fetch(USER_AGENT, url):
            raise SourceBlocked(
                f"robots.txt не разрешает обход {host}{urlparse(url).path}"
            )
        target = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".html")
        if (
            target.exists()
            and 0 <= time.time() - target.stat().st_mtime < max_age_hours * 3600
        ):
            return target.read_text(encoding="utf-8")
        current = url
        for _ in range(4):
            status, headers, text = self._request(current)
            if status in (301, 302, 303, 307, 308):
                redirected = urljoin(current, headers.get("Location", ""))
                if urlparse(redirected).netloc != urlparse(current).netloc:
                    raise SourceBlocked(
                        "Межсайтовое перенаправление требует отдельного источника"
                    )
                if not robot.can_fetch(USER_AGENT, redirected):
                    raise SourceBlocked("Перенаправление запрещено robots.txt")
                current = redirected
                continue
            if status != 200:
                raise SourceError(f"{host}: HTTP {status}")
            if any(
                x in text.lower()
                for x in ("cf-chl-", "verify you are human", "captcha challenge")
            ):
                raise SourceBlocked(f"{host}: проверка доступа, источник пропущен")
            atomic_write_text(target, text)
            return text
        raise SourceError("Слишком много перенаправлений")
