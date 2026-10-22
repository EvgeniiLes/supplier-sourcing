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


class FlipFlopClient(FakeClient):
    """Первый ответ «не поставщик», второй «поставщик»: так модель ведёт себя на пограничных сайтах."""

    def __init__(self):
        super().__init__(None)
        self.calls = 0

    async def _create(self, **kwargs):
        self.calls += 1
        payload = {"is_supplier": self.calls > 1, "name": {"value": "Кофчег", "quote": "Кофчег", "url": URL}}
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=payload)])


def test_shop_that_calls_itself_wholesale_supplier_is_asked_twice():
    page = "Кофчег: интернет-магазин чая и кофе. Оптовый поставщик кофе и чая в HoReCa. Оптовые поставки от 5000 руб."
    client = FlipFlopClient()
    s = asyncio.run(extract_supplier("t.example.ru", URL, {URL: page}, client=client))
    assert s is not None and client.calls == 2                                  # не теряем поставщика из-за одного «нет»


def test_retail_site_without_wholesale_hints_is_not_asked_again():
    client = FlipFlopClient()
    s = asyncio.run(extract_supplier("t.example.ru", URL, {URL: "Магазин чая. Купить чай в розницу. Телефон +7 (812) 123-45-67."},
                                     client=client))
    assert s is None and client.calls == 1                                      # лишних вызовов (и расходов) нет


def test_persistent_no_gets_at_most_three_calls():
    class AlwaysNo(FlipFlopClient):
        async def _create(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"is_supplier": False})])

    client = AlwaysNo()
    assert asyncio.run(extract_supplier("t.example.ru", URL, {URL: "Оптовые поставки оборудования для HoReCa."}, client=client)) is None
    assert client.calls == 3                                                    # первый ответ и не больше двух проверок
