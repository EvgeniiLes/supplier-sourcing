"""Оркестрация: запрос -> поиск -> загрузка -> извлечение -> проверки -> оценка -> БД."""
import asyncio
import json
import os
from pathlib import Path

from . import db
from .config import ROOT, get_settings
from .extract import content_hash, extract_supplier
from .fetch import fetch_site
from .models import Filters, Supplier
from .score import score_supplier
from .search import expand_queries, find_candidates
from .verify import dedupe

DEMO_DIR = Path(os.getenv("DEMO_DIR", str(ROOT / "demo")))
CONCURRENCY = 4


def score_and_store(search_id: int, suppliers: list[Supplier], region: str, filters: Filters) -> None:
    for s in suppliers:
        db.save_result(search_id, s.domain, score_supplier(s, region, filters).model_dump())


async def run_live(search_id: int, category: str, region: str, filters: Filters) -> None:
    cfg = get_settings()
    try:
        db.update_search(search_id, progress="Расширяю запрос и ищу сайты")
        queries = await expand_queries(category, region)
        candidates = await find_candidates(queries, cfg.max_suppliers)
        if not candidates:
            raise RuntimeError("Поиск ничего не нашёл. Попробуйте другую формулировку товара")

        done = 0
        sem = asyncio.Semaphore(CONCURRENCY)
        found: list[Supplier] = []

        async def work(c: dict):
            nonlocal done
            async with sem:
                try:
                    pages = await fetch_site(c["url"])
                    if not pages:
                        return
                    h = content_hash(pages)
                    row = db.get_supplier_row(c["domain"])
                    if row and row["content_hash"] == h:
                        sup = Supplier(**row["data"])  # страницы не менялись — LLM не зовём
                    else:
                        sup = await extract_supplier(c["domain"], c["url"], pages)
                        if sup:
                            db.save_supplier(c["domain"], sup.model_dump(), h)
                    if sup:
                        found.append(sup)
                except Exception:
                    pass  # один сайт не должен ронять весь поиск
                finally:
                    done += 1
                    db.update_search(search_id, progress=f"Разобрано сайтов: {done} из {len(candidates)}")

        await asyncio.gather(*(work(c) for c in candidates))
        unique = dedupe(found)
        for s in unique:  # после склейки сохраняем итоговый вид
            row = db.get_supplier_row(s.domain)
            db.save_supplier(s.domain, s.model_dump(), row["content_hash"] if row else "")
        score_and_store(search_id, unique, region, filters)
        db.update_search(search_id, status="done",
                         progress=f"Готово: найдено поставщиков — {len(unique)}")
    except Exception as e:
        db.update_search(search_id, status="error", error=str(e) or e.__class__.__name__)


# --- демо -------------------------------------------------------------------

def load_snapshots() -> list[dict]:
    out = []
    for p in sorted(Path(DEMO_DIR).glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def find_snapshot(category: str) -> dict | None:
    q = category.lower().replace("ё", "е")
    for snap in load_snapshots():
        if any(k in q or q in k for k in snap.get("keywords", [])):
            return snap
    return None


def run_demo(search_id: int, category: str, region: str, filters: Filters) -> None:
    snap = find_snapshot(category)
    if not snap:
        topics = ", ".join(s["topic"] for s in load_snapshots()) or "нет данных"
        db.update_search(search_id, status="error",
                         error=f"В демо-режиме есть только готовые темы: {topics}")
        return
    suppliers = [Supplier(**d) for d in snap["suppliers"]]
    for s in suppliers:
        row = db.get_supplier_row(s.domain)
        db.save_supplier(s.domain, s.model_dump(), row["content_hash"] if row else "demo")
    score_and_store(search_id, suppliers, region, filters)
    db.update_search(search_id, status="done",
                     progress=f"Демо-данные от {snap.get('collected_at', '')}. Поставщиков: {len(suppliers)}")
