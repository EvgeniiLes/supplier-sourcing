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
from .search import expand_queries, find_candidates
from .verify import dedupe, foreign_signals

DEMO_DIR = Path(os.getenv("DEMO_DIR", str(ROOT / "demo")))
CONCURRENCY = 4


class ForeignSite(RuntimeError):
    """Сайт не из РФ (по телефону или валюте): в российские закупки не берём."""


def extra_for(sup: Supplier) -> dict:
    """Данные поставщика, которые относятся к конкретному поиску: цены на искомый товар и вердикт
    по доставке в регион поиска. Пусто, если извлечение не знало запроса и региона (старые данные)."""
    extra: dict = {}
    if sup.query_prices is not None:
        extra.update({"query": sup.query_for, "query_prices": [p.model_dump() for p in sup.query_prices]})
    if sup.delivery_check is not None:
        extra["delivery"] = sup.delivery_check.model_dump()
    if sup.presence_check is not None:
        extra["presence"] = sup.presence_check.model_dump()
    return extra


async def analyse_site(domain: str, url: str, force: bool = False, query: str = "", region: str = "") -> Supplier | None:
    """Скачать сайт, извлечь и проверить данные, сохранить поставщика.
    force=True игнорирует кэш страниц и сохранённый хэш (перепроверка).
    query и region — искомый товар и регион пользователя: от них зависят цены и вердикт по доставке; входят в ключ кэша."""
    pages = await fetch_site(url, ttl=0 if force else None)
    if not pages:
        return None
    reason = foreign_signals(pages)
    if reason:                       # до обращения к модели: на зарубежный сайт токены не тратим
        raise ForeignSite(f"Сайт не из РФ ({reason}), в российские закупки не берём")
    h = content_hash(pages, query, region)
    row = db.get_supplier_row(domain)
    if row and row["content_hash"] == h and not force:
        return Supplier(**row["data"])  # страницы, запрос и регион те же: LLM не зовём
    sup = await extract_supplier(domain, url, pages, query=query, region=region)
    if sup:
        db.save_supplier(domain, sup.model_dump(), h)
    return sup


async def run_live(search_id: int, category: str, region: str, filters: Filters) -> None:
    """Результаты попадают в выдачу по мере разбора сайтов: интерфейс показывает их сразу."""
    cfg = get_settings()
    try:
        db.update_search(search_id, progress="Расширяю запрос и ищу сайты")
        queries = await expand_queries(category, region)
        candidates = await find_candidates(queries, cfg.max_suppliers)
        if not candidates:
            raise RuntimeError("Поиск ничего не нашёл. Попробуйте другую формулировку товара")
        db.update_search(search_id, total=len(candidates), done=0,
                         progress=f"Найдено сайтов: {len(candidates)}. Читаю их")

        done = 0
        skipped_foreign = getattr(candidates, "skipped_foreign", 0)     # отсеяны по зоне домена (.ua)
        sem = asyncio.Semaphore(CONCURRENCY)
        found: list[Supplier] = []

        async def work(c: dict):
            nonlocal done, skipped_foreign
            async with sem:
                try:
                    sup = await analyse_site(c["domain"], c["url"], query=category, region=region)
                    if sup:
                        found.append(sup)
                        db.save_result(search_id, sup.domain, extra_for(sup))
                except ForeignSite:
                    skipped_foreign += 1             # по телефону или валюте сайта
                except Exception:
                    pass  # один сайт не должен ронять весь поиск
                finally:
                    done += 1
                    db.update_search(search_id, done=done,
                                     progress=f"Разобрано сайтов: {done} из {len(candidates)}")

        await asyncio.gather(*(work(c) for c in candidates))
        unique = dedupe(found)
        kept = {s.domain for s in unique}
        for s in found:
            if s.domain not in kept:          # склеен с другим доменом той же компании
                db.delete_result(search_id, s.domain)
        for s in unique:                      # после склейки сохраняем итоговый вид
            row = db.get_supplier_row(s.domain)
            db.save_supplier(s.domain, s.model_dump(), row["content_hash"] if row else "")
        note = f" (исключено сайтов не из РФ: {skipped_foreign})" if skipped_foreign else ""
        db.update_search(search_id, status="done",
                         progress=f"Готово: найдено поставщиков — {len(unique)}{note}")
    except Exception as e:
        db.update_search(search_id, status="error", error=str(e) or e.__class__.__name__)


async def refresh_supplier(domain: str, search_id: int | None = None) -> Supplier:
    """Перечитать сайт поставщика заново. Статус, заметка и шорт-лист сохраняются.
    Если указан поиск, цены извлекаются на его запрос и обновляются в его выдаче."""
    row = db.get_supplier_row(domain)
    if not row:
        raise LookupError("Поставщик не найден")
    search = db.get_search(search_id) if search_id else None
    sup = await analyse_site(domain, row["data"]["url"], force=True, query=search["category"] if search else "",
                             region=search["region"] if search else "")
    if sup is None:
        raise RuntimeError("Сайт не открылся или больше не похож на сайт поставщика")
    if search:
        db.update_result_extra(search_id, domain, extra_for(sup))
    return sup


# --- демо -------------------------------------------------------------------

def load_snapshots() -> list[dict]:
    out = []
    for p in sorted(Path(DEMO_DIR).glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "suppliers" in data:   # offers_demo.json и прочее сюда не относится
            out.append(data)
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
    collected = snap.get("collected_at") or ""
    for s in suppliers:
        row = db.get_supplier_row(s.domain)
        # свежие данные (живой поиск, «обновить») демо-снимком не затираем; обновляем только снимок снимком
        if row is None or row["content_hash"] in ("", "demo"):
            db.save_supplier(s.domain, s.model_dump(), "demo", updated_at=f"{collected}T00:00:00+00:00" if collected else None)
        db.save_result(search_id, s.domain, extra_for(s))      # цены из снимка: они на тему снимка
    db.update_search(search_id, status="done", total=len(suppliers), done=len(suppliers),
                     progress=f"Демо-данные от {snap.get('collected_at', '')}. Поставщиков: {len(suppliers)}")
