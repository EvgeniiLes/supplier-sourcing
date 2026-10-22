import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, export, pipeline
from app.extract import content_hash, extract_supplier, verify_delivery_check
from app.main import app
from app.models import DeliveryCheck, Fact, Filters, Supplier
from app.results import scored_results
from app.score import score_supplier

# Тексты синтетические, нужны только тестам.
URL = "https://dairy.ru/contacts"
PAGE = ("Молочная компания. Склад: г. Казань, ул. Примерная, 1.\n"
        "Доставка: только по Москве и Московской области.\n"
        "Тел. +7 (843) 123-45-67")


def F(v):
    return Fact(value=v, quote=v, url=URL)


def sup(**kw):
    return Supplier(domain="dairy.ru", url="https://dairy.ru/", **kw)


def dc(verdict, quote="", region="Казань"):
    return DeliveryCheck(region=region, verdict=verdict, quote=quote, url=URL if quote else "")


def region_points(r):
    return next(b["points"] for b in r.breakdown if b["key"] == "region")


# --- модель фильтров --------------------------------------------------------------

def test_filters_legacy_checkbox_maps_to_new_modes():
    assert Filters().delivery == "any" and Filters().presence is False
    assert Filters(delivery_to_region=True).delivery == "not_no"          # старые сохранённые поиски
    assert Filters(delivery_to_region=False).delivery == "any"
    assert Filters(delivery="yes", presence=True).delivery == "yes"       # новое поле не затирается прежним


# --- проверка вердикта по тексту --------------------------------------------------

def test_verdict_needs_a_real_quote():
    ok = verify_delivery_check({"verdict": "no", "quote": "Доставка: только по Москве и Московской области", "url": URL}, {URL: PAGE}, "Казань")
    assert ok.verdict == "no" and ok.url == URL and ok.region == "Казань"
    made_up = verify_delivery_check({"verdict": "yes", "quote": "Доставляем по всей России бесплатно", "url": URL}, {URL: PAGE}, "Казань")
    assert made_up.verdict == "unknown" and made_up.quote == ""            # цитаты нет в тексте: утверждение отброшено
    no_quote = verify_delivery_check({"verdict": "yes"}, {URL: PAGE}, "Казань")
    assert no_quote.verdict == "unknown"
    assert verify_delivery_check(None, {URL: PAGE}, "Казань").verdict == "unknown"
    assert verify_delivery_check({"verdict": "maybe", "quote": "Склад: г. Казань", "url": URL}, {URL: PAGE}, "Казань").verdict == "unknown"


def test_evidence_must_actually_support_the_verdict():
    pages = {URL: PAGE + "\nТелефон в Казани: +7 (843) 111-22-33\nДоставляем по Казани и области\n"
                         "Офис: Республика Татарстан, г. Зеленодольск, ул. Мира, 5\nМы доставляем по всей территории РФ\n"
                         "Компания предлагает свежие сливки и качественное масло\n"
                         "Склад: Московская область, г. Мытищи, ул. Складская, 2"}

    def delivery(quote, region="Казань"):
        return verify_delivery_check({"verdict": "yes", "quote": quote, "url": URL}, pages, region).verdict

    assert delivery("Доставляем по Казани и области") == "yes"                       # назван регион
    assert delivery("Мы доставляем по всей территории РФ") == "yes"                  # вся Россия
    assert delivery("Компания предлагает свежие сливки и качественное масло") == "unknown"   # цитата не про доставку

    def presence(quote, region="Казань"):
        return verify_delivery_check({"verdict": "yes", "quote": quote, "url": URL}, pages, region, allowed=("yes",), kind="presence").verdict

    assert presence("Склад: г. Казань, ул. Примерная, 1") == "yes"
    assert presence("Телефон в Казани: +7 (843) 111-22-33") == "unknown"             # телефон не адрес
    assert presence("Доставляем по Казани и области") == "unknown"                   # доставка не присутствие
    assert presence("Офис: Республика Татарстан, г. Зеленодольск, ул. Мира, 5") == "unknown"   # другой город
    assert presence("Склад: Московская область, г. Мытищи, ул. Складская, 2", region="Москва") == "unknown"   # область, не город
    assert presence("Склад: Московская область, г. Мытищи, ул. Складская, 2", region="Московская область") == "yes"


def test_presence_cannot_be_no():
    raw = {"verdict": "no", "quote": "Склад: г. Казань, ул. Примерная, 1", "url": URL}
    assert verify_delivery_check(raw, {URL: PAGE}, "Казань", allowed=("yes",)).verdict == "unknown"    # нет адреса ≠ нет присутствия
    yes = verify_delivery_check({**raw, "verdict": "yes"}, {URL: PAGE}, "Казань", allowed=("yes",))
    assert yes.verdict == "yes" and "Казань" in yes.quote


class FakeClient:
    def __init__(self, payload):
        self.messages = SimpleNamespace(create=self._create)
        self.payload, self.last = payload, None

    async def _create(self, **kw):
        self.last = kw
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.payload)])


PAYLOAD = {"is_supplier": True,
           "delivery_to_region": {"verdict": "no", "quote": "Доставка: только по Москве и Московской области", "url": URL},
           "presence_in_region": {"verdict": "yes", "quote": "Склад: г. Казань, ул. Примерная, 1", "url": URL}}


def test_extraction_gets_region_and_returns_verified_verdicts():
    c = FakeClient(PAYLOAD)
    s = asyncio.run(extract_supplier("dairy.ru", URL, {URL: PAGE}, client=c, query="сливки", region="Казань"))
    assert "Регион пользователя: «Казань»" in c.last["messages"][0]["content"]
    props = c.last["tools"][0]["input_schema"]["properties"]
    assert "delivery_to_region" in props and "presence_in_region" in props
    assert s.delivery_check.verdict == "no" and s.delivery_check.region == "Казань"
    assert s.presence_check.verdict == "yes" and "Казань" in s.presence_check.quote


def test_extraction_without_region_makes_no_region_checks():
    s = asyncio.run(extract_supplier("dairy.ru", URL, {URL: PAGE}, client=FakeClient(PAYLOAD)))
    assert s.delivery_check is None and s.presence_check is None


def test_cache_key_depends_on_region():
    pages = {URL: PAGE}
    assert content_hash(pages, "сливки", "Казань") != content_hash(pages, "сливки", "Москва")
    assert content_hash(pages, "сливки", "г. Казань") == content_hash(pages, "сливки", "казань")      # запись региона не важна
    assert content_hash(pages, "сливки", "Казань") != content_hash(pages, "сливки")


# --- оценка и фильтры ---------------------------------------------------------------

def test_delivery_no_is_hidden_by_default_filter_and_shown_with_any():
    s = sup()
    quote = "только по Москве и Московской области"
    r = score_supplier(s, "Казань", Filters(delivery="not_no"), delivery_check=dc("no", quote).model_dump())
    assert not r.passes and "не доставляет в ваш регион" in r.fail_reasons
    assert any(quote in c for c in r.cons) and region_points(r) == 0
    assert score_supplier(s, "Казань", Filters(delivery="any"), delivery_check=dc("no", quote).model_dump()).passes


def test_delivery_yes_scores_and_passes_strict_mode():
    r = score_supplier(sup(), "Казань", Filters(delivery="yes"), delivery_check=dc("yes", "доставка по РФ").model_dump())
    assert r.passes and region_points(r) == 26.0                          # подтверждённая доставка ниже, чем офис в городе
    assert "работаете ли вы с нашим регионом" not in r.missing


def test_unknown_delivery_is_kept_by_not_no_but_dropped_by_strict_mode():
    far = sup(address=F("Москва, ул. Тверская"))                           # офис в другом городе, про доставку молчит
    unknown = dc("unknown").model_dump()
    assert score_supplier(far, "Казань", Filters(delivery="not_no"), delivery_check=unknown).passes
    strict = score_supplier(far, "Казань", Filters(delivery="yes"), delivery_check=unknown)
    assert not strict.passes and "доставка в ваш регион не подтверждена" in strict.fail_reasons
    assert "работаете ли вы с нашим регионом" in strict.missing            # в письмо попадёт вопрос
    # старые данные без вердикта: прежняя логика по адресу
    assert not score_supplier(far, "Казань", Filters(delivery="not_no")).passes


def test_nationwide_or_region_list_counts_as_confirmed_for_strict_mode():
    assert score_supplier(sup(nationwide=F("по всей России")), "Казань", Filters(delivery="yes")).passes
    assert score_supplier(sup(regions=[F("Казань и область")]), "Казань", Filters(delivery="yes")).passes


def test_presence_filter_keeps_only_confirmed_local_suppliers():
    local = sup(address=F("Казань, ул. Примерная, 1"))
    by_check = sup()
    elsewhere = sup(address=F("Москва, ул. Тверская"), nationwide=F("по всей России"))
    f = Filters(presence=True)
    r_local = score_supplier(local, "Казань", f)
    r_check = score_supplier(by_check, "Казань", f, presence_check=dc("yes", "Склад: г. Казань").model_dump())
    r_far = score_supplier(elsewhere, "Казань", f, presence_check=dc("unknown").model_dump())
    assert r_local.passes and r_check.passes and region_points(r_local) == region_points(r_check) == 30.0
    assert not r_far.passes and any("нет офиса" in x for x in r_far.fail_reasons)
    assert any("есть присутствие" in p and "Склад: г. Казань" in p for p in r_check.pros)
    assert score_supplier(elsewhere, "Казань", Filters(presence=False)).passes        # без фильтра не мешает


def test_no_region_means_no_region_filters():
    r = score_supplier(sup(), "", Filters(delivery="yes", presence=True), delivery_check=dc("no").model_dump())
    assert r.passes and not r.fail_reasons


def test_verdict_stored_with_supplier_is_used_only_for_the_same_region():
    s = sup(delivery_check=dc("no", "только по Москве", region="Москва"))
    assert score_supplier(s, "Москва", Filters(delivery="not_no")).passes is False      # тот же регион: вердикт действует
    assert score_supplier(s, "Казань", Filters(delivery="not_no")).passes                # чужой регион: вердикт не применяем


# --- весь путь через поиск и API ------------------------------------------------------

def test_live_search_stores_region_verdicts_per_search(monkeypatch):
    async def fake_expand(c, r, client=None): return ["q"]

    async def fake_find(q, l, client=None, region=""): return [{"domain": "dairy.ru", "url": URL}]

    seen = {}

    async def fake_analyse(domain, url, force=False, query="", region=""):
        seen["region"] = region
        s = sup(delivery_check=dc("no", "только по Москве", region=region), presence_check=dc("yes", "Склад: г. Казань", region=region))
        db.save_supplier(domain, s.model_dump(), "h")
        return s

    monkeypatch.setattr(pipeline, "expand_queries", fake_expand)
    monkeypatch.setattr(pipeline, "find_candidates", fake_find)
    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    sid = db.create_search("сливки", "Казань", Filters(delivery="not_no").model_dump(), "live")
    asyncio.run(pipeline.run_live(sid, "сливки", "Казань", Filters(delivery="not_no")))
    assert seen["region"] == "Казань"
    row = scored_results(sid)[0]
    assert row["delivery_check"]["verdict"] == "no" and row["presence_check"]["verdict"] == "yes"
    assert row["score"]["passes"]                         # офис в городе: «не доставляет» это не приговор, раз склад на месте


@pytest.fixture
def client():
    return TestClient(app)


def snapshot(tmp_path, monkeypatch):
    def s(domain, delivery=None, presence=None):
        d = Supplier(domain=domain, url=f"https://{domain}/", delivery_check=delivery, presence_check=presence)
        return d.model_dump()
    sups = [s("far.ru", dc("no", "только по Москве")), s("deliver.ru", dc("yes", "доставка по РФ")),
            s("local.ru", dc("unknown"), dc("yes", "Склад: г. Казань")), s("silent.ru", dc("unknown"), dc("unknown"))]
    (tmp_path / "s.json").write_text(json.dumps({"topic": "т", "keywords": ["тест"], "region": "Казань", "collected_at": "2026-01-01",
                                                 "suppliers": sups}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pipeline, "DEMO_DIR", tmp_path)


def search(client, **filters):
    sid = client.post("/api/search", json={"category": "тест", "region": "Казань", "filters": filters}).json()["search_id"]
    return {r["domain"]: r for r in client.get(f"/api/search/{sid}").json()["results"]}, sid


def test_api_filters_hide_the_right_suppliers(client, tmp_path, monkeypatch):
    snapshot(tmp_path, monkeypatch)
    by, _ = search(client, delivery="not_no")
    assert {d for d, r in by.items() if r["score"]["passes"]} == {"deliver.ru", "local.ru", "silent.ru"}   # far.ru скрыт
    by, _ = search(client, delivery="yes")
    assert {d for d, r in by.items() if r["score"]["passes"]} == {"deliver.ru", "local.ru"}                # silent.ru не подтверждён
    by, _ = search(client, delivery="any", presence=True)
    assert {d for d, r in by.items() if r["score"]["passes"]} == {"local.ru"}
    by, _ = search(client, delivery="any")
    assert all(r["score"]["passes"] for r in by.values())
    old, _ = search(client, delivery_to_region=True)                                                      # старый флажок
    assert not old["far.ru"]["score"]["passes"] and old["silent.ru"]["score"]["passes"]


def test_export_has_region_columns(client, tmp_path, monkeypatch):
    snapshot(tmp_path, monkeypatch)
    _, sid = search(client)
    rows = {r["domain"]: r for r in scored_results(sid)}
    one = dict(zip(export.HEADERS, export.rows([rows["far.ru"]])[0]))
    assert one["Доставка в ваш регион"] == "нет: «только по Москве»" and one["Офис/склад в вашем городе"] == "не проверялось"
    two = dict(zip(export.HEADERS, export.rows([rows["local.ru"]])[0]))
    assert two["Офис/склад в вашем городе"].startswith("да: «Склад: г. Казань")
    assert "Доставка в ваш регион" in client.get(f"/api/export?search_id={sid}&format=csv").content.decode("utf-8-sig")
