"""Загрузка страниц сайта поставщика и очистка до текста."""
import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import db
from .config import get_settings

UA = "Mozilla/5.0 (compatible; SupplierFinderBot/0.1; +prototype)"
MAX_BYTES = 2_000_000
MAX_CHARS_PER_PAGE = 12_000
MAX_SUBPAGES = 4

# что ищем в ссылках: контакты, доставка, опт, документы
SUBPAGE_HINTS = ("контакт", "доставк", "опт", "о-компании", "о компании", "о нас", "сертифик",
                 "документ", "прайс", "условия", "оплата", "партнер", "поставщик",
                 "contact", "delivery", "about", "price", "wholesale", "docs", "certif")


def clean_html(html: str | bytes) -> tuple[str, str]:
    """Возвращает (текст страницы, заголовок). Телефоны и почту из ссылок дописываем в текст."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    extra = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("tel:"):
            extra.append("Телефон: " + href[4:])
        elif href.lower().startswith("mailto:"):
            extra.append("Email: " + href[7:].split("?")[0])
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    if extra:
        text += "\n" + "\n".join(dict.fromkeys(extra))
    return text[:MAX_CHARS_PER_PAGE], title


def find_subpages(html: str | bytes, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_host = urlparse(base_url).hostname
    scored: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        p = urlparse(url)
        if p.scheme not in ("http", "https") or p.hostname != base_host:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|pdf|zip|docx?|xlsx?)$", p.path, re.I):
            continue
        hay = (a.get_text(" ", strip=True) + " " + p.path).lower()
        for i, hint in enumerate(SUBPAGE_HINTS):
            if hint in hay:
                scored.append((i, url))
                break
    scored.sort(key=lambda t: t[0])
    out: list[str] = []
    for _, url in scored:
        if url.rstrip("/") != base_url.rstrip("/") and url not in out:
            out.append(url)
    return out[:MAX_SUBPAGES]


async def fetch_html(url: str, client: httpx.AsyncClient) -> str | bytes | None:
    """Если сервер назвал кодировку в заголовке — декодируем ею, иначе отдаём байты:
    тогда её определяет BeautifulSoup по <meta> (так читаются сайты на windows-1251)."""
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.7"})
        if r.status_code >= 400:
            return None
        if "html" not in r.headers.get("content-type", "").lower():
            return None
        raw = r.content[:MAX_BYTES]
        m = re.search(r"charset=([\w-]+)", r.headers.get("content-type", ""), re.I)
        if m:
            try:
                return raw.decode(m.group(1), errors="replace")
            except LookupError:
                pass
        return raw
    except httpx.HTTPError:
        return None


LINKS_MARK = "\n@@LINKS@@\n"


async def load_page(u: str, client: httpx.AsyncClient, ttl: int) -> tuple[str, list[str]] | None:
    """Текст страницы и её полезные ссылки; в кэше SQLite лежат вместе."""
    cached = db.get_cached_page(u, ttl)
    if cached is not None:
        text, _, links = cached.partition(LINKS_MARK)
        return text, [x for x in links.split("\n") if x]
    html = await fetch_html(u, client)
    if html is None:
        return None
    text, _ = clean_html(html)
    links = find_subpages(html, u)
    db.put_cached_page(u, text + LINKS_MARK + "\n".join(links))
    return text, links


async def fetch_site(url: str, client: httpx.AsyncClient | None = None) -> dict[str, str]:
    """Главная + подходящие внутренние страницы. Возвращает {url: текст}."""
    ttl = get_settings().page_ttl_days
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=True, verify=False)
    pages: dict[str, str] = {}
    try:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}/"
        start = [root] if url.rstrip("/") == root.rstrip("/") else [root, url]
        subs: list[str] = []
        for u in start:
            got = await load_page(u, client, ttl)
            if got:
                pages[u] = got[0]
                subs += got[1]
        subs = [u for u in dict.fromkeys(subs) if u not in pages][:MAX_SUBPAGES]
        for u, got in zip(subs, await asyncio.gather(*(load_page(u, client, ttl) for u in subs))):
            if got and got[0]:
                pages[u] = got[0]
        return pages
    finally:
        if own:
            await client.aclose()
