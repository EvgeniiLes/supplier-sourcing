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
CREATE TABLE IF NOT EXISTS offers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT,
    filename TEXT NOT NULL,
    stored_as TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    data TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    demo INTEGER NOT NULL DEFAULT 0
);
"""

# колонки, добавленные после первого релиза: (таблица, колонка, определение)
MIGRATIONS = [
    ("searches", "total", "INTEGER NOT NULL DEFAULT 0"),
    ("searches", "done", "INTEGER NOT NULL DEFAULT 0"),
]

STATUSES = ["new", "contacted", "quote", "agreed", "rejected"]
LIVE_MODES = ("live", "refresh")   # режимы, которые тратят платные API и идут в дневной лимит поисков


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
        for table, column, definition in MIGRATIONS:
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
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
    marks = ",".join("?" * len(LIVE_MODES))
    with conn() as c:
        return c.execute(
            f"SELECT COUNT(*) FROM searches WHERE mode IN ({marks}) AND created_at>=?",
            (*LIVE_MODES, since),
        ).fetchone()[0]


def count_today(mode: str) -> int:
    """Сколько операций данного режима за последние сутки (загрузки КП считаются отдельно от поисков)."""
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM searches WHERE mode=? AND created_at>=?", (mode, since)).fetchone()[0]


def get_recent_searches(limit: int = 8) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, category, region, mode, status, created_at FROM searches "
            "WHERE mode IN ('live','demo') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- поставщики -------------------------------------------------------------

def save_supplier(domain: str, data: dict, content_hash: str = "", updated_at: str | None = None) -> None:
    """updated_at — когда данные были собраны (для демо-снимков это дата снимка, а не момент записи)."""
    with conn() as c:
        c.execute(
            """INSERT INTO suppliers(domain, data, content_hash, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(domain) DO UPDATE SET data=excluded.data,
               content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
            (domain, json.dumps(data, ensure_ascii=False), content_hash, updated_at or now()),
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


def save_result(search_id: int, domain: str) -> None:
    """Поставщик входит в выдачу поиска. Оценка не хранится: считается при чтении (учёт весов и фильтров)."""
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO search_results(search_id, domain, score) VALUES (?,?,'{}')",
                  (search_id, domain))


def delete_result(search_id: int, domain: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM search_results WHERE search_id=? AND domain=?", (search_id, domain))


def get_results(search_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT s.* FROM search_results r
               JOIN suppliers s ON s.domain = r.domain WHERE r.search_id=?""",
            (search_id,),
        ).fetchall()
    return [_supplier_dict(r) for r in rows]


def get_shortlist() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM suppliers WHERE shortlisted=1 ORDER BY updated_at DESC"
        ).fetchall()
    return [_supplier_dict(r) for r in rows]


def get_suppliers_all() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM suppliers ORDER BY domain").fetchall()
    return [_supplier_dict(r) for r in rows]


# --- коммерческие предложения -------------------------------------------------

def _offer_dict(row) -> dict:
    d = dict(row)
    d["data"] = json.loads(d["data"])
    d["demo"] = bool(d["demo"])
    return d


def create_offer(filename: str, data: dict, domain: str | None = None, received_at: str | None = None,
                 demo: bool = False, stored_as: str = "") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO offers(domain, filename, stored_as, received_at, data, demo) VALUES (?,?,?,?,?,?)",
            (domain, filename, stored_as, received_at or now(), json.dumps(data, ensure_ascii=False), int(demo)))
        return cur.lastrowid


def get_offer(offer_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
    return _offer_dict(row) if row else None


def list_offers(domain: str | None = None, unassigned: bool = False) -> list[dict]:
    """От старых к новым: так удобно считать динамику цен."""
    sql, args = "SELECT * FROM offers", ()
    if unassigned:
        sql += " WHERE domain IS NULL"
    elif domain is not None:
        sql, args = sql + " WHERE domain=?", (domain,)
    with conn() as c:
        rows = c.execute(sql + " ORDER BY received_at, id", args).fetchall()
    return [_offer_dict(r) for r in rows]


def offer_counts() -> dict[str, int]:
    with conn() as c:
        rows = c.execute("SELECT domain, COUNT(*) AS n FROM offers WHERE domain IS NOT NULL GROUP BY domain").fetchall()
    return {r["domain"]: r["n"] for r in rows}


def update_offer(offer_id: int, **fields) -> None:
    allowed = {"domain", "stored_as", "note", "data", "received_at"}
    sets = {k: (json.dumps(v, ensure_ascii=False) if k == "data" else v) for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with conn() as c:
        c.execute(f"UPDATE offers SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), offer_id))


def delete_offer(offer_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM offers WHERE id=?", (offer_id,))


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
