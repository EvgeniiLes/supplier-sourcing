"""Сравнение позиций между КП одного поставщика: что подорожало, что подешевело, что новое."""
import difflib
import re

UNIT_ALIASES = {
    "кг": "кг", "kg": "кг", "килограмм": "кг", "кило": "кг", "г": "г", "гр": "г", "грамм": "г",
    "т": "т", "тн": "т", "тонна": "т", "л": "л", "литр": "л", "мл": "мл",
    "шт": "шт", "штука": "шт", "штуки": "шт", "уп": "уп", "упак": "уп", "упаковка": "уп",
    "кор": "кор", "коробка": "кор", "пач": "пач", "пачка": "пач", "бут": "бут", "бутылка": "бут",
    "меш": "меш", "мешок": "меш", "бл": "бл", "блок": "бл",
}
SAME_THRESHOLD = 0.8


def norm_unit(unit: str | None) -> str:
    """'руб./кг', 'за 1 кг', 'КГ.' -> 'кг'; незнакомое оставляем как есть (в нижнем регистре)."""
    u = (unit or "").lower().replace("ё", "е")
    u = re.sub(r"руб(лей|ля|\.)?|₽|за|\d+", " ", u)
    u = re.sub(r"[\s/.\\,]+", " ", u).strip()
    return UNIT_ALIASES.get(u, u)


def norm_name(name: str) -> str:
    n = (name or "").lower().replace("ё", "е")
    n = re.sub(r"[«»\"'()\[\];:–—-]", " ", n)
    n = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", n)          # точка и запятая между цифрами (2,3) остаются
    return re.sub(r"\s+", " ", n).strip()


def _tokens(name: str) -> set[str]:
    return set(norm_name(name).split())


def _numbers(tokens: set[str]) -> set[str]:
    return {t for t in tokens if re.search(r"\d", t)}


PACK_WORDS = r"(?:мешок|мешки|канистра|пакет|короб|коробка|упаковка|уп|бутылка|банка|ведро|брус|блок|пачка|бочка|контейнер|лоток|фасовка|тара)"
PACK_PHRASES = (
    re.compile(PACK_WORDS + r"\.?\s*(?:по\s*)?\d+(?:[.,]\d+)?\s*(?:кг|гр|г|л|мл|шт)\b\.?"),      # «мешок 25 кг», «брус 2,3 кг»
    re.compile(r"\d+(?:[.,]\d+)?\s*(?:кг|гр|г|л|мл|шт)\.?\s*" + PACK_WORDS + r"\b"),              # «25 кг короб»
)


def _without_pack(name: str, pack: str | None) -> str:
    """Название без описания фасовки: в одном КП она в отдельной колонке, в другом дописана к названию.
    Остальные числа (жирность, сорт, вес порции в названии) сохраняются: по ним позиции различаются."""
    n, p = norm_name(name), norm_name(pack or "")
    if p and p in n:
        n = n.replace(p, " ")
    for rx in PACK_PHRASES:
        n = rx.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def similarity(a: str, b: str, pack_a: str | None = None, pack_b: str | None = None) -> float:
    """0..1. Если числа в названиях (жирность, сорт, вес) не совпадают, это разные позиции."""
    na, nb = _without_pack(a, pack_a), _without_pack(b, pack_b)
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    if _numbers(ta) != _numbers(tb):
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    return max(jaccard, ratio)


def item_similarity(x: dict, y: dict) -> float:
    return similarity(x["name"], y["name"], x.get("pack"), y.get("pack"))


def _units_compatible(u1: str, u2: str) -> bool:
    return not u1 or not u2 or u1 == u2


def match_items(prev: list[dict], new: list[dict]) -> list[tuple[dict, dict | None]]:
    """Каждой позиции нового КП ищем лучшую пару в прошлом (одна позиция прошлого — одна пара)."""
    pairs: list[tuple[dict, dict | None]] = []
    used: set[int] = set()
    for item in new:
        best, best_i, best_score = None, -1, SAME_THRESHOLD
        for i, old in enumerate(prev):
            if i in used:
                continue
            score = item_similarity(item, old)
            if score >= best_score:
                best, best_i, best_score = old, i, score
        if best is not None:
            used.add(best_i)
        pairs.append((item, best))
    return pairs


def compare(prev_offer: dict | None, new_offer: dict) -> dict:
    """Сравнение нового КП с предыдущим. prev_offer/new_offer: поле data из таблицы offers."""
    new_items = new_offer.get("items", [])
    if not prev_offer:
        return {"has_previous": False, "rows": [], "removed": [], "summary": {}, "warnings": []}
    prev_items = prev_offer.get("items", [])

    rows = []
    matched_prev_names: set[str] = set()
    for item, old in match_items(prev_items, new_items):
        row = {"name": item["name"], "unit": item.get("unit") or "", "price": item["price"],
               "prev_price": None, "prev_unit": "", "delta": None, "delta_pct": None, "trend": "new"}
        if old is not None:
            matched_prev_names.add(old["name"])
            row["prev_price"], row["prev_unit"] = old["price"], old.get("unit") or ""
            if not _units_compatible(norm_unit(item.get("unit")), norm_unit(old.get("unit"))):
                row["trend"] = "unit_changed"
            else:
                delta = round(item["price"] - old["price"], 2)
                row["delta"] = delta
                row["delta_pct"] = round(delta / old["price"] * 100, 1) if old["price"] else None
                row["trend"] = "up" if delta > 0 else "down" if delta < 0 else "same"
        rows.append(row)

    removed = [i for i in prev_items if i["name"] not in matched_prev_names]
    counts = {t: sum(1 for r in rows if r["trend"] == t) for t in ("up", "down", "same", "new", "unit_changed")}
    pcts = [r["delta_pct"] for r in rows if r["delta_pct"] is not None]
    summary = {**counts, "removed": len(removed),
               "avg_change_pct": round(sum(pcts) / len(pcts), 1) if pcts else None}

    warnings = []
    if prev_offer.get("currency") and new_offer.get("currency") and prev_offer["currency"] != new_offer["currency"]:
        warnings.append("Валюта в двух КП разная, сравнение цен может быть неверным")
    pv, nv = prev_offer.get("vat_included"), new_offer.get("vat_included")
    if pv is not None and nv is not None and pv != nv:
        warnings.append("В одном КП цены с НДС, в другом без НДС, учтите при сравнении")
    return {"has_previous": True, "rows": rows, "removed": removed, "summary": summary, "warnings": warnings}


def history(offers: list[dict]) -> list[dict]:
    """Динамика цены по каждой позиции за все КП. offers: [{'id', 'date', 'data'}] от старых к новым."""
    clusters: list[dict] = []
    for off in offers:
        for item in off["data"].get("items", []):
            unit = norm_unit(item.get("unit"))
            target = None
            for cl in clusters:
                if _units_compatible(cl["unit_norm"], unit) and item_similarity(cl["first"], item) >= SAME_THRESHOLD:
                    target = cl
                    break
            if target is None:
                target = {"name": item["name"], "unit": item.get("unit") or "", "unit_norm": unit,
                          "first": item, "series": []}
                clusters.append(target)
            target["series"].append({"offer_id": off["id"], "date": off["date"], "price": item["price"]})
    for cl in clusters:
        prices = [p["price"] for p in cl["series"]]
        first, last = prices[0], prices[-1]
        cl["change_pct"] = round((last - first) / first * 100, 1) if len(prices) > 1 and first else None
        cl.pop("unit_norm")
        cl.pop("first")
    clusters.sort(key=lambda c: (-len(c["series"]), c["name"]))
    return clusters
