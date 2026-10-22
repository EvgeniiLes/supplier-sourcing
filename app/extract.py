"""Извлечение данных о поставщике из текстов страниц (Claude Haiku, строгая схема)."""
import hashlib
import re

from .config import get_settings
from .models import DeliveryCheck, Fact, Supplier
from .score import region_matches, region_stems
from .verify import PageIndex, norm, query_stems, regex_contacts, verify_price_items, verify_supplier

FACT = {
    "type": "object",
    "properties": {
        "value": {"type": "string", "description": "Значение коротко, своими словами или как на сайте"},
        "quote": {"type": "string", "description": "ДОСЛОВНАЯ цитата со страницы, до 200 символов, без изменений"},
        "url": {"type": "string", "description": "Адрес страницы из заголовка [PAGE ...], где найдена цитата"},
    },
    "required": ["value", "quote", "url"],
}

def _nullable(desc: str) -> dict:
    return {"anyOf": [FACT, {"type": "null"}], "description": desc}


TOOL = {
    "name": "save_supplier",
    "description": "Сохранить данные о поставщике, найденные ТОЛЬКО в тексте страниц.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_supplier": {"type": "boolean", "description": (
                "true только если сайт принадлежит производителю, оптовику или дистрибьютору ПРОДУКТОВ ПИТАНИЯ, "
                "ингредиентов, напитков, упаковки или одноразовой посуды, который продаёт бизнесу. "
                "false: розничный магазин, СМИ, блог, каталог, справочник, а также продавцы оборудования, мебели, "
                "кухонной техники и услуг (даже если сайт про HoReCa).")},
            "name": _nullable("Название компании"),
            "offering": _nullable("Что поставляют, коротко"),
            "address": _nullable("Адрес или город расположения"),
            "regions": {"type": "array", "items": FACT, "description": "Регионы и города, куда явно заявлена работа или доставка"},
            "nationwide": _nullable("Заявлена работа или доставка по всей России"),
            "phones": {"type": "array", "items": FACT},
            "emails": {"type": "array", "items": FACT},
            "min_order": _nullable("Минимальный объём или сумма заказа"),
            "min_order_kg": {"type": ["number", "null"], "description": (
                "Минимальный заказ в килограммах, если в min_order указан вес (1 т = 1000). "
                "Иначе null. Не считай по ценам и не угадывай.")},
            "price_info": _nullable(
                "ОБЩИЕ условия по ценам, без конкретных товаров: опубликован ли открытый прайс (в т.ч. PDF), "
                "цены только по запросу или после регистрации, скидки и оптовые уровни"),
            "query_prices": {"type": "array", "description": (
                "Цены на ТОВАР, КОТОРЫЙ ИЩЕТ ПОЛЬЗОВАТЕЛЬ (указан в начале сообщения), и его разновидности/фасовки. "
                "Другие товары не включай, даже если цены на них есть. Нет цен на искомый товар: пустой список."),
                "items": {"type": "object", "properties": {
                    "name": {"type": "string", "description": "Название позиции как на сайте, с фасовкой"},
                    "matches_query": {"type": "boolean", "description": (
                        "true, только если позиция и есть искомый товар или его разновидность/фасовка "
                        "(для «упаковка»: контейнеры, пакеты, лотки; для «сливки»: сливки). Иначе false")},
                    "unit": {"type": ["string", "null"], "description": (
                        "Единица ТОЛЬКО если она написана рядом с ценой: «₽/кг», «за шт», «руб/л». "
                        "Вес товара из названия единицей цены НЕ является: если цена просто «от 138 ₽» за позицию "
                        "весом 100 г, unit = null")},
                    "price": {"type": "number", "description": "Цена числом, как написано, без пересчётов"},
                    "pack": {"type": ["string", "null"], "description": "Фасовка как написана: «25 кг», «1 л», «12 шт в упаковке»"},
                    "quote": {"type": "string", "description": "ДОСЛОВНАЯ строка страницы с позицией и ценой"},
                    "url": {"type": "string", "description": "Адрес страницы из заголовка [PAGE ...]"},
                }, "required": ["name", "matches_query", "price", "quote", "url"]}},
            "certificates": {"type": "array", "items": FACT, "description": "Сертификаты, декларации, ГОСТ/ТУ, Меркурий, Халяль и т.п."},
            "delivery": _nullable("Условия доставки"),
            "delivery_to_region": {"type": "object", "description": (
                "Возит ли поставщик в РЕГИОН ПОЛЬЗОВАТЕЛЯ (указан в начале сообщения). yes: в тексте прямо сказано, что "
                "доставка или работа охватывает этот регион (город, область, округ) либо всю Россию. no: прямо сказано, что "
                "доставка только в другие города или регионы («только по Москве и МО»), что возможен лишь самовывоз, "
                "или что компания не работает с РФ. Адрес офиса сам по себе не доказывает ни yes, ни no. Иначе unknown."),
                "properties": {
                    "verdict": {"type": "string", "enum": ["yes", "no", "unknown"]},
                    "quote": {"type": "string", "description": "ДОСЛОВНАЯ цитата, подтверждающая yes или no"},
                    "url": {"type": "string", "description": "Адрес страницы из заголовка [PAGE ...]"},
                }, "required": ["verdict"]},
            "presence_in_region": {"type": "object", "description": (
                "Есть ли у поставщика ОФИС, СКЛАД, ПРОИЗВОДСТВО, ФИЛИАЛ или ТОЧКА В САМОМ ГОРОДЕ пользователя. yes: в тексте "
                "указан АДРЕС (улица, дом, склад, офис) именно в этом городе (один из нескольких адресов тоже считается). "
                "НЕ считается: телефон с городским кодом, фраза «доставляем в…», адрес в области или соседнем городе "
                "(Мытищи, Одинцово не Москва), доставка. Иначе unknown."),
                "properties": {
                    "verdict": {"type": "string", "enum": ["yes", "unknown"]},
                    "quote": {"type": "string", "description": "ДОСЛОВНАЯ цитата с адресом или названием филиала в этом городе"},
                    "url": {"type": "string", "description": "Адрес страницы из заголовка [PAGE ...]"},
                }, "required": ["verdict"]},
            "wholesale": _nullable("Работа с юрлицами или оптом"),
        },
        "required": ["is_supplier"],
    },
}

SYSTEM = (
    "Ты извлекаешь данные о поставщике продуктов питания из текста страниц его сайта. "
    "Правила: используй только текст ниже; ничего не домысливай и не вспоминай из общих знаний. "
    "Если данных нет — null или пустой список. К каждому факту приложи дословную цитату "
    "(скопируй фрагмент текста без правок) и url страницы из заголовка [PAGE ...]. "
    "Цитата должна прямо подтверждать значение: в value не пиши ничего, чего нет в цитате "
    "(города, цифры, названия). "
    "Минимальный заказ — только если сайт называет сумму или объём именно минимальным заказом/партией. "
    "Порог бесплатной доставки, 'доставка от N руб.' и примеры цен минимальным заказом не являются: "
    "их записывай в delivery. Если точной цифры нет — min_order это null. "
    "Цены: конкретные цены клади только в query_prices и только на товар из запроса пользователя "
    "(для «сыр моцарелла» это моцарелла любой фасовки, но не другие сыры и не напитки; для общей категории вроде "
    "«упаковка для доставки еды» это контейнеры, пакеты, лотки и т.п.). Для каждой позиции честно ставь matches_query. "
    "Единицу цены указывай только если она написана рядом с ценой, а не выводи из веса товара. "
    "price_info — лишь общие условия по ценам, без примеров товаров. "
    "Доставка и присутствие в регионе: если указан регион пользователя, заполни delivery_to_region и presence_in_region. "
    "Это разные вопросы: адрес офиса в городе даёт presence=yes, но не доказывает доставку; доставка в город не доказывает "
    "наличие офиса. Честный unknown лучше выдуманного yes или no. "
    "Вызови инструмент save_supplier."
)


def allocate(lengths: list[int], budget: int) -> list[int]:
    """Справедливый раздел бюджета: короткие страницы входят целиком, остаток делят длинные.
    Так длинная главная не вытесняет страницы «Контакты» и «Доставка»."""
    caps = [0] * len(lengths)
    remaining = budget
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    for k, i in enumerate(order):
        caps[i] = min(lengths[i], remaining // (len(order) - k))
        remaining -= caps[i]
    return caps


def build_prompt(pages: dict[str, str], html_budget: int = 24_000, pdf_budget: int = 14_000) -> str:
    """Тексты страниц для модели. У PDF-прайсов отдельный бюджет: длинные HTML-страницы их не вытесняют."""
    def pack(items: list[tuple[str, str]], budget: int) -> list[str]:
        caps = allocate([len(t) for _, t in items], budget)
        return [f"[PAGE {url}]\n{text[:cap]}\n" for (url, text), cap in zip(items, caps) if cap > 0]

    is_pdf = lambda u: u.split("?")[0].lower().endswith(".pdf")   # noqa: E731
    html = [(u, t) for u, t in pages.items() if not is_pdf(u)]
    pdfs = [(u, t) for u, t in pages.items() if is_pdf(u)]
    return "\n".join(pack(html, html_budget) + pack(pdfs, pdf_budget))


# Версия правил извлечения. Поднимайте при смене схемы, промпта или проверок: иначе кэш отдаст результат старых правил.
EXTRACT_VERSION = 7


def content_hash(pages: dict[str, str], query: str = "", region: str = "") -> str:
    """Ключ кэша поставщика: страницы, запрос и регион (от них зависят цены и вердикт по доставке)
    и версия правил извлечения."""
    h = hashlib.sha256()
    for u in sorted(pages):
        h.update(u.encode()); h.update(pages[u].encode())
    h.update(b"\x00q:" + " ".join(sorted(query_stems(query))).encode())
    h.update(b"\x00r:" + " ".join(sorted(region_stems(region))).encode())
    h.update(f"\x00v{EXTRACT_VERSION}".encode())
    return h.hexdigest()[:16]


def _fact(d) -> Fact | None:
    if not isinstance(d, dict) or not d.get("value"):
        return None
    return Fact(value=str(d["value"]), quote=str(d.get("quote", "")), url=str(d.get("url", "")))


def _facts(items) -> list[Fact]:
    return [f for f in (_fact(i) for i in (items or [])) if f]


def parse_tool_input(domain: str, url: str, data: dict) -> Supplier | None:
    if not data.get("is_supplier"):
        return None
    mo = data.get("min_order_kg")
    return Supplier(
        domain=domain, url=url,
        name=_fact(data.get("name")), offering=_fact(data.get("offering")),
        address=_fact(data.get("address")), regions=_facts(data.get("regions")),
        nationwide=_fact(data.get("nationwide")),
        phones=_facts(data.get("phones")), emails=_facts(data.get("emails")),
        min_order=_fact(data.get("min_order")),
        min_order_kg=float(mo) if isinstance(mo, (int, float)) and mo > 0 else None,
        price_info=_fact(data.get("price_info")), certificates=_facts(data.get("certificates")),
        delivery=_fact(data.get("delivery")), wholesale=_fact(data.get("wholesale")),
    )


NATIONWIDE_RE = re.compile(r"росси|\bрф\b|во\s+все\s+регион|все\s+регион|любой\s+регион|любой\s+город|по\s+всей\s+стране|"
                           r"всем\s+област|до\s+владивостока|\bсн?г\b")
ADDRESS_RE = re.compile(r"\bул\b|улиц|пр-т|просп|пр-д|проезд|шоссе|\bнаб\b|набереж|\bпер\b|переул|бульвар|\bб-р\b|тракт|"
                        r"\bд\.\s*\d|дом\s*\d|\bстр\b|\bлит\b|корп|офис|склад|производств|\b\d{6}\b|километр|\bкм\b|торговый\s+центр|\bтц\b")
OBLAST_RE = re.compile(r"област|\bобл\b")


def _evidence_ok(kind: str, verdict: str, quote: str, region: str) -> bool:
    """Вердикт «да» принимаем, только если цитата говорит именно об этом, а не просто лежит где-то в тексте.
    Доставка: цитата называет регион либо «по России/всем регионам». Присутствие: это адрес в самом городе
    (не телефон, не «доставляем в…» и не адрес в области, если регион задан городом)."""
    if verdict != "yes":
        return True
    q = norm(quote)
    if kind == "delivery":
        return region_matches(region, q) or bool(NATIONWIDE_RE.search(q))
    if not region_matches(region, q) or not ADDRESS_RE.search(q):
        return False
    return not OBLAST_RE.search(q) or bool(OBLAST_RE.search(norm(region)))


def verify_delivery_check(raw, pages: dict[str, str], region: str, allowed: tuple = ("yes", "no"),
                          kind: str = "delivery") -> DeliveryCheck:
    """Вердикт модели -> проверенный. yes/no принимаются только с цитатой, найденной в тексте и по существу
    подтверждающей вердикт; иначе unknown. allowed: допустимые вердикты (для присутствия «no» не бывает:
    отсутствие адреса на сайте ничего не доказывает). kind: delivery | presence."""
    raw = raw if isinstance(raw, dict) else {}
    verdict = raw.get("verdict") if raw.get("verdict") in allowed else "unknown"
    quote = str(raw.get("quote") or "").strip()
    url = PageIndex(pages).locate(quote, str(raw.get("url") or "")) if quote else None
    if verdict != "unknown" and (url is None or not _evidence_ok(kind, verdict, quote, region)):
        return DeliveryCheck(region=region, verdict="unknown")        # утверждение без доказательства не принимаем
    return DeliveryCheck(region=region, verdict=verdict, quote=quote if url else "", url=url or "")


async def extract_supplier(domain: str, url: str, pages: dict[str, str], client=None, query: str = "",
                           region: str = "") -> Supplier | None:
    """Страницы -> Supplier с проверенными фактами. None, если сайт не поставщик.
    query — что ищет пользователь (по нему извлекаются цены), region — его регион (для вердикта по доставке)."""
    if not pages:
        return None
    s = get_settings()
    if client is None:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=s.anthropic_api_key)
    head = (f"Пользователь ищет: «{query.strip()}».\n" if query.strip() else "") + \
           (f"Регион пользователя: «{region.strip()}».\n" if region.strip() else "")
    head = head + "\n" if head else ""
    msg = await client.messages.create(
        model=s.model_extract,
        max_tokens=4000,
        system=SYSTEM,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "save_supplier"},
        messages=[{"role": "user", "content": head + build_prompt(pages)}],
    )
    block = next((b for b in msg.content if b.type == "tool_use"), None)
    if block is None:
        return None
    data = dict(block.input)
    supplier = parse_tool_input(domain, url, data)
    if supplier is None:
        return None
    supplier = verify_supplier(supplier, pages)
    if query.strip():                       # цены на искомый товар: каждая проверена по тексту и относится к запросу
        supplier.query_prices, dropped = verify_price_items(data.get("query_prices"), pages, query)
        supplier.query_for = query.strip()
        supplier.rejected_facts += dropped
    if region.strip():                      # доставка в регион: вердикт с цитатой, проверенной по тексту
        supplier.delivery_check = verify_delivery_check(data.get("delivery_to_region"), pages, region.strip())
        supplier.presence_check = verify_delivery_check(data.get("presence_in_region"), pages, region.strip(),
                                                        allowed=("yes",), kind="presence")
    if not supplier.phones or not supplier.emails:
        phones, emails = regex_contacts(pages)
        supplier.phones = supplier.phones or phones
        supplier.emails = supplier.emails or emails
    return supplier
