import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, pipeline
from app.main import app
from app.models import Filters, Supplier
from app.search import find_candidates, is_foreign_domain
from app.verify import foreign_signals

UA_SITE = "ООО Тест. Тел. +380 (44) 123-45-67, Киев, ул. Примерная 1. Сливки 33% - 150 грн, масло - 220 грн."
RU_SITE = "ООО Тест. Тел. +7 (812) 123-45-67, Санкт-Петербург. Сливки 33% - 310 руб."


def test_ukrainian_domains_are_foreign_by_default():
    for u in ("https://sofia-tc.com.ua/ru/", "https://shop.kiev.ua", "sofia-tc.com.ua", "https://example.ua/x"):
        assert is_foreign_domain(u), u
    for u in ("https://example.ru", "https://eastfood.spb.ru/product", "https://site.com", "https://uavto.ru"):
        assert not is_foreign_domain(u), u


def test_excluded_zones_are_configurable(monkeypatch):
    monkeypatch.setenv("EXCLUDED_TLDS", "ua, .by")
    assert is_foreign_domain("https://shop.by") and is_foreign_domain("https://x.com.ua")
    monkeypatch.setenv("EXCLUDED_TLDS", "")
    assert not is_foreign_domain("https://x.com.ua")                 # пусто: ничего не исключаем


def tavily_client(results):
    handler = lambda request: httpx.Response(200, json={"results": results})        # noqa: E731
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_candidates_from_search_skip_foreign_zones_and_count_them():
    results = [{"url": "https://sofia-tc.com.ua/ru/", "title": "UA"}, {"url": "https://milk-opt.ru/", "title": "RU"},
               {"url": "https://shop.kiev.ua/", "title": "UA2"}, {"url": "https://www.avito.ru/x", "title": "агрегатор"}]

    async def run():
        async with tavily_client(results) as c:
            return await find_candidates(["сливки"], 10, client=c)

    found = asyncio.run(run())
    assert [x["domain"] for x in found] == ["milk-opt.ru"]
    assert found.skipped_foreign == 2                                  # агрегаторы в счёт «зарубежных» не входят


def test_foreign_signals_are_strong_only():
    assert foreign_signals({"u": UA_SITE})
    assert foreign_signals({"u": "Телефон: 380 44 123 45 67"})                                   # без плюса
    assert foreign_signals({"u": "Сливки 150 грн. Масло 220 грн."}) == "цены в гривнах"
    assert foreign_signals({"u": "Цена 99 ₴ и ещё 120 ₴"})
    assert not foreign_signals({"u": RU_SITE})
    assert not foreign_signals({"u": "Мы работаем с поставщиками из Украины и Беларуси. Цена 150 грн за образец."})   # одно слабое упоминание
    assert not foreign_signals({"u": "Артикул 38044123456 в каталоге"})                          # номер без границы слова не телефон
    assert not foreign_signals({"u": "ООО «Тест», ОГРН 1054001004870. Филиал ОГРН 1027700132195, Киевское шоссе, 22"})   # «ОГРН» не «грн»


def test_site_with_ukrainian_phone_is_rejected_before_the_model_is_called(monkeypatch):
    async def fake_fetch(url, client=None, ttl=None):
        return {"https://shop.ru/": UA_SITE}

    async def must_not_be_called(*a, **kw):
        raise AssertionError("модель не должна вызываться для зарубежного сайта")

    monkeypatch.setattr(pipeline, "fetch_site", fake_fetch)
    monkeypatch.setattr(pipeline, "extract_supplier", must_not_be_called)
    with pytest.raises(pipeline.ForeignSite):
        asyncio.run(pipeline.analyse_site("shop.ru", "https://shop.ru/", query="сливки"))


def test_live_search_reports_excluded_sites(monkeypatch):
    async def fake_expand(c, r, client=None): return ["q"]

    async def fake_find(q, l, client=None):
        out = find_result([{"domain": "ok.ru", "url": "https://ok.ru/"}, {"domain": "ua-by-phone.ru", "url": "https://ua-by-phone.ru/"}])
        out.skipped_foreign = 2                                       # два .ua отсеяны ещё на этапе выдачи
        return out

    async def fake_analyse(domain, url, force=False, query="", region=""):
        if domain == "ua-by-phone.ru":
            raise pipeline.ForeignSite("телефоны +380")
        s = Supplier(domain=domain, url=url)
        db.save_supplier(domain, s.model_dump(), "h")
        return s

    monkeypatch.setattr(pipeline, "expand_queries", fake_expand)
    monkeypatch.setattr(pipeline, "find_candidates", fake_find)
    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    sid = db.create_search("сливки", "", {}, "live")
    asyncio.run(pipeline.run_live(sid, "сливки", "", Filters()))
    s = db.get_search(sid)
    assert s["status"] == "done" and "найдено поставщиков — 1" in s["progress"]
    assert "исключено сайтов не из РФ: 3" in s["progress"]            # 2 по зоне + 1 по телефону
    assert [r["domain"] for r in db.get_results(sid)] == ["ok.ru"]


def find_result(items):
    from app.search import CandidateList
    return CandidateList(items)


def test_refresh_of_foreign_site_gives_clear_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k"); monkeypatch.setenv("TAVILY_API_KEY", "k")
    db.save_supplier("shop.ru", Supplier(domain="shop.ru", url="https://shop.ru/").model_dump())

    async def fake_fetch(url, client=None, ttl=None):
        return {"https://shop.ru/": UA_SITE}

    monkeypatch.setattr(pipeline, "fetch_site", fake_fetch)
    r = TestClient(app).post("/api/supplier/shop.ru/refresh")
    assert r.status_code == 502 and "не из РФ" in r.json()["detail"]
