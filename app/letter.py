"""Письмо-запрос КП: шаблон с вопросами по пустым полям, по желанию — правка Sonnet."""
from .config import get_settings

QUESTIONS = {
    "работаете ли вы с нашим регионом": "Работаете ли вы с городом {region} и какие условия доставки сюда?",
    "контактный телефон": None,
    "email для заявок": None,
    "минимальный объём заказа": "Какой минимальный объём (вес или сумма) первого заказа?",
    "сертификаты/декларации на продукцию": "Можете ли выслать сертификаты или декларации соответствия на продукцию?",
    "условия и стоимость доставки": "Каковы условия и стоимость доставки, сроки и график отгрузок?",
    "прайс-лист / цены": "Пришлите, пожалуйста, актуальный прайс-лист с ценами для юрлиц.",
}


def build_letter(name: str, category: str, region: str, missing: list[str], sender: str = "") -> dict:
    questions = [q.format(region=region or "доставки") for m in missing if (q := QUESTIONS.get(m))]
    questions.append("Условия оплаты и отсрочка для новых клиентов.")
    lines = [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    where = f" Регион поставки: {region}." if region else ""
    body = (
        f"Здравствуйте!\n\n"
        f"Мы работаем в сфере общественного питания и подбираем поставщика: {category}.{where} "
        f"Нашли вашу компанию на сайте и хотим уточнить условия сотрудничества.\n\n"
        f"Просим ответить на вопросы:\n" + "\n".join(lines) +
        f"\n\nЕсли удобнее, пришлите коммерческое предложение на эту почту.\n\n"
        f"С уважением,\n{sender or '[ваше имя, компания, телефон]'}"
    )
    return {"subject": f"Запрос коммерческого предложения: {category}", "body": body}


async def polish_with_llm(letter: dict, client=None) -> dict:
    """Сглаживает текст, не меняя вопросов. При любой ошибке возвращает исходный шаблон."""
    s = get_settings()
    if not s.anthropic_api_key:
        return letter
    try:
        if client is None:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=s.anthropic_api_key)
        msg = await client.messages.create(
            model=s.model_write,
            max_tokens=900,
            messages=[{"role": "user", "content": (
                "Перепиши деловое письмо поставщику: коротко, вежливо, без канцелярита. "
                "Сохрани все вопросы и их порядок, ничего не добавляй от себя (цифры, факты, имена). "
                "Верни только текст письма.\n\n" + letter["body"])}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return {**letter, "body": text} if len(text) > 80 else letter
    except Exception:
        return letter
