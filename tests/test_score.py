from app.models import Fact, Filters, Supplier
from app.score import min_order_rub, region_matches, score_supplier


def F(v):
    return Fact(value=v, quote=v, url="https://x.ru")


def full():
    return Supplier(
        domain="x.ru", url="https://x.ru",
        address=F("Санкт-Петербург, ул. Примерная"), phones=[F("+7 (812) 123-45-67")], emails=[F("a@x.ru")],
        min_order=F("от 20 кг"), min_order_kg=20, certificates=[F("Декларация ТР ТС")],
        delivery=F("Доставка по городу"), price_info=F("от 300 руб"),
    )


def test_long_values_are_shortened_in_reasons():
    s = full()
    s.address = F("Санкт-Петербург, " + "очень длинный адрес " * 10)
    pro = next(p for p in score_supplier(s, "Санкт-Петербург", Filters()).pros if "регионе" in p)
    assert len(pro) < 100 and "…" in pro


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


def test_min_order_in_rubles_is_read_from_the_text():
    def rub(text):
        s = full()
        s.min_order, s.min_order_kg = F(text), None
        return min_order_rub(s)
    assert rub("Регулярными партиями на сумму от 2000 рублей (для HoReCa)") == 2000
    assert rub("минимальный заказ от 15 000 руб.") == 15000
    assert rub("Мин. сумма заказа 15 000 ₽") == 15000
    assert rub("от 5 тыс. руб") == 5000 and rub("от 3 т.р.") == 3000
    assert rub("от ₽ 7 500") == 7500
    assert rub("от 20 кг") is None and rub("по договорённости") is None      # килограммы и слова рублями не считаем
    s = full()
    s.min_order = Fact(value="Минимальный заказ", quote="Минимальная сумма заказа 4500 руб", url="https://x.ru")
    assert min_order_rub(s) == 4500                                          # если в значении суммы нет, ищем в цитате


def test_min_order_filter_in_rubles_and_kg():
    def s_with(text, kg=None):
        s = full()
        s.min_order, s.min_order_kg = F(text), kg
        return s
    ok = score_supplier(s_with("от 3000 руб"), "Санкт-Петербург", Filters(max_min_order_rub=5000))
    assert ok.passes and "в вашем лимите" in " ".join(ok.pros)
    over = score_supplier(s_with("от 8000 руб"), "Санкт-Петербург", Filters(max_min_order_rub=5000))
    assert not over.passes and any("выше вашего лимита" in c for c in over.cons)
    # лимит в рублях, а минималка в килограммах: сравнить нельзя, не отсеиваем
    assert score_supplier(s_with("от 20 кг", 20), "Санкт-Петербург", Filters(max_min_order_rub=1000)).passes
    # оба лимита: достаточно превысить любой из сравнимых
    assert not score_supplier(s_with("от 20 кг или 9000 руб", 20), "Санкт-Петербург", Filters(max_min_order_kg=50, max_min_order_rub=5000)).passes
    assert score_supplier(s_with("от 20 кг или 3000 руб", 20), "Санкт-Петербург", Filters(max_min_order_kg=50, max_min_order_rub=5000)).passes


def test_other_region_without_nationwide_fails_delivery_filter():
    r = score_supplier(full(), "Казань", Filters(delivery_to_region=True))
    assert not r.passes
    s = full()
    s.nationwide = F("Доставка по всей России")
    s.address = None
    assert score_supplier(s, "Казань", Filters(delivery_to_region=True)).passes
