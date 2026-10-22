import asyncio
import base64

import httpx
import pytest

from app.config import get_settings
from app.search import (CandidateList, build_queries, expand_queries, fallback_queries, find_candidates, horeca_query,
                        is_blacklisted, literal_query, parse_yandex_response, yandex_region_id, yandex_search)

# Ответ Яндекса синтетический (структура как у реального XML-ответа Search API).
XML = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0"><response><results><grouping>
<group><doc id="1"><url>https://nash-souz.ru/horeca</url><title>Продукты для <hlword>Horeca</hlword> в Екатеринбурге</title></doc></group>
<group><doc id="2"><url>https://prodservice.ru/</url><title>Продсервис</title></doc></group>
<group><doc id="3"><url>https://spark-interfax.ru/card/123</url><title>Реестр компаний</title></doc></group>
</grouping></results></response></yandexsearch>"""


def yandex_payload(xml=XML, b64=True):
    return {"rawData": base64.b64encode(xml.encode("utf-8")).decode() if b64 else xml}


def test_yandex_response_is_parsed_from_base64_and_plain_xml():
    for b64 in (True, False):
        found = parse_yandex_response(yandex_payload(b64=b64))
        assert [x["url"] for x in found] == ["https://nash-souz.ru/horeca", "https://prodservice.ru/", "https://spark-interfax.ru/card/123"]
        assert found[0]["title"] == "Продукты для Horeca в Екатеринбурге"            # подсветка слов <hlword> не ломает заголовок


def test_yandex_error_and_garbage_are_reported_clearly():
    err = '<yandexsearch><response><error code="15">Искомая комбинация слов нигде не встречается</error></response></yandexsearch>'
    with pytest.raises(RuntimeError, match="Яндекс: Искомая комбинация"):
        parse_yandex_response(yandex_payload(err))
    with pytest.raises(RuntimeError, match="не XML"):
        parse_yandex_response({"rawData": "это не xml"})


def test_region_ids_for_big_cities():
    assert yandex_region_id("Екатеринбург") == "54" and yandex_region_id("г. Екатеринбург, Свердловская область") == "54"
    assert yandex_region_id("Санкт-Петербург") == "2" and yandex_region_id("СПб") == "2"
    assert yandex_region_id("Москва") == "213" and yandex_region_id("Нижний Новгород") == "47"
    assert yandex_region_id("Ростов-на-Дону") == "39"
    assert yandex_region_id("Урюпинск") == "" and yandex_region_id("") == ""         # неизвестный город: без кода, город в тексте запроса


def test_queries_start_with_what_the_user_would_type():
    assert literal_query("хорека", "Екатеринбург") == "хорека Екатеринбург"
    assert literal_query("сыр", "") == "сыр"
    q = build_queries("хорека", "Екатеринбург", ["поставщик продуктов для ресторанов Екатеринбург", "хорека  екатеринбург"])
    assert q[0] == "хорека Екатеринбург" and len(q) == 2                              # повтор буквального запроса не дублируется
    assert all("Екатеринбург" in x for x in fallback_queries("хорека", "Екатеринбург"))


def test_horeca_is_added_to_any_query_but_not_twice():
    assert horeca_query("сливки", "Екатеринбург") == "сливки хорека Екатеринбург"
    assert horeca_query("сыр моцарелла", "") == "сыр моцарелла хорека"
    for already in ("хорека", "поставщик HoReCa", "продукты для horeca", "сливки хореку"):
        assert horeca_query(already, "Казань") == literal_query(already, "Казань")        # уже сказано: не дублируем
    q = build_queries("сливки", "Екатеринбург", ["сливки оптом производитель Екатеринбург", "сливки прайс Екатеринбург", "сливки дистрибьютор Екатеринбург"])
    assert q[:2] == ["сливки хорека Екатеринбург", "сливки Екатеринбург"]            # сначала с «хорека», потом обычный
    assert len(q) == 5 and len({x.lower() for x in q}) == 5


def test_expansion_prompt_keeps_the_topic_and_handles_vague_categories():
    seen = {}

    class Client:
        class messages:
            @staticmethod
            async def create(**kw):
                seen.update(kw)
                from types import SimpleNamespace
                return SimpleNamespace(content=[SimpleNamespace(type="text", text='["a Екатеринбург", "b Екатеринбург", "c", "d"]')])

    import os
    os.environ["ANTHROPIC_API_KEY"] = "k"
    out = asyncio.run(expand_queries("хорека", "Екатеринбург", client=Client()))
    prompt = seen["messages"][0]["content"]
    assert out == ["a Екатеринбург", "b Екатеринбург", "c"]                            # не больше трёх
    assert "не меняй и не сужай" in prompt and "хорека" in prompt and "Екатеринбург" in prompt


def test_registries_and_foreign_databases_are_blacklisted():
    for u in ("https://www.spark-interfax.ru/x", "https://cbinsights.com/y", "https://jsprav.ru/ekaterinburg", "https://yandex.ru/maps"):
        assert is_blacklisted(u), u
    assert not is_blacklisted("https://nash-souz.ru/")


# --- источники ------------------------------------------------------------------------

def router(handlers):
    def handle(request: httpx.Request) -> httpx.Response:
        for host, fn in handlers.items():
            if host in str(request.url):
                return fn(request)
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def tavily_ok(request):
    return httpx.Response(200, json={"results": [{"url": "https://tkdip.ru/", "title": "T1"}, {"url": "https://nash-souz.ru/horeca", "title": "T2"},
                                                  {"url": "https://shop.com.ua/", "title": "UA"}]})


def yandex_ok(request):
    return httpx.Response(200, json=yandex_payload())


def run(queries, handlers, region="Екатеринбург", limit=12):
    async def go():
        async with router(handlers) as c:
            return await find_candidates(queries, limit, client=c, region=region)
    return asyncio.run(go())


def test_yandex_goes_first_and_sources_are_merged_without_duplicates(monkeypatch):
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "yk"); monkeypatch.setenv("YANDEX_FOLDER_ID", "folder"); monkeypatch.setenv("TAVILY_API_KEY", "tk")
    assert get_settings().sources == ["yandex", "tavily"]
    found = run(["хорека Екатеринбург"], {"searchapi.api.cloud.yandex.net": yandex_ok, "api.tavily.com": tavily_ok})
    domains = [x["domain"] for x in found]
    assert domains == ["nash-souz.ru", "tkdip.ru", "prodservice.ru"]                  # круговое слияние: Яндекс первым; реестр и .ua отсеяны
    assert found.sources_used == ["yandex", "tavily"] and found.skipped_foreign == 1 and not found.source_errors


def test_request_to_yandex_has_region_key_folder_and_query(monkeypatch):
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "yk"); monkeypatch.setenv("YANDEX_FOLDER_ID", "folder"); monkeypatch.setenv("TAVILY_API_KEY", "")
    sent = {}

    def capture(request):
        import json
        sent["headers"], sent["body"] = request.headers, json.loads(request.content)
        return yandex_ok(request)

    found = run(["хорека Екатеринбург"], {"searchapi.api.cloud.yandex.net": capture})
    assert sent["headers"]["authorization"] == "Api-Key yk"
    b = sent["body"]
    assert b["query"]["queryText"] == "хорека Екатеринбург" and b["query"]["searchType"] == "SEARCH_TYPE_RU"
    assert b["region"] == "54" and b["folderId"] == "folder" and b["groupSpec"]["docsInGroup"] == "1"
    assert found.sources_used == ["yandex"]


def test_tavily_request_asks_for_russian_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    sent = {}

    def capture(request):
        import json
        sent["body"] = json.loads(request.content)
        return tavily_ok(request)

    run(["хорека Екатеринбург"], {"api.tavily.com": capture})
    assert sent["body"]["country"] == "russia" and sent["body"]["query"] == "хорека Екатеринбург"


def test_one_broken_source_does_not_stop_the_search_and_is_reported(monkeypatch):
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "yk"); monkeypatch.setenv("YANDEX_FOLDER_ID", "folder"); monkeypatch.setenv("TAVILY_API_KEY", "tk")
    found = run(["хорека Екатеринбург"], {"searchapi.api.cloud.yandex.net": lambda r: httpx.Response(403, text="forbidden"),
                                          "api.tavily.com": tavily_ok})
    assert [x["domain"] for x in found] == ["tkdip.ru", "nash-souz.ru"] and found.sources_used == ["tavily"]
    assert "403" in found.source_errors["yandex"]


def test_all_sources_down_gives_a_readable_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    with pytest.raises(RuntimeError, match="tavily"):
        run(["x"], {"api.tavily.com": lambda r: httpx.Response(500)})


def test_no_keys_means_live_search_is_unavailable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k"); monkeypatch.setenv("TAVILY_API_KEY", ""); monkeypatch.setenv("YANDEX_SEARCH_API_KEY", ""); monkeypatch.setenv("YANDEX_FOLDER_ID", "")
    assert not get_settings().live_available and get_settings().sources == []
    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "yk")
    assert not get_settings().live_available                                          # без каталога Яндекс не работает
    monkeypatch.setenv("YANDEX_FOLDER_ID", "f")
    assert get_settings().live_available and get_settings().sources == ["yandex"]     # одного Яндекса достаточно


def test_status_mentions_sources_and_failures(monkeypatch):
    from app import db, pipeline
    from app.models import Filters, Supplier
    out = CandidateList([{"domain": "ok.ru", "url": "https://ok.ru/"}])
    out.sources_used, out.source_errors = ["tavily"], {"yandex": "Яндекс ответил HTTP 403"}

    async def fake_expand(c, r, client=None): return ["q"]
    async def fake_find(q, l, client=None, region=""): return out

    async def fake_analyse(domain, url, force=False, query="", region=""):
        s = Supplier(domain=domain, url=url)
        db.save_supplier(domain, s.model_dump(), "h")
        return s

    monkeypatch.setattr(pipeline, "expand_queries", fake_expand)
    monkeypatch.setattr(pipeline, "find_candidates", fake_find)
    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    sid = db.create_search("хорека", "Екатеринбург", {}, "live")
    asyncio.run(pipeline.run_live(sid, "хорека", "Екатеринбург", Filters()))
    p = db.get_search(sid)["progress"]
    assert "найдено поставщиков — 1" in p and "Источник недоступен" in p and "Яндекс: Яндекс ответил HTTP 403" in p
