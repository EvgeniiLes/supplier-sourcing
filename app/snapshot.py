"""Собирает демо-снимок живым конвейером.

python -m app.snapshot "сыр моцарелла" "Санкт-Петербург" --keywords сыр моцарелла
Нужны ANTHROPIC_API_KEY и TAVILY_API_KEY. Результат: demo/<slug>.json
"""
import argparse
import asyncio
import json
import re
from datetime import date

from . import db, pipeline
from .config import get_settings
from .models import Filters


def slug(text: str) -> str:
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeezzijklmnoprstufhccss_y_eua")
    return re.sub(r"[^a-z0-9]+", "-", text.lower().translate(table)).strip("-") or "topic"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("category")
    ap.add_argument("region")
    ap.add_argument("--keywords", nargs="*", default=None)
    args = ap.parse_args()
    if not get_settings().live_available:
        raise SystemExit("Задайте ANTHROPIC_API_KEY и TAVILY_API_KEY в .env")

    sid = db.create_search(args.category, args.region, Filters().model_dump(), "snapshot")  # не считается в дневной лимит
    await pipeline.run_live(sid, args.category, args.region, Filters())
    search = db.get_search(sid)
    if search["status"] != "done":
        raise SystemExit(search["error"])

    suppliers = [r["data"] for r in db.get_results(sid)]
    snap = {
        "topic": args.category, "region": args.region,
        "keywords": [k.lower() for k in (args.keywords or args.category.split())],
        "collected_at": date.today().isoformat(), "suppliers": suppliers,
    }
    pipeline.DEMO_DIR.mkdir(exist_ok=True)
    path = pipeline.DEMO_DIR / f"{slug(args.category)}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Сохранено: {path} ({len(suppliers)} поставщиков)")


if __name__ == "__main__":
    asyncio.run(main())
