import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, pipeline
from app.main import app
from app.models import Fact, Filters, Supplier
from app.results import parse_weights, scored_results
from app.score import DEFAULT_WEIGHTS, normalize_weights, score_supplier


def F(v):
    return Fact(value=v, quote=v, url="https://x.ru")


def sup(domain, **kw):
    return Supplier(domain=domain, url=f"https://{domain}/", **kw)


def test_breakdown_sums_to_score_and_max_to_100():
    s = sup("x.ru", address=F("Санкт-Петербург"), phones=[F("+7 (812) 123-45-67")], delivery=F("по городу"))
    r = score_supplier(s, "Санкт-Петербург", Filters())
    assert abs(sum(b["max"] for b in r.breakdown) - 100) < 0.2
    assert abs(sum(b["points"] for b in r.breakdown) - r.score) < 1
    assert {b["key"] for b in r.breakdown} == set(DEFAULT_WEIGHTS)


def test_weights_change_ranking():
    with_certs = sup("a.ru", certificates=[F("Меркурий")])
    with_price = sup("b.ru", price_info=F("от 100 руб"))
    docs_first = {"docs": 10, "price": 0}
    price_first = {"docs": 0, "price": 10}
    assert score_supplier(with_certs, "", Filters(), docs_first).score > score_supplier(with_price, "", Filters(), docs_first).score
    assert score_supplier(with_price, "", Filters(), price_first).score > score_supplier(with_certs, "", Filters(), price_first).score


def test_weights_are_sanitized():
    assert normalize_weights({"region": 99})["region"] == 10
    assert normalize_weights({"region": -3})["region"] == 0
    assert normalize_weights({"region": "abc"})["region"] == DEFAULT_WEIGHTS["region"]
    assert normalize_weights({k: 0 for k in DEFAULT_WEIGHTS}) == DEFAULT_WEIGHTS   # все нули: как по умолчанию
    assert parse_weights("region:6, docs:0,bad,x:y") == {"region": 6.0, "docs": 0.0}


def test_old_database_is_migrated(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE searches(id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, region TEXT NOT NULL,"
              " filters TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, progress TEXT NOT NULL DEFAULT '',"
              " error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
    c.execute("INSERT INTO searches(category, region, filters, mode, status, created_at) VALUES ('сыр','СПб','{}','demo','done','2026-01-01')")
    c.commit(); c.close()
    monkeypatch.setenv("DB_PATH", str(old))
    s = db.get_search(1)
    assert s["total"] == 0 and s["done"] == 0
    db.update_search(1, total=5, done=2)
    assert db.get_search(1)["done"] == 2


def test_live_results_stream_in_and_duplicates_are_merged(monkeypatch):
    seen_during_run = []

    async def fake_expand(category, region, client=None):
        return ["q"]

    async def fake_find(queries, limit, client=None, region=""):
        return [{"domain": d, "url": f"https://{d}/"} for d in ("a.ru", "b.ru", "dup.ru")]

    async def fake_analyse(domain, url, force=False, query="", region=""):
        if domain == "b.ru":
            await asyncio.sleep(0.05)      # к этому моменту a.ru уже должен быть в выдаче
            seen_during_run.append(len(db.get_results(sid)))
        phone = [F("+7 (812) 123-45-67")] if domain in ("a.ru", "dup.ru") else [F("+7 (495) 000-11-22")]
        s = sup(domain, phones=phone, name=F(domain))
        db.save_supplier(domain, s.model_dump(), "h")
        return s

    monkeypatch.setattr(pipeline, "expand_queries", fake_expand)
    monkeypatch.setattr(pipeline, "find_candidates", fake_find)
    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    sid = db.create_search("сыр", "СПб", {}, "live")
    asyncio.run(pipeline.run_live(sid, "сыр", "СПб", Filters()))

    assert seen_during_run and seen_during_run[0] >= 1
    s = db.get_search(sid)
    assert s["status"] == "done" and s["total"] == 3 and s["done"] == 3
    domains = {r["domain"] for r in scored_results(sid)}
    assert len(domains) == 2 and "b.ru" in domains          # a.ru и dup.ru — одна компания по телефону


def test_one_failing_site_does_not_break_search(monkeypatch):
    async def fake_expand(c, r, client=None): return ["q"]
    async def fake_find(q, l, client=None, region=""): return [{"domain": "ok.ru", "url": "https://ok.ru/"}, {"domain": "bad.ru", "url": "https://bad.ru/"}]

    async def fake_analyse(domain, url, force=False, query="", region=""):
        if domain == "bad.ru":
            raise RuntimeError("boom")
        s = sup(domain)
        db.save_supplier(domain, s.model_dump(), "h")
        return s

    monkeypatch.setattr(pipeline, "expand_queries", fake_expand)
    monkeypatch.setattr(pipeline, "find_candidates", fake_find)
    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    sid = db.create_search("x", "", {}, "live")
    asyncio.run(pipeline.run_live(sid, "x", "", Filters()))
    assert db.get_search(sid)["status"] == "done"
    assert [r["domain"] for r in scored_results(sid)] == ["ok.ru"]


@pytest.fixture
def client():
    return TestClient(app)


def _seed(client, tmp_path, monkeypatch):
    snap = {"topic": "т", "keywords": ["тест"], "region": "СПб", "collected_at": "2026-01-01", "suppliers": [
        sup("a.ru", phones=[F("+7 (812) 123-45-67")], emails=[F("a@a.ru")], certificates=[F("Меркурий")]).model_dump(),
        sup("b.ru", price_info=F("от 10 руб"), emails=[F("b@b.ru")]).model_dump()]}
    (tmp_path / "s.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pipeline, "DEMO_DIR", tmp_path)
    return client.post("/api/search", json={"category": "тест", "region": ""}).json()["search_id"]


def test_api_weights_and_partial_fields(client, tmp_path, monkeypatch):
    sid = _seed(client, tmp_path, monkeypatch)
    docs = client.get(f"/api/search/{sid}?w=docs:10,price:0").json()
    price = client.get(f"/api/search/{sid}?w=docs:0,price:10").json()
    assert docs["results"][0]["domain"] == "a.ru" and price["results"][0]["domain"] == "b.ru"
    assert docs["search"]["total"] == 2 and docs["results"][0]["score"]["breakdown"]
    h = client.get("/api/health").json()
    assert {c["key"] for c in h["criteria"]} == set(DEFAULT_WEIGHTS)


def test_recent_and_bulk_letter(client, tmp_path, monkeypatch):
    sid = _seed(client, tmp_path, monkeypatch)
    assert client.get("/api/recent").json()["items"][0]["id"] == sid
    assert client.post("/api/letter/bulk", json={}).status_code == 400          # шорт-лист пуст
    for d in ("a.ru", "b.ru"):
        client.patch(f"/api/supplier/{d}", json={"shortlisted": True})
    r = client.post("/api/letter/bulk", json={"category": "сыр", "region": "СПб", "mark_contacted": True}).json()
    assert [x["email"] for x in r["recipients"]] == ["b@b.ru", "a@a.ru"] or {x["email"] for x in r["recipients"]} == {"a@a.ru", "b@b.ru"}
    assert "прайс-лист" in r["body"].lower()
    assert {i["status"] for i in client.get("/api/shortlist").json()["items"]} == {"contacted"}


def test_refresh_requires_keys_and_known_supplier(client, tmp_path, monkeypatch):
    _seed(client, tmp_path, monkeypatch)
    assert client.post("/api/supplier/nope.ru/refresh").status_code == 404
    assert client.post("/api/supplier/a.ru/refresh").status_code == 503        # ключей в тестах нет


def test_demo_keeps_snapshot_date_and_does_not_overwrite_fresh_data(client, tmp_path, monkeypatch):
    sid = _seed(client, tmp_path, monkeypatch)
    row = db.get_supplier_row("a.ru")
    assert row["updated_at"].startswith("2026-01-01") and row["content_hash"] == "demo"      # дата снимка, не «сегодня»

    # данные обновили вручную («обновить»): повторный демо-поиск их не откатывает
    fresh = sup("a.ru", phones=[F("+7 (812) 999-99-99")])
    db.save_supplier("a.ru", fresh.model_dump(), "live-hash")
    client.post("/api/search", json={"category": "тест", "region": ""})
    row = db.get_supplier_row("a.ru")
    assert row["data"]["phones"][0]["value"] == "+7 (812) 999-99-99" and row["content_hash"] == "live-hash"
    assert sid


def test_refresh_keeps_status_and_note(client, tmp_path, monkeypatch):
    _seed(client, tmp_path, monkeypatch)
    client.patch("/api/supplier/a.ru", json={"status": "quote", "note": "звонил", "shortlisted": True})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k"); monkeypatch.setenv("TAVILY_API_KEY", "k")

    async def fake_analyse(domain, url, force=False, query="", region=""):
        assert force
        s = sup(domain, phones=[F("+7 (812) 999-99-99")])
        db.save_supplier(domain, s.model_dump(), "new")
        return s

    monkeypatch.setattr(pipeline, "analyse_site", fake_analyse)
    row = client.post("/api/supplier/a.ru/refresh").json()
    assert row["data"]["phones"][0]["value"] == "+7 (812) 999-99-99"
    assert row["status"] == "quote" and row["note"] == "звонил" and row["shortlisted"]
