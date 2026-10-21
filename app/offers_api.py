"""API коммерческих предложений: загрузка, привязка к поставщику, история и динамика цен."""
import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db, pipeline
from .config import ROOT, get_settings
from .docs import ALLOWED, Document, DocError, read_document
from .offers import auto_target, build_offer_data, extract_offer, match_supplier, supplier_from_offer
from .pricing import compare, history

router = APIRouter(prefix="/api")
UPLOAD_DIR_NAME = "offers"


class AssignIn(BaseModel):
    domain: str | None = None
    create: bool = False


def _upload_dir() -> Path:
    p = get_settings().db_path.parent / UPLOAD_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(name: str) -> str:
    name = Path(name or "document").name
    return re.sub(r"[^\w .()\-№]+", "_", name, flags=re.U)[:120] or "document"


def _effective_date(data: dict) -> str | None:
    return data.get("offer_date")


def _view(offer: dict, prev: dict | None) -> dict:
    """Предложение для интерфейса: данные + сравнение с предыдущим КП того же поставщика."""
    return {
        "id": offer["id"], "domain": offer["domain"], "filename": offer["filename"],
        "received_at": offer["received_at"], "demo": offer["demo"], "has_file": bool(offer["stored_as"]),
        "data": offer["data"], "diff": compare(prev["data"] if prev else None, offer["data"]),
    }


def _views_for(domain: str) -> list[dict]:
    offers = db.list_offers(domain=domain)
    out, prev = [], None
    for o in offers:
        out.append(_view(o, prev))
        prev = o
    return out


def _after_assign(domain: str) -> None:
    """КП получено: если по поставщику были «новый» или «написал», переводим в «получил КП»."""
    row = db.get_supplier_row(domain)
    if row and row["status"] in ("new", "contacted"):
        db.update_supplier_meta(domain, "quote", None, None)


def _match_payload(offer_id: int, data: dict) -> dict:
    cands = match_supplier(data["supplier"], db.get_suppliers_all())
    target = auto_target(cands)
    if target:
        db.update_offer(offer_id, domain=target)
        _after_assign(target)
    return {"assigned": target, "candidates": cands[:5]}


def _full_view(offer_id: int) -> dict:
    offer = db.get_offer(offer_id)
    prev = None
    if offer["domain"]:
        earlier = [o for o in db.list_offers(domain=offer["domain"]) if (o["received_at"], o["id"]) < (offer["received_at"], offer["id"])]
        prev = earlier[-1] if earlier else None
    return _view(offer, prev)


def _budget() -> None:
    s = get_settings()
    if not s.live_available:
        raise HTTPException(503, "Распознавание КП недоступно: на сервере не заданы ключи API. "
                                 "Попробуйте пример КП кнопкой ниже.")
    if db.count_today("offer") >= s.offer_daily_limit:
        raise HTTPException(429, "Дневной лимит загрузок КП исчерпан. Попробуйте завтра или пример КП.")


@router.post("/offers")
async def upload_offer(file: UploadFile | None = File(None), text: str = Form("")):
    """Принимает файл (PDF, Word, Excel, письмо .eml, текст, картинка) или вставленный текст письма."""
    _budget()
    try:
        if file is not None and file.filename:
            content = await file.read(9 * 1024 * 1024)
            doc, filename = read_document(file.filename, content), _safe_name(file.filename)
            raw_bytes = content
        elif text.strip():
            body = text.strip()[:60_000].encode("utf-8")
            doc, filename, raw_bytes = read_document("письмо.txt", body), "Вставленный текст.txt", None
        else:
            raise HTTPException(400, "Прикрепите файл или вставьте текст письма")
    except DocError as e:
        raise HTTPException(400, str(e))

    sid = db.create_search("КП", "", {}, "offer")     # считаем в дневной лимит
    try:
        data = await extract_offer(doc)
    except Exception as e:
        db.update_search(sid, status="error", error=str(e))
        raise HTTPException(502, "Не удалось распознать документ, попробуйте ещё раз")
    db.update_search(sid, status="done")
    if data is None or not data["items"]:
        raise HTTPException(422, "Не нашёл в документе цен: это не похоже на КП или прайс. "
                                 "Если это скан, пришлите файл в лучшем качестве.")

    offer_id = db.create_offer(filename, data, received_at=_effective_date(data))
    if raw_bytes is not None:
        stored = f"{offer_id}-{uuid.uuid4().hex[:8]}{Path(filename).suffix.lower()}"
        (_upload_dir() / stored).write_bytes(raw_bytes)
        db.update_offer(offer_id, stored_as=stored)
    match = _match_payload(offer_id, data)
    return {"offer": _full_view(offer_id), "match": match}


@router.post("/offers/demo")
def load_demo_offers():
    """Два вымышленных КП одной компании: показывает привязку, историю и динамику цен без затрат на ИИ."""
    path = pipeline.DEMO_DIR / "offers_demo.json"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "Демо-КП не найдены")
    domain = None
    for item in spec["offers"]:
        data = build_offer_data(item["raw"], Document(item["text"], None, "text"))
        data["demo"] = True
        if domain is None:
            sup = supplier_from_offer(data["supplier"])
            sup.url = ""                       # сайта нет: ссылку не рисуем
            domain = sup.domain
            if not db.get_supplier_row(domain):
                db.save_supplier(domain, sup.model_dump(), "demo-offer")
        existing = [o for o in db.list_offers(domain=domain) if o["filename"] == item["filename"]]
        if not existing:
            db.create_offer(item["filename"], data, domain=domain, received_at=item["received_at"], demo=True)
    _after_assign(domain)
    return {"domain": domain, "offers": _views_for(domain)}


@router.get("/offers/unassigned")
def unassigned():
    out = []
    for o in db.list_offers(unassigned=True):
        cands = match_supplier(o["data"]["supplier"], db.get_suppliers_all())
        out.append({**_view(o, None), "candidates": cands[:5]})
    return {"items": out}


@router.get("/offers/overview")
def overview():
    """Все поставщики, по которым есть КП, с последним изменением цен: для вкладки «КП»."""
    by_domain: dict[str, list[dict]] = {}
    for o in db.list_offers():
        if o["domain"]:
            by_domain.setdefault(o["domain"], []).append(o)
    items = []
    for domain, offers in by_domain.items():
        row = db.get_supplier_row(domain)
        last = _view(offers[-1], offers[-2] if len(offers) > 1 else None)
        name = ((row["data"].get("name") or {}).get("value") if row else None) or domain
        items.append({"domain": domain, "name": name, "count": len(offers), "last": last,
                      "status": row["status"] if row else "new"})
    items.sort(key=lambda i: i["last"]["received_at"], reverse=True)
    return {"items": items, "unassigned": len(db.list_offers(unassigned=True))}


@router.get("/supplier/{domain}/offers")
def supplier_offers(domain: str):
    if not db.get_supplier_row(domain):
        raise HTTPException(404, "Поставщик не найден")
    offers = db.list_offers(domain=domain)
    views = _views_for(domain)
    hist = history([{"id": o["id"], "date": o["received_at"][:10], "data": o["data"]} for o in offers])
    return {"offers": list(reversed(views)), "history": [h for h in hist if len(h["series"]) > 1]}


@router.post("/offers/{offer_id}/assign")
def assign(offer_id: int, body: AssignIn):
    offer = db.get_offer(offer_id)
    if not offer:
        raise HTTPException(404, "КП не найдено")
    if body.create:
        sup = supplier_from_offer(offer["data"]["supplier"])
        if not db.get_supplier_row(sup.domain):
            db.save_supplier(sup.domain, sup.model_dump(), "from-offer")
        domain = sup.domain
    elif body.domain and db.get_supplier_row(body.domain):
        domain = body.domain
    else:
        raise HTTPException(400, "Выберите поставщика или создайте нового")
    db.update_offer(offer_id, domain=domain)
    _after_assign(domain)
    return {"offer": _full_view(offer_id)}


@router.delete("/offers/{offer_id}")
def remove_offer(offer_id: int):
    offer = db.get_offer(offer_id)
    if not offer:
        raise HTTPException(404, "КП не найдено")
    if offer["stored_as"]:
        (_upload_dir() / offer["stored_as"]).unlink(missing_ok=True)
    db.delete_offer(offer_id)
    return {"ok": True}


@router.get("/offers/{offer_id}/file")
def offer_file(offer_id: int):
    offer = db.get_offer(offer_id)
    if not offer or not offer["stored_as"]:
        raise HTTPException(404, "Файла нет")
    path = _upload_dir() / Path(offer["stored_as"]).name      # имя без путей: обход каталогов невозможен
    if not path.exists():
        raise HTTPException(404, "Файла нет")
    return FileResponse(path, filename=offer["filename"])


@router.get("/suppliers")
def suppliers_brief():
    return {"items": [{"domain": r["domain"], "name": (r["data"].get("name") or {}).get("value") or r["domain"]}
                      for r in db.get_suppliers_all()]}


@router.get("/offers/formats")
def formats():
    return {"extensions": sorted(ALLOWED)}
