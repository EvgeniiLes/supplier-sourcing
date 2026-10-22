"""Расширение запроса и поиск кандидатов через Tavily."""
import asyncio
import json
import re
from urllib.parse import urlparse

import httpx

from .config import get_settings

TAVILY_URL = "https://api.tavily.com/search"

# Агрегаторы, маркетплейсы, справочники, соцсети: это не сайты самих поставщиков.
BLACKLIST = {
    "avito.ru", "ozon.ru", "wildberries.ru", "market.yandex.ru", "yandex.ru", "ya.ru",
    "2gis.ru", "zoon.ru", "yell.ru", "flamp.ru", "tiu.ru", "pulscen.ru", "satom.ru",
    "b2b-center.ru", "alibaba.com", "aliexpress.com", "1688.com", "made-in-china.com",
    "hh.ru", "wikipedia.org", "youtube.com", "vk.com", "ok.ru", "t.me", "dzen.ru",
    "instagram.com", "facebook.com", "rutube.ru", "otzovik.com", "irecommend.ru",
    "rusprofile.ru", "list-org.com", "sbis.ru", "checko.ru", "e-katalog.ru", "prom.ua",
    "vsegda-pomogut.ru", "supl.biz", "cataloxy.ru", "orgpage.ru", "all-biz.info",
    "metro-cc.ru", "auchan.ru", "lenta.com", "perekrestok.ru", "5ka.ru", "magnit.ru",
    "vkusvill.ru", "sbermarket.ru", "samokat.ru", "kuper.ru", "lavka.yandex.ru",
    "gosuslugi.ru", "consultant.ru", "garant.ru", "pinterest.com", "linkedin.com",
}


# региональные и общие зоны, под которыми живут независимые компании: eastfood.spb.ru != spb.ru
SHARED_SUFFIXES = {"spb.ru", "msk.ru", "nnov.ru", "nov.ru", "com.ru", "org.ru", "net.ru", "pp.ru",
                   "ru.com", "com.ua", "co.uk", "com.tr", "com.cn",
                   # конструкторы сайтов и хостинги: у каждой компании свой поддомен, корень общий
                   "tilda.ws", "ucoz.ru", "ucoz.com", "ucoz.net", "narod.ru", "wixsite.com", "github.io", "blogspot.com",
                   "business.site", "weebly.com", "jimdofree.com", "nethouse.ru", "shop-pro.ru", "bitrix24.ru",
                   "bitrix24.site", "taplink.ws", "mystrikingly.com", "sitey.me", "site123.me", "wordpress.com"}


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) > 2:
        keep = 3 if ".".join(parts[-2:]) in SHARED_SUFFIXES else 2
        return ".".join(parts[-keep:])
    return host


def is_blacklisted(url: str) -> bool:
    d = domain_of(url)
    return not d or d in BLACKLIST


def is_foreign_domain(url_or_domain: str) -> bool:
    """Домен в зоне, которую мы не показываем (по умолчанию .ua): такие сайты не доставляют в РФ."""
    host = (urlparse(url_or_domain if "//" in url_or_domain else "//" + url_or_domain).hostname or "").lower()
    return host.rsplit(".", 1)[-1] in get_settings().excluded_tlds


class CandidateList(list):
    """Список кандидатов и сколько сайтов отсеяно как зарубежные: для честной строки статуса."""
    skipped_foreign = 0


def fallback_queries(category: str, region: str) -> list[str]:
    return [
        f"{category} оптом поставщик {region}",
        f"{category} производитель {region}",
        f"купить {category} оптом для ресторанов и кафе {region}",
        f"{category} прайс-лист опт условия доставки {region}",
    ]


async def expand_queries(category: str, region: str, client=None) -> list[str]:
    """LLM придумывает поисковые формулировки. Без ключа или при ошибке — шаблоны."""
    s = get_settings()
    base = fallback_queries(category, region)
    if not s.anthropic_api_key:
        return base
    try:
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=s.anthropic_api_key)
        msg = await client.messages.create(
            model=s.model_extract,
            max_tokens=400,
            messages=[{"role": "user", "content": (
                "Составь 4 разных поисковых запроса для Яндекса/Google, чтобы найти сайты "
                "ПОСТАВЩИКОВ и ПРОИЗВОДИТЕЛЕЙ для ресторанов и доставки еды.\n"
                f"Товар: {category}\nРегион: {region}\n"
                "Используй синонимы товара, слова «оптом», «производитель», «поставщик». "
                "Ответь только JSON-массивом строк."
            )}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        found = re.search(r"\[.*\]", text, re.S)
        queries = [q.strip() for q in json.loads(found.group(0)) if isinstance(q, str) and q.strip()]
        return (queries[:4] or base)
    except Exception:
        return base


async def tavily_search(query: str, max_results: int = 10, client: httpx.AsyncClient | None = None) -> list[dict]:
    s = get_settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {s.tavily_api_key}"},
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
        )
        r.raise_for_status()
        return r.json().get("results", [])
    finally:
        if own:
            await client.aclose()


async def find_candidates(queries: list[str], limit: int, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Объединяет выдачи запросов, убирает агрегаторы и дубли доменов."""
    batches = await asyncio.gather(*(tavily_search(q, client=client) for q in queries),
                                   return_exceptions=True)
    seen: dict[str, dict] = {}
    foreign: set[str] = set()
    ok = [b for b in batches if not isinstance(b, Exception)]
    if not ok:
        raise RuntimeError("Поисковый API не ответил: проверьте TAVILY_API_KEY и лимиты")
    # по кругу берём из каждой выдачи, чтобы не перекосить в пользу первого запроса
    for rank in range(max((len(b) for b in ok), default=0)):
        for batch in ok:
            if rank >= len(batch):
                continue
            item = batch[rank]
            url = item.get("url", "")
            if is_blacklisted(url):
                continue
            d = domain_of(url)
            if is_foreign_domain(url):
                foreign.add(d)
                continue
            if d not in seen:
                seen[d] = {"domain": d, "url": url, "title": item.get("title", "")}
    out = CandidateList(list(seen.values())[:limit])
    out.skipped_foreign = len(foreign)
    return out
