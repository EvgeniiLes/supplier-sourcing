import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, export, pipeline
from app.extract import content_hash, extract_supplier
from app.letter import build_bulk_letter, build_letter
from app.main import app
from app.models import Fact, Filters, Supplier
from app.results import scored_results
from app.score import score_supplier
from app.verify import query_stems, relevant_to_query, verify_price_items

# Тексты страниц синтетические, нужны только тестам.
URL = "https://dairy.ru/price"
PAGE = """Прайс-лист молочной компании. Все цены с НДС.
Сливки 33% пакет 1 л - 310,00 руб/л
Сливки 20% пакет 1 л - 240,00 руб/л
Коктейль Топтыжка банан 500г - 81,55 руб
Молоко 3,2% бутылка 1 л - 95,00 руб/л
Тел. +7 (812) 123-45-67, sales@dairy.ru"""


def item(name, price, quote, unit="л", matches=None, pack=None):
    d = {"name": name, "price": price, "unit": unit, "quote": quote, "url": URL}
    if matches is not None:
        d["matches_query"] = matches
    if pack is not None:
        d["pack"] = pack
    return d


def test_stems_ignore_noise_words():
    assert query_stems("сыр моцарелла оптом") == {"сыр", "моцар"}
    assert query_stems("СЛИВКИ для кафе") == {"сливк", "кафе"}


def test_relevance_by_stem():
    assert relevant_to_query("Сливки 33% пакет", "", "сливки")
    assert relevant_to_query("Моцарелла для пиццы", "", "сыр моцарелла")
    assert not relevant_to_query("Коктейль Топтыжка банан", "", "сливки")
    assert relevant_to_query("что угодно", "", "")                    # запроса нет: фильтр не применяется


def test_only_verified_relevant_prices_survive():
    items = [
        item("Сливки 33% пакет 1 л", 310.0, "Сливки 33% пакет 1 л - 310,00 руб/л", matches=True),
        item("Сливки 20% пакет 1 л", 240.0, "Сливки 20% пакет 1 л - 240,00 руб/л", matches=True),
        item("Коктейль Топтыжка банан 500г", 81.55, "Коктейль Топтыжка банан 500г - 81,55 руб", matches=False),   # чужой товар
        item("Сливки 10% пакет 1 л", 150.0, "Сливки 10% пакет 1 л - 150,00 руб/л", matches=True),               # нет в тексте
        item("Сливки 33% пакет 1 л", 999.0, "Сливки 33% пакет 1 л - 310,00 руб/л", matches=True),               # цена не из цитаты
        item("Сливки 33% пакет 1 л", 310.0, "Сливки 33% пакет 1 л - 310,00 руб/л", matches=True),               # дубль
        {"name": "Сливки", "price": "много", "quote": "x", "url": URL},                                          # мусор
    ]
    ok, rejected = verify_price_items(items, {URL: PAGE}, "сливки")
    assert [(p.name, p.price) for p in ok] == [("Сливки 33% пакет 1 л", 310.0), ("Сливки 20% пакет 1 л", 240.0)]
    assert rejected == 5 and ok[0].url == URL


def test_model_decides_relevance_so_category_queries_work():
    page = "Контейнер ПП 500 мл с крышкой - 9,80 руб/шт\nПакет бумажный крафт - 6,40 руб/шт\nСтаканчик для кофе - 3,10 руб/шт"
    items = [item("Контейнер ПП 500 мл", 9.8, "Контейнер ПП 500 мл с крышкой - 9,80 руб/шт", "шт", matches=True),
             item("Пакет бумажный крафт", 6.4, "Пакет бумажный крафт - 6,40 руб/шт", "шт", matches=True)]
    ok, rejected = verify_price_items(items, {URL: page}, "упаковка для доставки еды")
    assert len(ok) == 2 and rejected == 0                       # по основам слов запроса их бы отсеяло
    # модель сказала «не тот товар»: отбрасываем, даже если слова совпадают
    bad = [item("Контейнер ПП 500 мл", 9.8, "Контейнер ПП 500 мл с крышкой - 9,80 руб/шт", "шт", matches=False)]
    assert verify_price_items(bad, {URL: page}, "контейнер")[0] == []
    # флага нет (старый ответ): запасной вариант, основы слов запроса
    legacy = [item("Контейнер ПП 500 мл", 9.8, "Контейнер ПП 500 мл с крышкой - 9,80 руб/шт", "шт"),
              item("Стаканчик для кофе", 3.1, "Стаканчик для кофе - 3,10 руб/шт", "шт")]
    assert [p.name for p in verify_price_items(legacy, {URL: page}, "контейнер")[0]] == ["Контейнер ПП 500 мл"]


def test_unit_must_stand_next_to_the_price():
    from app.verify import unit_next_to_price
    assert unit_next_to_price("Сыр Моцарелла 816,38 ₽/кг", 816.38, "кг")
    assert unit_next_to_price("Цена 430,00 ₽/шт B упаковке: 12 шт", 430.0, "шт")
    assert unit_next_to_price("Сливки Цена за 1 л: 310 руб", 310.0, "л")
    assert unit_next_to_price("Мука 28,8 руб за кг", 28.8, "кг")
    # вес товара рядом с названием не является единицей цены
    assert not unit_next_to_price("Сыр Моцарелла 45% 100г Вес 0.100 кг Цена от 138,32 ₽", 138.32, "кг")
    assert not unit_next_to_price("Мука мешок 25 кг\n808,00", 808.0, "кг")
    assert not unit_next_to_price("Сыр 500 руб", 500.0, "")


def test_wrong_unit_is_dropped_and_nothing_is_divided_by_guess():
    page = ("Сыр Моцарелла 45% 100г Вес 0.100 кг Цена от 138,32 ₽\n"
            "Сыр Моцарелла блок 816,38 ₽/кг\n"
            "Мука пшеничная - мешок 25 кг\n808,00")
    items = [item("Сыр Моцарелла 45% 100г", 138.32, "Сыр Моцарелла 45% 100г Вес 0.100 кг Цена от 138,32 ₽", "кг", matches=True, pack="0.100 кг"),
             item("Сыр Моцарелла блок", 816.38, "Сыр Моцарелла блок 816,38 ₽/кг", "кг", matches=True),
             item("Мука пшеничная", 808.0, "Мука пшеничная - мешок 25 кг\n808,00", None, matches=True, pack="25 кг")]
    small, block, flour = verify_price_items(items, {URL: page}, "моцарелла мука")[0]
    # единица не стоит рядом с ценой, и в заголовке её нет: не знаем, за что цена, и НЕ делим на фасовку
    assert small.unit == "" and small.base_price is None and not small.derived
    assert flour.unit == "" and flour.base_price is None                                                  # 808: за мешок или за кг, неизвестно
    assert block.unit == "кг" and block.base_price == 816.38 and not block.derived


def test_unit_is_normalized_to_a_plain_word():
    page = "Сыр Моцарелла 816,38 ₽/кг\nСливки Цена 430,00 ₽ / шт"
    items = [item("Сыр Моцарелла", 816.38, "Сыр Моцарелла 816,38 ₽/кг", "₽/кг", matches=True),
             item("Сливки (1л)", 430.0, "Сливки Цена 430,00 ₽ / шт", "за шт", matches=True)]
    cheese, cream = verify_price_items(items, {URL: page}, "сыр сливки")[0]
    assert cheese.unit == "кг" and cream.unit == "шт" and cream.base_unit == "л"


def test_footnote_mark_and_spaced_slash_do_not_hide_the_unit():
    from app.verify import unit_next_to_price
    assert unit_next_to_price("Пшеничная мука от 21* руб. /кг", 21.0, "кг")
    assert unit_next_to_price("цена 28,8 ₽ / кг", 28.8, "кг")


def test_unit_declared_in_table_header_is_accepted_for_bare_numbers():
    page = "Пшеничная мука\nЦена за кг, руб.\nМука высший сорт 50 кг\n28.80\nМука первый сорт 50 кг\n28.10"
    items = [item("Мука высший сорт", 28.8, "Мука высший сорт 50 кг\n28.80", "кг", matches=True, pack="50 кг")]
    flour = verify_price_items(items, {URL: page}, "мука")[0][0]
    assert flour.unit == "кг" and flour.base_price == 28.8 and not flour.derived        # а не 0,58 ₽/кг
    # заголовка нет: единице не верим
    flour2 = verify_price_items(items, {URL: page.replace("Цена за кг, руб.", "Цена")}, "мука")[0][0]
    assert flour2.unit == "" and flour2.base_price is None


def test_per_kg_is_derived_only_for_explicit_piece_price_of_food():
    from app.verify import base_price_of
    assert base_price_of(550.0, "шт", "1 кг", "Сыр Моцарелла Ностра (1 кг)") == (550.0, "кг", True)
    assert base_price_of(430.0, "шт", "", "Сливки Петмол 33% (1л)") == (430.0, "л", True)
    assert base_price_of(21.94, "шт", "", "Контейнер бумажный Ant Box 300 мл") == (None, "", False)   # посуда: цена за литр бессмысленна
    assert base_price_of(21.94, "", "", "Мука 25 кг")[0] is None                                      # без единицы не делим


def test_amounts_from_pack_or_name():
    from app.verify import base_price_of, single_amount
    assert single_amount("25кг") == (25.0, "кг") and single_amount("0.100 кг") == (0.1, "кг")
    assert single_amount("500 мл") == (0.5, "л") and single_amount("(1л)") == (1.0, "л")
    assert single_amount("5-6кг") is None and single_amount("2 кг х 5") == (2.0, "кг")
    assert single_amount("упаковка 12 шт") is None and single_amount("") is None
    assert base_price_of(430.0, "шт", "", "Сливки Петмол 33% (1л)") == (430.0, "л", True)        # цена за штуку 1 л
    assert base_price_of(100.0, "кг", "", "Сыр") == (100.0, "кг", False)
    assert base_price_of(50.0, "шт", "12 шт", "Сливки") == (None, "", False)                      # нет веса: не выдумываем


def test_prices_are_capped():
    page = "\n".join(f"Сливки {i}% - {100 + i},00 руб" for i in range(1, 15))
    items = [item(f"Сливки {i}%", 100.0 + i, f"Сливки {i}% - {100 + i},00 руб") for i in range(1, 15)]
    ok, _ = verify_price_items(items, {URL: page}, "сливки")
    assert len(ok) == 8


class FakeClient:
    def __init__(self, payload):
        self.messages = SimpleNamespace(create=self._create)
        self.payload, self.calls, self.last = payload, 0, None

    async def _create(self, **kw):
        self.calls += 1
        self.last = kw
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.payload)])


PAYLOAD = {
    "is_supplier": True, "name": {"value": "Молочная компания", "quote": "Прайс-лист молочной компании", "url": URL},
    "price_info": {"value": "Открытый прайс-лист", "quote": "Прайс-лист молочной компании", "url": URL},
    "query_prices": [item("Сливки 33% пакет 1 л", 310.0, "Сливки 33% пакет 1 л - 310,00 руб/л", matches=True),
                     item("Коктейль Топтыжка банан 500г", 81.55, "Коктейль Топтыжка банан 500г - 81,55 руб", matches=False)],
}


def test_extraction_gets_query_and_returns_only_requested_item():
    c = FakeClient(PAYLOAD)
    s = asyncio.run(extract_supplier("dairy.ru", URL, {URL: PAGE}, client=c, query="сливки"))
    assert "Пользователь ищет: «сливки»" in c.last["messages"][0]["content"]
    assert [p.name for p in s.query_prices] == ["Сливки 33% пакет 1 л"] and s.query_for == "сливки"
    assert s.rejected_facts == 1                                   # коктейль отброшен и посчитан
    assert s.price_info.value == "Открытый прайс-лист"             # общие условия сохраняются отдельно
    assert "query_prices" in c.last["tools"][0]["input_schema"]["properties"]


def test_extraction_without_query_keeps_legacy_behavior():
    s = asyncio.run(extract_supplier("dairy.ru", URL, {URL: PAGE}, client=FakeClient(PAYLOAD)))
    assert s.query_prices is None and s.query_for == ""            # None: запрос не учитывался


def test_no_prices_for_the_item_is_an_empty_list_not_none():
    payload = {**PAYLOAD, "query_prices": []}
    s = asyncio.run(extract_supplier("dairy.ru", URL, {URL: PAGE}, client=FakeClient(payload), query="сыр"))
    assert s.query_prices == []


def test_cache_key_depends_on_query():
    pages = {URL: PAGE}
    assert content_hash(pages, "сливки") == content_hash(pages, "Сливки оптом")       # слова-шум не в счёт
    assert content_hash(pages, "сливки") != content_hash(pages, "молоко")
    assert content_hash(pages, "сливки") != content_hash(pages)


def test_cache_key_depends_on_extraction_version(monkeypatch):
    from app import extract
    before = content_hash({URL: PAGE}, "сливки")
    monkeypatch.setattr(extract, "EXTRACT_VERSION", extract.EXTRACT_VERSION + 1)
    assert content_hash({URL: PAGE}, "сливки") != before          # правила сменились: старый кэш недействителен


def test_analyse_site_reextracts_for_new_query_and_reuses_for_same(monkeypatch):
    calls = []

    async def fake_fetch(url, client=None, ttl=None):
        return {URL: PAGE}

    async def fake_extract(domain, url, pages, client=None, query=""):
        calls.append(query)
        return Supplier(domain=domain, url=url, query_prices=[], query_for=query)

    monkeypatch.setattr(pipeline, "fetch_site", fake_fetch)
    monkeypatch.setattr(pipeline, "extract_supplier", fake_extract)
    for q in ("сливки", "сливки", "молоко", "сливки оптом"):
        asyncio.run(pipeline.analyse_site("dairy.ru", URL, query=q))
    # 1-й и 2-й вызовы одинаковы -> одно извлечение; «молоко» -> новое; «сливки оптом» после «молоко» -> снова новое
    assert calls == ["сливки", "молоко", "сливки оптом"]


def test_prices_are_stored_per_search(monkeypatch):
    async def fake_expand(c, r, client=None): return ["q"]
    async def fake_find(q, l, client=None): return [{"domain": "dairy.ru", "url": URL}]

    async def fake_analyse(domain, url, force=False, query=""):
        prices = [{"name": f"{query} 1 л", "price": 100.0, "unit": "л", "quote": "x", "url": URL}] if query == "сливки" else []
        s = Supplier(domain=domain, url=url, query_prices=[__import__("app.models", fromlist=["PriceItem"]).PriceItem(**p) for p in prices],
                     query_for=query)
        db.save_supplier(domain, s.model_dump(), "h-" + query)
        return s

    monkeypatch.setattr(pipeline, "expand_queries", fake_expand)
    monkeypatch.setattr(pipeline, "find_candidates", fake_find)
    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    ids = {}
    for q in ("сливки", "сыр"):
        ids[q] = db.create_search(q, "", {}, "live")
        asyncio.run(pipeline.run_live(ids[q], q, "", Filters()))

    a, b = scored_results(ids["сливки"])[0], scored_results(ids["сыр"])[0]
    assert [p["price"] for p in a["query_prices"]] == [100.0] and a["query_prices_for"] == "сливки"
    assert b["query_prices"] == [] and b["query_prices_for"] == "сыр"            # у другого запроса свои цены
    assert "опубликованы цены на искомый товар" in a["score"]["pros"]
    assert any("цен на искомый товар" in c for c in b["score"]["cons"])
    # вернулись к первому поиску: его цены не затёрлись вторым
    assert scored_results(ids["сливки"])[0]["query_prices"][0]["price"] == 100.0


def test_price_score_three_states():
    base = Supplier(domain="x.ru", url="https://x.ru", price_info=Fact(value="Есть прайс", quote="Есть прайс", url="u"))
    legacy = score_supplier(base, "", Filters(), has_query_prices=None)          # старые данные: любые цены годятся
    wrong_item = score_supplier(base, "", Filters(), has_query_prices=False)     # прайс есть, но не на этот товар
    right_item = score_supplier(base, "", Filters(), has_query_prices=True)
    nothing = score_supplier(Supplier(domain="y.ru", url="https://y.ru"), "", Filters(), has_query_prices=False)
    price = lambda r: next(b["points"] for b in r.breakdown if b["key"] == "price")           # noqa: E731
    assert price(right_item) == price(legacy) > price(wrong_item) > price(nothing) == 0
    assert "прайс-лист / цены" in wrong_item.missing and "прайс-лист / цены" in nothing.missing
    assert "прайс-лист / цены" not in right_item.missing


def test_letter_asks_for_price_of_the_requested_item():
    msg = build_letter("Молоко-Опт", "сливки 33%", "СПб", ["прайс-лист / цены"])
    assert "цену на сливки 33% по фасовкам и объёмам" in msg["body"]
    assert "цена на сливки 33%" in build_bulk_letter("сливки 33%", "СПб")["body"].lower()


def test_export_has_prices_for_requested_item():
    it = {"data": {"domain": "x.ru", "url": "https://x.ru", "phones": [], "emails": [], "regions": [], "certificates": [], "sources": []},
          "score": {"score": 50, "pros": [], "cons": []}, "status": "new", "note": "",
          "query_prices": [{"name": "Сливки 33%", "price": 310.0, "unit": "л"},
                           {"name": "Мука", "price": 808.0, "unit": "", "base_price": 32.32, "base_unit": "кг", "derived": True}]}
    row = dict(zip(export.HEADERS, export.rows([it])[0]))
    assert row["Цены на искомый товар"] == "Сливки 33%: 310 ₽/л; Мука: 808 ₽ (≈32.32 ₽/кг, расчёт)"
    it["query_prices"] = []
    assert "запросить КП" in dict(zip(export.HEADERS, export.rows([it])[0]))["Цены на искомый товар"]
    it["query_prices"] = None
    assert dict(zip(export.HEADERS, export.rows([it])[0]))["Цены на искомый товар"] == "не указано"


@pytest.fixture
def client():
    return TestClient(app)


def snapshot_with_prices(tmp_path, monkeypatch):
    sups = [Supplier(domain="a.ru", url="https://a.ru/", query_for="тест", query_prices=[
                {"name": "Тест 1 кг", "price": 100.0, "unit": "кг", "quote": "q", "url": "https://a.ru/p"}]).model_dump(),
            Supplier(domain="b.ru", url="https://b.ru/", query_for="тест", query_prices=[]).model_dump(),
            Supplier(domain="c.ru", url="https://c.ru/").model_dump()]               # старый формат: без цен на запрос
    (tmp_path / "s.json").write_text(json.dumps({"topic": "т", "keywords": ["тест"], "region": "", "collected_at": "2026-01-01",
                                                 "suppliers": sups}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pipeline, "DEMO_DIR", tmp_path)


def test_demo_snapshot_prices_reach_the_api(client, tmp_path, monkeypatch):
    snapshot_with_prices(tmp_path, monkeypatch)
    sid = client.post("/api/search", json={"category": "тест"}).json()["search_id"]
    by = {r["domain"]: r for r in client.get(f"/api/search/{sid}").json()["results"]}
    assert by["a.ru"]["query_prices"][0]["price"] == 100.0 and by["a.ru"]["query_prices_for"] == "тест"
    assert by["b.ru"]["query_prices"] == [] and by["c.ru"]["query_prices"] is None
    xl = client.get(f"/api/export?search_id={sid}&format=csv").content.decode("utf-8-sig")
    assert "Тест 1 кг: 100 ₽/кг" in xl


def test_refresh_updates_prices_of_that_search(client, tmp_path, monkeypatch):
    snapshot_with_prices(tmp_path, monkeypatch)
    sid = client.post("/api/search", json={"category": "тест"}).json()["search_id"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k"); monkeypatch.setenv("TAVILY_API_KEY", "k")
    seen = {}

    async def fake_analyse(domain, url, force=False, query=""):
        seen["query"] = query
        from app.models import PriceItem
        s = Supplier(domain=domain, url=url, query_for=query,
                     query_prices=[PriceItem(name="Тест 1 кг", price=77.0, unit="кг", quote="q", url=url)])
        db.save_supplier(domain, s.model_dump(), "live")
        return s

    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    assert client.post(f"/api/supplier/a.ru/refresh?search_id={sid}").status_code == 200
    assert seen["query"] == "тест"                                                   # запрос берётся из поиска
    by = {r["domain"]: r for r in client.get(f"/api/search/{sid}").json()["results"]}
    assert by["a.ru"]["query_prices"][0]["price"] == 77.0


def test_database_without_extra_column_is_migrated(tmp_path, monkeypatch):
    import sqlite3
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.executescript("""CREATE TABLE search_results(search_id INTEGER NOT NULL, domain TEXT NOT NULL, score TEXT NOT NULL,
                       PRIMARY KEY(search_id, domain));
                       INSERT INTO search_results VALUES (1, 'a.ru', '{}');""")
    c.commit(); c.close()
    monkeypatch.setenv("DB_PATH", str(old))
    db.save_supplier("a.ru", Supplier(domain="a.ru", url="https://a.ru/").model_dump())
    assert db.get_results(1)[0]["extra"] == {}                                       # старая строка читается
    db.save_result(1, "a.ru", {"query_prices": []})
    assert db.get_results(1)[0]["extra"] == {"query_prices": []}
