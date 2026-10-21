from app.models import Fact, Supplier
from app.verify import dedupe, normalize_phone, quote_in_text, norm, regex_contacts, verify_supplier

# Тестовый текст страницы — синтетический, используется только в тестах.
PAGE = """ООО «Тест-Молоко». ИНН 7701234567
Адрес: г. Санкт-Петербург, ул. Примерная, 1
Телефон: +7 (812) 123-45-67
Email: sales@test-moloko.example.ru
Минимальный заказ от 20 кг. Доставка по Санкт-Петербургу бесплатно."""
URL = "https://test-moloko.example.ru/"


def test_quote_matching_ignores_case_spaces_and_yo():
    assert quote_in_text("минимальный  ЗАКАЗ от 20 кг", norm(PAGE))
    assert not quote_in_text("минимальный заказ от 5 кг", norm(PAGE))
    assert not quote_in_text("ab", norm(PAGE))


def test_phone_normalization():
    assert normalize_phone("8 812 123 45 67") == "+7 (812) 123-45-67"
    assert normalize_phone("+7(921)000-11-22") == "+7 (921) 000-11-22"
    assert normalize_phone("123456") is None
    assert normalize_phone("+7 (112) 123-45-67") is None


def test_verify_drops_invented_facts():
    s = Supplier(
        domain="test-moloko.example.ru", url=URL,
        min_order=Fact(value="от 20 кг", quote="Минимальный заказ от 20 кг", url=URL),
        price_info=Fact(value="от 300 руб/кг", quote="цена от 300 руб за кг", url=URL),  # выдумка
        phones=[Fact(value="+7 (812) 123-45-67", quote="+7 (812) 123-45-67", url=URL),
                Fact(value="+7 (999) 000-00-00", quote="+7 (999) 000-00-00", url=URL)],  # выдумка
        emails=[Fact(value="sales@test-moloko.example.ru", quote="sales@test-moloko.example.ru", url=URL),
                Fact(value="fake@nowhere.ru", quote="fake@nowhere.ru", url=URL)],
        min_order_kg=20,
    )
    v = verify_supplier(s, {URL: PAGE})
    assert v.min_order and v.price_info is None
    assert [p.value for p in v.phones] == ["+7 (812) 123-45-67"]
    assert [e.value for e in v.emails] == ["sales@test-moloko.example.ru"]
    assert v.inn and v.inn.value == "7701234567"
    assert v.rejected_facts == 3


def test_verify_resets_min_kg_without_min_order():
    s = Supplier(domain="a.ru", url=URL, min_order=Fact(value="x", quote="нет такой цитаты", url=URL), min_order_kg=5)
    assert verify_supplier(s, {URL: PAGE}).min_order_kg is None


def test_regex_contacts_fallback():
    phones, emails = regex_contacts({URL: PAGE})
    assert phones[0].value == "+7 (812) 123-45-67"
    assert emails[0].value == "sales@test-moloko.example.ru"


def test_dedupe_merges_by_phone_and_inn():
    a = Supplier(domain="a.ru", url="https://a.ru", phones=[Fact(value="+7 (812) 123-45-67")], name=Fact(value="A"))
    b = Supplier(domain="b.ru", url="https://b.ru", phones=[Fact(value="+7 (812) 123-45-67")])
    c = Supplier(domain="c.ru", url="https://c.ru", inn=Fact(value="7701234567"))
    d = Supplier(domain="d.ru", url="https://d.ru", inn=Fact(value="7701234567"))
    e = Supplier(domain="e.ru", url="https://e.ru")
    out = dedupe([a, b, c, d, e])
    assert len(out) == 3
    merged = next(s for s in out if s.domain == "a.ru")
    assert merged.also_domains == ["b.ru"]
