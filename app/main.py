import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, export, pipeline
from .config import ROOT, get_settings
from .letter import build_letter, polish_with_llm
from .models import Filters

app = FastAPI(title="Снабженец-ассистент")
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


def _public(item: dict) -> dict:
    d = item["data"]
    return {**item, "data": d}


@app.get("/api/health")
def health():
    s = get_settings()
    topics = [{"topic": x["topic"], "region": x.get("region", "")} for x in pipeline.load_snapshots()]
    return {"live_available": s.live_available, "demo_topics": topics,
            "live_left_today": max(0, s.live_daily_limit - db.count_live_today())}


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
def get_search(search_id: int):
    search = db.get_search(search_id)
    if not search:
        raise HTTPException(404, "Поиск не найден")
    results = db.get_results(search_id) if search["status"] == "done" else []
    return {"search": search, "results": [_public(r) for r in results]}


@app.patch("/api/supplier/{domain}")
def patch_supplier(domain: str, body: SupplierPatch):
    if body.status is not None and body.status not in db.STATUSES:
        raise HTTPException(400, "Неизвестный статус")
    if not db.update_supplier_meta(domain, body.status, body.note, body.shortlisted):
        raise HTTPException(404, "Поставщик не найден")
    return db.get_supplier_row(domain)


@app.get("/api/shortlist")
def shortlist():
    return {"items": db.get_shortlist()}


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


@app.get("/api/export")
def export_file(search_id: int | None = None, shortlist: bool = False, format: str = "xlsx"):
    if shortlist:
        items = db.get_shortlist()
    elif search_id is not None:
        items = db.get_results(search_id)
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
