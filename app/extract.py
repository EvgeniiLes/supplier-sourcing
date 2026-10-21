"""Извлечение данных о поставщике из текстов страниц (Claude Haiku, строгая схема)."""
import hashlib

from .config import get_settings
from .models import Fact, Supplier
from .verify import regex_contacts, verify_supplier

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
                "Опубликованные цены: 2–3 конкретные позиции с ценой из прайса или таблицы (название, цена за единицу) "
                "и пометка, если на сайте есть полный прайс (например PDF). Цитата — дословная строка прайса с ценой. "
                "Оговорки вроде «цены по запросу» не подходят, если в тексте есть таблица с ценами"),
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
    "их записывай в delivery или price_info. Если точной цифры нет — min_order это null. "
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


def content_hash(pages: dict[str, str]) -> str:
    h = hashlib.sha256()
    for u in sorted(pages):
        h.update(u.encode()); h.update(pages[u].encode())
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


async def extract_supplier(domain: str, url: str, pages: dict[str, str], client=None) -> Supplier | None:
    """Страницы -> Supplier с проверенными фактами. None, если сайт не поставщик."""
    if not pages:
        return None
    s = get_settings()
    if client is None:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=s.anthropic_api_key)
    msg = await client.messages.create(
        model=s.model_extract,
        max_tokens=3000,
        system=SYSTEM,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "save_supplier"},
        messages=[{"role": "user", "content": build_prompt(pages)}],
    )
    block = next((b for b in msg.content if b.type == "tool_use"), None)
    if block is None:
        return None
    supplier = parse_tool_input(domain, url, dict(block.input))
    if supplier is None:
        return None
    supplier = verify_supplier(supplier, pages)
    if not supplier.phones or not supplier.emails:
        phones, emails = regex_contacts(pages)
        supplier.phones = supplier.phones or phones
        supplier.emails = supplier.emails or emails
    return supplier
