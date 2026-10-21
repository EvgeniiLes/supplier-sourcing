import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    region TEXT NOT NULL,
    filters TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    progress TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers(
    domain TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    note TEXT NOT NULL DEFAULT '',
    shortlisted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS search_results(
    search_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    score TEXT NOT NULL,
    PRIMARY KEY(search_id, domain)
);
CREATE TABLE IF NOT EXISTS pages(
    url TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

STATUSES = ["new", "contacted", "quote", "agreed", "rejected"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


# --- поиски -----------------------------------------------------------------

def create_search(category: str, region: str, filters: dict, mode: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO searches(category, region, filters, mode, status, created_at) VALUES (?,?,?,?,?,?)",
            (category, region, json.dumps(filters, ensure_ascii=False), mode, "running", now()),
        )
        return cur.lastrowid


def update_search(search_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE searches SET {cols} WHERE id=?", (*fields.values(), search_id))


def get_search(search_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["filters"] = json.loads(d["filters"])
    return d


def count_live_today() -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM searches WHERE mode='live' AND created_at>=?", (since,)
        ).fetchone()[0]


# --- поставщики -------------------------------------------------------------

def save_supplier(domain: str, data: dict, content_hash: str = "") -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO suppliers(domain, data, content_hash, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(domain) DO UPDATE SET data=excluded.data,
               content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
            (domain, json.dumps(data, ensure_ascii=False), content_hash, now()),
        )


def get_supplier_row(domain: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM suppliers WHERE domain=?", (domain,)).fetchone()
    return _supplier_dict(row) if row else None


def _supplier_dict(row) -> dict:
    d = dict(row)
    d["data"] = json.loads(d["data"])
    d["shortlisted"] = bool(d["shortlisted"])
    return d


def update_supplier_meta(domain: str, status: str | None, note: str | None,
                         shortlisted: bool | None) -> bool:
    sets, vals = [], []
    if status is not None:
        sets.append("status=?"); vals.append(status)
    if note is not None:
        sets.append("note=?"); vals.append(note)
    if shortlisted is not None:
        sets.append("shortlisted=?"); vals.append(int(shortlisted))
    if not sets:
        return True
    with conn() as c:
        cur = c.execute(f"UPDATE suppliers SET {', '.join(sets)} WHERE domain=?", (*vals, domain))
        return cur.rowcount > 0


def save_result(search_id: int, domain: str, score: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO search_results(search_id, domain, score) VALUES (?,?,?)",
            (search_id, domain, json.dumps(score, ensure_ascii=False)),
        )


def get_results(search_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT s.*, r.score AS score_json FROM search_results r
               JOIN suppliers s ON s.domain = r.domain WHERE r.search_id=?""",
            (search_id,),
        ).fetchall()
    out = []
    for row in rows:
        d = _supplier_dict(row)
        d["score"] = json.loads(d.pop("score_json"))
        out.append(d)
    out.sort(key=lambda x: (not x["score"]["passes"], -x["score"]["score"]))
    return out


def get_shortlist() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM suppliers WHERE shortlisted=1 ORDER BY updated_at DESC"
        ).fetchall()
    return [_supplier_dict(r) for r in rows]


# --- кэш страниц ------------------------------------------------------------

def get_cached_page(url: str, ttl_days: int) -> str | None:
    with conn() as c:
        row = c.execute("SELECT text, fetched_at FROM pages WHERE url=?", (url,)).fetchone()
    if not row:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    if datetime.now(timezone.utc) - fetched > timedelta(days=ttl_days):
        return None
    return row["text"]


def put_cached_page(url: str, text: str) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO pages(url, text, fetched_at) VALUES (?,?,?)",
                  (url, text, now()))
