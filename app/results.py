"""Выдача поиска с оценками. Оценка считается при чтении: так работают веса пользователя
и любые правки правил оценки сразу применяются к уже сохранённым поискам."""
from . import db
from .models import Filters, Supplier
from .score import score_supplier


def parse_weights(raw: str | None) -> dict[str, float]:
    """'region:6,docs:0' -> {'region': 6.0, 'docs': 0.0}; мусор пропускаем."""
    out: dict[str, float] = {}
    for part in (raw or "").split(","):
        key, _, value = part.partition(":")
        try:
            out[key.strip()] = float(value)
        except ValueError:
            continue
    return out


def scored_results(search_id: int, weights: dict | None = None) -> list[dict]:
    search = db.get_search(search_id)
    if not search:
        return []
    filters = Filters(**search["filters"])
    rows = db.get_results(search_id)
    counts = db.offer_counts()
    for r in rows:
        extra = r.pop("extra", {}) or {}
        # None: поиск без цен на запрос (старые данные), список (в т.ч. пустой): цены на искомый товар этого поиска
        qp = extra.get("query_prices")
        r["query_prices"] = qp
        r["query_prices_for"] = extra.get("query", "")
        r["delivery_check"] = extra.get("delivery")            # вердикт «возит ли в регион» именно этого поиска
        r["presence_check"] = extra.get("presence")            # вердикт «есть ли офис/склад в городе»
        r["score"] = score_supplier(Supplier(**r["data"]), search["region"], filters, weights,
                                    has_query_prices=None if qp is None else bool(qp),
                                    delivery_check=r["delivery_check"],
                                    presence_check=r["presence_check"]).model_dump()
        r["offers_count"] = counts.get(r["domain"], 0)
    rows.sort(key=lambda x: (not x["score"]["passes"], -x["score"]["score"]))
    return rows
