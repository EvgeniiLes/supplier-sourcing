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
            "price_info": _nullable("Цены или примерный ценовой диапазон, если опубликованы"),
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


def build_prompt(pages: dict[str, str], budget: int = 30_000) -> str:
    parts, used = [], 0
    for url, text in pages.items():
        chunk = f"[PAGE {url}]\n{text}\n"
        if used + len(chunk) > budget:
            chunk = chunk[: max(0, budget - used)]
        if chunk:
            parts.append(chunk)
            used += len(chunk)
        if used >= budget:
            break
    return "\n".join(parts)


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
