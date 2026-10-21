import json

import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.main import app

SNAP = {
    "topic": "Тестовая тема", "keywords": ["тест"], "region": "Санкт-Петербург", "collected_at": "2026-01-01",
    "suppliers": [{
        "domain": "t.example.ru", "url": "https://t.example.ru/",
        "name": {"value": "Тест", "quote": "Тест", "url": "https://t.example.ru/"},
        "phones": [{"value": "+7 (812) 123-45-67", "quote": "+7 (812) 123-45-67", "url": "https://t.example.ru/"}],
        "emails": [{"value": "a@t.example.ru", "quote": "a@t.example.ru", "url": "https://t.example.ru/"}],
    }],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "snap.json").write_text(json.dumps(SNAP, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pipeline, "DEMO_DIR", tmp_path)
    return TestClient(app)


def test_site_icons_are_served(client):
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200 and ico.headers["content-type"] == "image/png" and ico.content[:4] == b"\x89PNG"
    assert client.get("/apple-touch-icon.png").content[:4] == b"\x89PNG"
    svg = client.get("/static/favicon.svg")
    assert svg.status_code == 200 and b"<svg" in svg.content
    page = client.get("/").text
    assert 'rel="icon"' in page and "apple-touch-icon" in page


def test_health_without_keys(client):
    h = client.get("/api/health").json()
    assert h["live_available"] is False
    assert h["demo_topics"][0]["topic"] == "Тестовая тема"


def test_live_requires_keys(client):
    r = client.post("/api/search", json={"category": "тест", "demo": False})
    assert r.status_code == 503


def test_demo_flow_shortlist_letter_export(client):
    sid = client.post("/api/search", json={"category": "тестовый сыр", "region": "Санкт-Петербург"}).json()["search_id"]
    r = client.get(f"/api/search/{sid}").json()
    assert r["search"]["status"] == "done"
    item = r["results"][0]
    assert item["data"]["domain"] == "t.example.ru" and item["score"]["score"] > 0

    assert client.patch("/api/supplier/t.example.ru", json={"shortlisted": True, "status": "contacted", "note": "звонил"}).status_code == 200
    short = client.get("/api/shortlist").json()["items"]
    assert short[0]["status"] == "contacted" and short[0]["note"] == "звонил"
    assert client.patch("/api/supplier/t.example.ru", json={"status": "bogus"}).status_code == 400

    letter = client.post("/api/letter", json={"domain": "t.example.ru", "category": "сыр",
                                              "region": "Санкт-Петербург", "missing": ["прайс-лист / цены"]}).json()
    assert letter["to"] == "a@t.example.ru" and "прайс-лист" in letter["body"]

    xlsx = client.get(f"/api/export?search_id={sid}&format=xlsx")
    assert xlsx.content[:2] == b"PK"
    csv = client.get("/api/export?shortlist=true&format=csv")
    assert "Тест" in csv.content.decode("utf-8-sig")


def test_demo_unknown_topic_gives_clear_error(client):
    sid = client.post("/api/search", json={"category": "бетон"}).json()["search_id"]
    s = client.get(f"/api/search/{sid}").json()["search"]
    assert s["status"] == "error" and "Тестовая тема" in s["error"]
