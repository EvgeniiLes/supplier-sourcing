from app.models import Fact, Filters, Supplier
from app.score import region_matches, score_supplier


def F(v):
    return Fact(value=v, quote=v, url="https://x.ru")


def full():
    return Supplier(
        domain="x.ru", url="https://x.ru",
        address=F("Санкт-Петербург, ул. Примерная"), phones=[F("+7 (812) 123-45-67")], emails=[F("a@x.ru")],
        min_order=F("от 20 кг"), min_order_kg=20, certificates=[F("Декларация ТР ТС")],
        delivery=F("Доставка по городу"), price_info=F("от 300 руб"),
    )


def test_region_matching_handles_aliases_and_forms():
    assert region_matches("СПб", "г. Санкт-Петербург")
    assert region_matches("Москва", "Московская область")
    assert not region_matches("Казань", "Санкт-Петербург")


def test_full_supplier_scores_high():
    r = score_supplier(full(), "Санкт-Петербург", Filters())
    assert r.score == 97 and r.passes and not r.missing  # минималка известна, но лимита нет: 12 из 15
    assert score_supplier(full(), "Санкт-Петербург", Filters(max_min_order_kg=50)).score == 100


def test_empty_supplier_scores_low_and_lists_questions():
    r = score_supplier(Supplier(domain="y.ru", url="https://y.ru"), "Санкт-Петербург", Filters())
    assert r.score < 25
    assert "прайс-лист / цены" in r.missing


def test_filters_exclude():
    s = full()
    s.certificates = []
    assert not score_supplier(s, "Санкт-Петербург", Filters(need_certs=True)).passes
    s = full()
    assert not score_supplier(s, "Санкт-Петербург", Filters(max_min_order_kg=10)).passes
    assert score_supplier(s, "Санкт-Петербург", Filters(max_min_order_kg=50)).passes


def test_other_region_without_nationwide_fails_delivery_filter():
    r = score_supplier(full(), "Казань", Filters(delivery_to_region=True))
    assert not r.passes
    s = full()
    s.nationwide = F("Доставка по всей России")
    s.address = None
    assert score_supplier(s, "Казань", Filters(delivery_to_region=True)).passes
