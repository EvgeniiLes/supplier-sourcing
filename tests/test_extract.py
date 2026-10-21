import asyncio
from types import SimpleNamespace

from app.extract import extract_supplier

URL = "https://t.example.ru/"
PAGE = "ООО Тест. Сыры оптом. Телефон +7 (812) 123-45-67. Минимальный заказ от 10 кг."


class FakeClient:
    """Подменяет Anthropic: возвращает заранее заданный вызов инструмента."""

    def __init__(self, payload):
        self.messages = SimpleNamespace(create=self._create)
        self.payload = payload

    async def _create(self, **kwargs):
        assert kwargs["tool_choice"]["name"] == "save_supplier"
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.payload)])


def test_extract_keeps_only_verified():
    payload = {
        "is_supplier": True,
        "name": {"value": "ООО Тест", "quote": "ООО Тест", "url": URL},
        "min_order": {"value": "от 10 кг", "quote": "Минимальный заказ от 10 кг", "url": URL},
        "min_order_kg": 10,
        "price_info": {"value": "дёшево", "quote": "самые низкие цены", "url": URL},
    }
    s = asyncio.run(extract_supplier("t.example.ru", URL, {URL: PAGE}, client=FakeClient(payload)))
    assert s.min_order and s.min_order_kg == 10
    assert s.price_info is None and s.rejected_facts == 1
    assert s.phones[0].value == "+7 (812) 123-45-67"  # подхвачено запасным разбором


def test_extract_non_supplier_returns_none():
    s = asyncio.run(extract_supplier("t.example.ru", URL, {URL: PAGE}, client=FakeClient({"is_supplier": False})))
    assert s is None
