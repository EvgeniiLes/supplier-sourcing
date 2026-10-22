"""Отзывы о компании на открытых сайтах и краткое резюме от ИИ.

Отзывы Яндекс Карт программно не отдаются, поэтому читаем открытые страницы отзовиков из выдачи поиска.
Резюме строится только по прочитанному тексту: каждая мысль идёт с цитатой, которую код находит на странице;
не нашли отзывов, так и пишем."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from .config import get_settings
from .fetch import load_page
from .models import Fact, ReviewPoint, ReviewSummary, Supplier
from .score import region_matches
from .search import tavily_search, yandex_search
from .verify import PageIndex, norm

REVIEW_HOSTS = ("otzovik.com", "irecommend.ru", "flamp.ru", "zoon.ru", "yell.ru", "2gis.ru", "orgpage.ru", "spr.ru",
                "otzyvru.com", "pulscen.ru", "market.yandex.ru")
REVIEW_PATH = re.compile(r"otzyv|otzov|review|отзыв", re.I)
MAX_PAGES = 3
MAX_CHARS = 7000
FRESH_DAYS = 14
LEGAL = {"ооо", "оао", "зао", "пао", "ао", "ип", "тд", "тк", "компания", "торговый", "торговая", "дом", "группа", "холдинг"}
# слова, которые есть у десятков разных компаний: по ним нельзя узнать, что отзыв именно об этой
GENERIC = {"хорека", "horeca", "хореку", "сервис", "service", "продукт", "продукты", "опт", "оптом", "групп", "торг",
           "трейд", "trade", "food", "фуд", "market", "маркет", "снаб", "снабжение", "урал", "сибирь", "центр"}
# отзывы привязаны к филиалу в городе: берём, только если страница про город пользователя
CITY_HOSTS = ("2gis.ru", "flamp.ru", "zoon.ru", "yell.ru")

TOOL = {
    "name": "save_reviews",
    "description": "Сохранить краткое резюме отзывов о компании, составленное ТОЛЬКО по тексту страниц.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean", "description": (
                "true только если в тексте есть настоящие отзывы клиентов именно об ЭТОЙ компании. "
                "Если страницы про другую компанию, про каталог, или отзывов нет, поставь false.")},
            "summary": {"type": "string", "description": (
                "2–3 коротких предложения по-русски: что чаще хвалят и на что жалуются. Только то, что есть в отзывах. "
                "Если отзывов мало или они противоречат друг другу, так и скажи.")},
            "positives": {"type": "array", "description": "До 3 главных похвал", "items": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Мысль коротко, своими словами"},
                "quote": {"type": "string", "description": "ДОСЛОВНАЯ цитата из отзыва, до 200 символов, без изменений"},
                "url": {"type": "string", "description": "Адрес страницы из заголовка [PAGE ...]"}},
                "required": ["text", "quote", "url"]}},
            "negatives": {"type": "array", "description": "До 3 главных жалоб", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "quote": {"type": "string"}, "url": {"type": "string"}},
                "required": ["text", "quote", "url"]}},
            "rating": {"anyOf": [{"type": "object", "properties": {
                "value": {"type": "string", "description": "Рейтинг как написан («4,2 из 5», «4.5»)"},
                "quote": {"type": "string", "description": "ДОСЛОВНАЯ цитата с рейтингом"},
                "url": {"type": "string"}}, "required": ["value", "quote", "url"]}, {"type": "null"}],
                "description": "Общий рейтинг компании, только если он прямо указан на странице"},
        },
        "required": ["found", "summary", "positives", "negatives", "rating"],
    },
}

SYSTEM = (
    "Ты помогаешь снабженцу оценить поставщика по отзывам клиентов. Тебе дан текст страниц с отзывами. "
    "Используй ТОЛЬКО этот текст. Ничего не выдумывай и не домысливай, не подмешивай общие знания о компании. "
    "Если на странице отзывы о другой компании или это реклама самой компании, а не мнение клиентов, не используй их. "
    "Цитаты копируй дословно, без правок. Не пересказывай отзывы о товарах, если они не говорят о работе компании."
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is_branch_page(url: str) -> bool:
    """Карточка филиала в городе (2ГИС, Флампа, Зун, Яндекс Карты), а не общая страница отзывов о магазине."""
    h = _host(url)
    return any(h == c or h.endswith("." + c) for c in CITY_HOSTS) or (h == "yandex.ru" and "/maps/" in url)


def brand_of(sup: Supplier) -> str:
    """Название для поиска: то, что в кавычках, иначе всё название, иначе домен."""
    name = sup.name.value if sup.name else ""
    quoted = re.findall(r"[«\"“]([^»\"”]{3,})[»\"”]", name)
    return (quoted[0] if quoted else name).strip() or sup.domain


def brand_tokens(sup: Supplier) -> list[str]:
    """Слова, по которым узнаём, что страница про эту компанию."""
    words = [w for w in re.findall(r"[a-zа-я0-9]+", norm(brand_of(sup))) if len(w) >= 4 and w not in LEGAL | GENERIC]
    stem = sup.domain.split(".")[0].lower()
    if len(stem) >= 4 and stem not in GENERIC:
        words.append(stem)
    return list(dict.fromkeys(words))


def is_review_url(url: str, own_domain: str) -> bool:
    host = _host(url)
    if not host or host == own_domain or host.endswith("." + own_domain):
        return False
    return any(host == h or host.endswith("." + h) for h in REVIEW_HOSTS) or bool(REVIEW_PATH.search(url))


async def search_review_urls(sup: Supplier, region: str, http: httpx.AsyncClient) -> list[str] | None:
    """Адреса страниц с отзывами. None: поиск не отработал (нет источника или он не ответил); это не то же самое,
    что «отзывов нет», и результатом не запоминается."""
    s = get_settings()
    brand = brand_of(sup)
    urls: list[str] = []
    answered = False
    for q in (f"{brand} {region} отзывы".replace("  ", " ").strip(), f"{brand} отзывы о компании"):
        try:
            if s.yandex_enabled:
                found = await yandex_search(q, region, 10, client=http)
            elif s.tavily_api_key:
                found = await tavily_search(q, 10, client=http)
            else:
                return None
        except Exception:
            continue
        answered = True
        for r in found:
            u = r.get("url", "")
            if u and u not in urls and is_review_url(u, sup.domain):
                urls.append(u)
        if len(urls) >= MAX_PAGES:
            break
    return urls[: MAX_PAGES + 2] if answered else None


async def collect_pages(sup: Supplier, region: str, http: httpx.AsyncClient) -> dict[str, str] | None:
    """Страницы с отзывами, которые действительно про эту компанию (в тексте есть её название или домен)."""
    urls = await search_review_urls(sup, region, http)
    if urls is None:
        return None
    tokens = brand_tokens(sup)
    ttl = get_settings().page_ttl_days
    got = await asyncio.gather(*(load_page(u, http, ttl) for u in urls), return_exceptions=True)
    pages: dict[str, str] = {}
    for u, g in zip(urls, got):
        if isinstance(g, Exception) or not g or len(g[0]) < 300:
            continue                           # закрыто от ботов, капча или пусто
        text = g[0][:MAX_CHARS]
        low = norm(text)
        if not tokens or not any(t in low for t in tokens):
            continue                           # отзывы, скорее всего, о другой компании
        if region.strip() and _is_branch_page(u) and not region_matches(region, text):
            continue                           # страница филиала в другом городе
        pages[u] = text
        if len(pages) >= MAX_PAGES:
            break
    return pages


def _points(raw, idx: PageIndex) -> list[ReviewPoint]:
    out = []
    for p in (raw or [])[:3]:
        if not isinstance(p, dict):
            continue
        text, quote = str(p.get("text") or "").strip(), str(p.get("quote") or "").strip()
        url = idx.locate(quote, str(p.get("url") or "")) if quote else None
        if text and url:                       # мысль без цитаты, найденной на странице, не принимаем
            out.append(ReviewPoint(text=text, quote=quote, url=url))
    return out


def build_summary(data: dict, pages: dict[str, str]) -> ReviewSummary:
    """Ответ модели -> проверенное резюме: цитаты найдены в тексте, рейтинг подтверждён цитатой."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    idx = PageIndex(pages)
    pos, neg = _points(data.get("positives"), idx), _points(data.get("negatives"), idx)
    rating = None
    r = data.get("rating")
    if isinstance(r, dict) and r.get("value") and r.get("quote"):
        url = idx.locate(str(r["quote"]), str(r.get("url") or ""))
        if url and norm(str(r["value"]).replace("из 5", "").strip(" .")) in norm(str(r["quote"])):
            rating = Fact(value=str(r["value"]).strip(), quote=str(r["quote"]).strip(), url=url)
    summary = str(data.get("summary") or "").strip()
    if not data.get("found") or not summary or not (pos or neg or rating):
        return ReviewSummary(found=False, pages=list(pages), checked_at=now)
    return ReviewSummary(found=True, summary=summary, positives=pos, negatives=neg, rating=rating,
                         pages=list(pages), checked_at=now)


def build_prompt(brand: str, pages: dict[str, str]) -> str:
    blocks = "\n\n".join(f"[PAGE {u}]\n{t}" for u, t in pages.items())
    return f"Компания: «{brand}».\nСтраницы с отзывами:\n\n{blocks}"


async def summarize(sup: Supplier, pages: dict[str, str], client=None) -> ReviewSummary:
    s = get_settings()
    if client is None:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=s.anthropic_api_key)
    msg = await client.messages.create(
        model=s.model_extract, max_tokens=1500, system=SYSTEM, tools=[TOOL],
        tool_choice={"type": "tool", "name": "save_reviews"},
        messages=[{"role": "user", "content": build_prompt(brand_of(sup), pages)}],
    )
    block = next((b for b in msg.content if b.type == "tool_use"), None)
    return build_summary(dict(block.input), pages) if block else ReviewSummary(found=False, pages=list(pages))


def is_fresh(r: ReviewSummary | None) -> bool:
    if not r or not r.checked_at:
        return False
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(r.checked_at) < timedelta(days=FRESH_DAYS)
    except ValueError:
        return False


async def attach_reviews(sup: Supplier, region: str = "", force: bool = False, http=None, client=None) -> bool:
    """Найти отзывы и положить резюме в sup.reviews. True, если что-то обновили. Никогда не бросает исключений:
    отзывы дополняют карточку, но не должны её ронять."""
    s = get_settings()
    if not force and is_fresh(sup.reviews):
        return False
    if not s.anthropic_api_key and client is None:
        return False
    own = http is None
    http = http or httpx.AsyncClient(timeout=20, follow_redirects=True, verify=False)
    try:
        pages = await collect_pages(sup, region, http)
        if pages is None:
            return False                       # поиск не ответил: «не нашли» не записываем
        if not pages:
            sup.reviews = ReviewSummary(found=False, checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        else:
            sup.reviews = await summarize(sup, pages, client=client)
        return True
    except Exception:
        return False
    finally:
        if own:
            await http.aclose()
