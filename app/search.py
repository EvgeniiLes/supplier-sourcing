"""Формулировки запроса и поиск кандидатов: Яндекс (если есть ключ) и Tavily."""
import asyncio
import base64
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from .config import get_settings

TAVILY_URL = "https://api.tavily.com/search"
YANDEX_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"

# Агрегаторы, маркетплейсы, справочники, реестры компаний, соцсети: это не сайты самих поставщиков.
BLACKLIST = {
    "avito.ru", "ozon.ru", "wildberries.ru", "market.yandex.ru", "yandex.ru", "ya.ru",
    "2gis.ru", "zoon.ru", "yell.ru", "flamp.ru", "tiu.ru", "pulscen.ru", "satom.ru",
    "b2b-center.ru", "alibaba.com", "aliexpress.com", "1688.com", "made-in-china.com",
    "hh.ru", "wikipedia.org", "youtube.com", "vk.com", "vk.ru", "ok.ru", "t.me", "dzen.ru",
    "instagram.com", "facebook.com", "rutube.ru", "otzovik.com", "irecommend.ru",
    "rusprofile.ru", "list-org.com", "sbis.ru", "checko.ru", "e-katalog.ru", "prom.ua",
    "vsegda-pomogut.ru", "supl.biz", "cataloxy.ru", "orgpage.ru", "all-biz.info",
    "metro-cc.ru", "auchan.ru", "lenta.com", "perekrestok.ru", "5ka.ru", "magnit.ru",
    "vkusvill.ru", "sbermarket.ru", "samokat.ru", "kuper.ru", "lavka.yandex.ru",
    "gosuslugi.ru", "consultant.ru", "garant.ru", "pinterest.com", "linkedin.com",
    # реестры и базы организаций, каталоги-справочники, вакансии, зарубежные базы
    "spark-interfax.ru", "kontur.ru", "zachestnyibiznes.ru", "audit-it.ru", "jsprav.ru", "mecon.ru",
    "cbinsights.com", "superjob.ru", "rabota.ru", "youla.ru", "tinkoff.ru", "rbc.ru", "vc.ru", "habr.com",
    "pikabu.ru", "yandex.com", "google.com", "bing.com", "maps.yandex.ru", "ru.wikipedia.org",
}


# региональные и общие зоны, под которыми живут независимые компании: eastfood.spb.ru != spb.ru
SHARED_SUFFIXES = {"spb.ru", "msk.ru", "nnov.ru", "nov.ru", "com.ru", "org.ru", "net.ru", "pp.ru",
                   "ru.com", "com.ua", "co.uk", "com.tr", "com.cn",
                   # конструкторы сайтов и хостинги: у каждой компании свой поддомен, корень общий
                   "tilda.ws", "ucoz.ru", "ucoz.com", "ucoz.net", "narod.ru", "wixsite.com", "github.io", "blogspot.com",
                   "business.site", "weebly.com", "jimdofree.com", "nethouse.ru", "shop-pro.ru", "bitrix24.ru",
                   "bitrix24.site", "taplink.ws", "mystrikingly.com", "sitey.me", "site123.me", "wordpress.com"}

# Числовые коды регионов Яндекса (lr) для крупных городов; для остальных регион задаётся только текстом запроса
YANDEX_REGIONS = {
    "москва": "213", "санкт-петербург": "2", "петербург": "2", "спб": "2", "екатеринбург": "54", "новосибирск": "65",
    "казань": "43", "нижний новгород": "47", "самара": "51", "челябинск": "56", "омск": "66", "ростов-на-дону": "39",
    "ростов": "39", "уфа": "172", "красноярск": "62", "пермь": "50", "воронеж": "193", "волгоград": "38",
    "краснодар": "35", "саратов": "194", "тюмень": "55",
}


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
    """Список кандидатов плюс сведения для честной строки статуса: сколько сайтов отсеяно как зарубежные,
    из каких источников пришла выдача и какие источники сломались."""
    skipped_foreign = 0
    sources_used: list[str] = []
    source_errors: dict[str, str] = {}

    def __init__(self, items=()):
        super().__init__(items)
        self.sources_used, self.source_errors = [], {}


# --- формулировки -----------------------------------------------------------------

def literal_query(category: str, region: str) -> str:
    """То, что человек написал бы в поиске сам: «товар город». Идёт первым: так выдача ближе всего к Яндексу."""
    return f"{category} {region}".strip()


def fallback_queries(category: str, region: str) -> list[str]:
    return [
        f"{category} оптом поставщик {region}".strip(),
        f"{category} производитель {region}".strip(),
        f"{category} для ресторанов и кафе оптом {region}".strip(),
    ]


HORECA_RE = re.compile(r"хорек|horeca|horeka|ho\.re\.ca", re.I)


def horeca_query(category: str, region: str) -> str:
    """«<товар> хорека <город>»: добавляем сегмент покупателя к любому запросу, чтобы выдача была про поставщиков
    для общепита, а не про розницу. Если человек уже написал «хорека», запрос остаётся как есть."""
    if HORECA_RE.search(category):
        return literal_query(category, region)
    return " ".join(f"{category} хорека {region}".split())


def build_queries(category: str, region: str, expansions: list[str]) -> list[str]:
    """Запросы по порядку важности: «товар хорека город», буквальный «товар город», затем формулировки модели; без повторов."""
    out: list[str] = []
    for q in [horeca_query(category, region), literal_query(category, region), *expansions]:
        q = " ".join(q.split())
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q)
    return out[:5]


async def expand_queries(category: str, region: str, client=None) -> list[str]:
    """Формулировки модели (без буквального запроса): другие углы того же товара. Без ключа или при ошибке: шаблоны."""
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
                "Составь 3 разных поисковых запроса для Яндекса, чтобы найти сайты ПОСТАВЩИКОВ, ПРОИЗВОДИТЕЛЕЙ и "
                "ДИСТРИБЬЮТОРОВ для ресторанов, кафе и доставки еды.\n"
                f"Что ищет человек: {category}\nГород или регион: {region or 'не указан'}\n"
                "Правила: не меняй и не сужай предмет поиска (не подставляй другие товары). Если это общая категория "
                "или тип клиентов (например «хорека», «для ресторанов»), ищи оптовых поставщиков и дистрибьюторов "
                "для этого сегмента. Для конкретного товара добавляй слова «оптом», «производитель», «поставщик», "
                "«прайс». Город пиши в каждом запросе. Ответь только JSON-массивом из 3 строк."
            )}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        found = re.search(r"\[.*\]", text, re.S)
        queries = [q.strip() for q in json.loads(found.group(0)) if isinstance(q, str) and q.strip()]
        return queries[:3] or base
    except Exception:
        return base


# --- источники ----------------------------------------------------------------------

def yandex_region_id(region: str) -> str:
    """Код региона Яндекса по тексту («г. Екатеринбург» -> 54) или пустая строка, если город не из списка."""
    text = " ".join(re.findall(r"[а-яё\-]+", region.lower().replace("ё", "е")))
    for name in sorted(YANDEX_REGIONS, key=len, reverse=True):
        if name in text:
            return YANDEX_REGIONS[name]
    return ""


def parse_yandex_response(payload: dict) -> list[dict]:
    """Ответ Yandex Search API: JSON с rawData (XML, обычно в base64) -> [{url, title}]."""
    raw = payload.get("rawData") or ""
    try:
        xml = base64.b64decode(raw, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        xml = raw                                           # не base64: считаем, что это уже XML
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise RuntimeError(f"Яндекс вернул не XML: {e}")
    error = root.find(".//error")
    if error is not None:
        raise RuntimeError(f"Яндекс: {''.join(error.itertext()).strip() or error.get('code', 'ошибка')}")
    out = []
    for doc in root.iter("doc"):
        url = (doc.findtext("url") or "").strip()
        if url:
            title = "".join(doc.find("title").itertext()).strip() if doc.find("title") is not None else ""
            out.append({"url": url, "title": title})
    return out


async def yandex_search(query: str, region: str = "", max_results: int = 10, client: httpx.AsyncClient | None = None) -> list[dict]:
    s = get_settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    body = {"query": {"searchType": "SEARCH_TYPE_RU", "queryText": query[:400]},
            "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": str(max_results), "docsInGroup": "1"},
            "responseFormat": "FORMAT_XML", "folderId": s.yandex_folder_id}
    rid = yandex_region_id(region)
    if rid:
        body["region"] = rid
    try:
        r = await client.post(YANDEX_URL, headers={"Authorization": f"Api-Key {s.yandex_api_key}"}, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"Яндекс ответил HTTP {r.status_code}: {r.text[:160]}")
        return parse_yandex_response(r.json())
    finally:
        if own:
            await client.aclose()


async def tavily_search(query: str, max_results: int = 10, client: httpx.AsyncClient | None = None) -> list[dict]:
    s = get_settings()
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {s.tavily_api_key}"},
            # country: российская выдача выше; без него в ответ лезут зарубежные базы компаний
            json={"query": query, "max_results": max_results, "search_depth": "basic", "country": "russia"},
        )
        r.raise_for_status()
        return r.json().get("results", [])
    finally:
        if own:
            await client.aclose()


async def find_candidates(queries: list[str], limit: int, client: httpx.AsyncClient | None = None,
                          region: str = "") -> list[dict]:
    """Выдачи всех включённых источников по всем запросам -> кандидаты: без агрегаторов, зарубежных и дублей.
    Яндекс идёт первым: при слиянии по кругу его результаты приоритетнее."""
    s = get_settings()
    jobs: list[tuple[str, asyncio.Future]] = []
    for source in s.sources:
        for q in queries:
            call = yandex_search(q, region, client=client) if source == "yandex" else tavily_search(q, client=client)
            jobs.append((source, call))
    results = await asyncio.gather(*(c for _, c in jobs), return_exceptions=True)

    out = CandidateList()
    batches: list[list[dict]] = []
    for (source, _), res in zip(jobs, results):
        if isinstance(res, Exception):
            out.source_errors.setdefault(source, str(res) or res.__class__.__name__)
            continue
        batches.append(res)
        if source not in out.sources_used:
            out.sources_used.append(source)
    if not batches:
        errors = "; ".join(f"{k}: {v}" for k, v in out.source_errors.items())
        raise RuntimeError(f"Поисковые источники не ответили ({errors or 'нет ключей'}). Проверьте ключи и лимиты")

    seen: dict[str, dict] = {}
    foreign: set[str] = set()
    # по кругу берём из каждой выдачи, чтобы не перекосить в пользу одного запроса или источника
    for rank in range(max((len(b) for b in batches), default=0)):
        for batch in batches:
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
    out.extend(list(seen.values())[:limit])
    out.skipped_foreign = len(foreign)
    return out
