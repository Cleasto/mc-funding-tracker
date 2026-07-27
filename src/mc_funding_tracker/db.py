"""SQLite data layer for companies, founders, funding rounds, and notes."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS founders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    class_year INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    website      TEXT,
    sec_cik      TEXT,
    dartmouth_ip INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS founder_companies (
    founder_id INTEGER NOT NULL REFERENCES founders(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    PRIMARY KEY (founder_id, company_id)
);

CREATE TABLE IF NOT EXISTS funding_rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    round_type      TEXT,
    amount_usd      INTEGER,
    announced_date  TEXT,
    investors       TEXT,
    source          TEXT NOT NULL,
    source_url      TEXT,
    status          TEXT NOT NULL DEFAULT 'unconfirmed',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, source, round_type, announced_date, amount_usd)
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the database and tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def add_company(
    name: str,
    website: str,
    dartmouth_ip: bool,
    founders: List[Dict[str, Any]],
) -> int:
    """Create a company and its founders, linking them. Returns the company id.

    `founders` is a list of {"name": str, "class_year": Optional[int]} dicts.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (name, website, dartmouth_ip) VALUES (?, ?, ?)",
            (name, website, 1 if dartmouth_ip else 0),
        )
        company_id = cursor.lastrowid

        for founder in founders:
            founder_name = founder["name"].strip()
            if not founder_name:
                continue
            class_year = founder.get("class_year") or None
            founder_cursor = conn.execute(
                "INSERT INTO founders (name, class_year) VALUES (?, ?)",
                (founder_name, class_year),
            )
            conn.execute(
                "INSERT INTO founder_companies (founder_id, company_id) VALUES (?, ?)",
                (founder_cursor.lastrowid, company_id),
            )

        return company_id


def get_founders_for_company(company_id: int, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        rows = conn.execute(
            """
            SELECT f.* FROM founders f
            JOIN founder_companies fc ON fc.founder_id = f.id
            WHERE fc.company_id = ?
            ORDER BY f.name
            """,
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns_conn:
            conn.close()


def get_latest_round(company_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM funding_rounds
            WHERE company_id = ?
            ORDER BY announced_date DESC, id DESC
            LIMIT 1
            """,
            (company_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns_conn:
            conn.close()


def get_notes_count(company_id: int, conn: Optional[sqlite3.Connection] = None) -> int:
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE company_id = ?", (company_id,)
        ).fetchone()
        return row["n"] if row else 0
    finally:
        if owns_conn:
            conn.close()


def get_companies() -> List[dict]:
    """Return all companies with founders, latest round, and note count attached."""
    with _connect() as conn:
        companies = [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY name").fetchall()]
        for company in companies:
            company["founders"] = get_founders_for_company(company["id"], conn)
            company["latest_round"] = get_latest_round(company["id"], conn)
            company["notes_count"] = get_notes_count(company["id"], conn)
        return companies


def get_company(company_id: int) -> Optional[dict]:
    """Return a single company with founders, funding rounds, and notes attached."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        if row is None:
            return None
        company = dict(row)
        company["founders"] = get_founders_for_company(company_id, conn)
        company["funding_rounds"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM funding_rounds
                WHERE company_id = ?
                ORDER BY announced_date DESC, id DESC
                """,
                (company_id,),
            ).fetchall()
        ]
        company["notes"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM notes WHERE company_id = ? ORDER BY created_at DESC",
                (company_id,),
            ).fetchall()
        ]
        return company


def set_company_cik(company_id: int, sec_cik: str) -> None:
    """Record the resolved SEC CIK for a company, once known."""
    with _connect() as conn:
        conn.execute("UPDATE companies SET sec_cik = ? WHERE id = ?", (sec_cik, company_id))


def add_funding_round(
    company_id: int,
    round_type: Optional[str],
    amount_usd: Optional[int],
    announced_date: Optional[str],
    investors: Optional[str],
    source: str,
    source_url: Optional[str],
    status: Optional[str] = None,
) -> bool:
    """Insert a funding round. Returns True if a new row was inserted (False if it was a dedupe no-op)."""
    if status is None:
        status = "confirmed" if source in ("manual", "sec_edgar") else "unconfirmed"
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO funding_rounds
                (company_id, round_type, amount_usd, announced_date, investors, source, source_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, round_type, amount_usd, announced_date, investors, source, source_url, status),
        )
        return cursor.rowcount > 0


def confirm_round(round_id: int) -> None:
    """Mark a funding round as confirmed after manual review."""
    with _connect() as conn:
        conn.execute("UPDATE funding_rounds SET status = 'confirmed' WHERE id = ?", (round_id,))


def add_note(company_id: int, body: str) -> None:
    """Add a freeform note to a company."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO notes (company_id, body) VALUES (?, ?)",
            (company_id, body),
        )


def get_counts() -> Dict[str, int]:
    """Return row counts for the status CLI command."""
    with _connect() as conn:
        return {
            "companies": conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"],
            "founders": conn.execute("SELECT COUNT(*) AS n FROM founders").fetchone()["n"],
            "funding_rounds": conn.execute("SELECT COUNT(*) AS n FROM funding_rounds").fetchone()["n"],
            "notes": conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"],
        }
