import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, export, pipeline
from .config import ROOT, get_settings
from .letter import build_bulk_letter, build_letter, polish_with_llm
from .models import Filters
from .results import parse_weights, scored_results
from .score import CRITERIA

from .offers_api import router as offers_router  # noqa: E402

app = FastAPI(title="Снабженец-ассистент")
app.include_router(offers_router)
STATIC = ROOT / "static"
_tasks: set[asyncio.Task] = set()


class SearchIn(BaseModel):
    category: str = Field(min_length=2, max_length=120)
    region: str = Field(default="", max_length=120)
    filters: Filters = Filters()
    demo: bool = True


class SupplierPatch(BaseModel):
    status: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    shortlisted: bool | None = None


class LetterIn(BaseModel):
    domain: str
    category: str = ""
    region: str = ""
    sender: str = Field(default="", max_length=300)
    missing: list[str] = []
    ai: bool = False


class BulkLetterIn(BaseModel):
    category: str = ""
    region: str = ""
    sender: str = Field(default="", max_length=300)
    mark_contacted: bool = False


def _check_live_budget() -> None:
    s = get_settings()
    if not s.live_available:
        raise HTTPException(503, "Нужны ключи API: функция недоступна на этом сервере.")
    if db.count_live_today() >= s.live_daily_limit:
        raise HTTPException(429, "Дневной лимит операций с ИИ исчерпан. Попробуйте завтра.")


@app.get("/api/health")
def health():
    s = get_settings()
    topics = [{"topic": x["topic"], "region": x.get("region", "")} for x in pipeline.load_snapshots()]
    return {"live_available": s.live_available, "demo_topics": topics,
            "live_left_today": max(0, s.live_daily_limit - db.count_live_today()),
            "criteria": [{"key": k, "label": label, "default": w} for k, label, w in CRITERIA]}


@app.get("/api/recent")
def recent():
    return {"items": db.get_recent_searches()}


@app.post("/api/search")
async def start_search(body: SearchIn):
    s = get_settings()
    category = body.category.strip()
    region = body.region.strip()
    if body.demo:
        sid = db.create_search(category, region, body.filters.model_dump(), "demo")
        pipeline.run_demo(sid, category, region, body.filters)
        return {"search_id": sid}
    if not s.live_available:
        raise HTTPException(503, "Живой поиск не настроен: нет ключей API. Включите демо-режим.")
    if db.count_live_today() >= s.live_daily_limit:
        raise HTTPException(429, "Дневной лимит живых поисков исчерпан. Попробуйте демо-режим.")
    sid = db.create_search(category, region, body.filters.model_dump(), "live")
    task = asyncio.create_task(pipeline.run_live(sid, category, region, body.filters))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"search_id": sid}


@app.get("/api/search/{search_id}")
def get_search(search_id: int, w: str | None = None):
    """w — веса критериев: 'region:6,contacts:4,...'. Результаты отдаются и пока поиск идёт (частично)."""
    search = db.get_search(search_id)
    if not search:
        raise HTTPException(404, "Поиск не найден")
    return {"search": search, "results": scored_results(search_id, parse_weights(w))}


@app.post("/api/supplier/{domain}/refresh")
async def refresh_supplier(domain: str):
    row = db.get_supplier_row(domain)
    if not row:
        raise HTTPException(404, "Поставщик не найден")
    if not row["data"].get("url"):
        raise HTTPException(400, "У этого поставщика нет сайта для перепроверки (данные только из КП)")
    _check_live_budget()
    sid = db.create_search(domain, "", {}, "refresh")
    try:
        await pipeline.refresh_supplier(domain)
    except RuntimeError as e:
        db.update_search(sid, status="error", error=str(e))
        raise HTTPException(502, str(e))
    db.update_search(sid, status="done")
    return db.get_supplier_row(domain)


@app.patch("/api/supplier/{domain}")
def patch_supplier(domain: str, body: SupplierPatch):
    if body.status is not None and body.status not in db.STATUSES:
        raise HTTPException(400, "Неизвестный статус")
    if not db.update_supplier_meta(domain, body.status, body.note, body.shortlisted):
        raise HTTPException(404, "Поставщик не найден")
    return db.get_supplier_row(domain)


@app.get("/api/shortlist")
def shortlist():
    counts = db.offer_counts()
    return {"items": [{**it, "offers_count": counts.get(it["domain"], 0)} for it in db.get_shortlist()]}


@app.post("/api/letter")
async def letter(body: LetterIn):
    row = db.get_supplier_row(body.domain)
    if not row:
        raise HTTPException(404, "Поставщик не найден")
    d = row["data"]
    name = (d.get("name") or {}).get("value") or body.domain
    result = build_letter(name, body.category or "продукция", body.region, body.missing, body.sender)
    if body.ai:
        result = await polish_with_llm(result)
    emails = [e["value"] for e in d.get("emails", [])]
    return {**result, "to": emails[0] if emails else ""}


@app.post("/api/letter/bulk")
def letter_bulk(body: BulkLetterIn):
    """Общий запрос КП для всех из шорт-листа: текст, адреса и (по желанию) отметка «написал»."""
    items = db.get_shortlist()
    if not items:
        raise HTTPException(400, "Шорт-лист пуст")
    result = build_bulk_letter(body.category or "продукция", body.region, body.sender)
    recipients = []
    for it in items:
        emails = [e["value"] for e in it["data"].get("emails", [])]
        name = (it["data"].get("name") or {}).get("value") or it["domain"]
        recipients.append({"domain": it["domain"], "name": name, "email": emails[0] if emails else ""})
        if body.mark_contacted and it["status"] == "new":
            db.update_supplier_meta(it["domain"], "contacted", None, None)
    return {**result, "recipients": recipients}


@app.get("/api/export")
def export_file(search_id: int | None = None, shortlist: bool = False, format: str = "xlsx",
                w: str | None = None):
    if shortlist:
        items = db.get_shortlist()
    elif search_id is not None:
        items = scored_results(search_id, parse_weights(w))
    else:
        raise HTTPException(400, "Нужен search_id или shortlist=true")
    if format == "csv":
        return Response(export.to_csv(items), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="suppliers.csv"'})
    return Response(
        export.to_xlsx(items),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="suppliers.xlsx"'},
    )


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if Path(STATIC).exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
