"""Извлечение данных о поставщике из текстов страниц (Claude Haiku, строгая схема)."""
import hashlib

from .config import get_settings
from .models import Fact, Supplier
from .verify import query_stems, regex_contacts, verify_price_items, verify_supplier

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
                "true только если сайт принадлежит производителю, оптовику или дистрибьютору, "
                "который продаёт бизнесу. false: розничный магазин, СМИ, блог, каталог, справочник.")},
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
EXTRACT_VERSION = 4


def content_hash(pages: dict[str, str], query: str = "") -> str:
    """Ключ кэша поставщика: страницы, запрос (цены зависят от него) и версия правил извлечения."""
    h = hashlib.sha256()
    for u in sorted(pages):
        h.update(u.encode()); h.update(pages[u].encode())
    h.update(b"\x00q:" + " ".join(sorted(query_stems(query))).encode())
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


async def extract_supplier(domain: str, url: str, pages: dict[str, str], client=None, query: str = "") -> Supplier | None:
    """Страницы -> Supplier с проверенными фактами. None, если сайт не поставщик.
    query — что ищет пользователь: по нему извлекаются цены на нужный товар."""
    if not pages:
        return None
    s = get_settings()
    if client is None:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=s.anthropic_api_key)
    head = f"Пользователь ищет: «{query.strip()}».\n\n" if query.strip() else ""
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
    if not supplier.phones or not supplier.emails:
        phones, emails = regex_contacts(pages)
        supplier.phones = supplier.phones or phones
        supplier.emails = supplier.emails or emails
    return supplier
