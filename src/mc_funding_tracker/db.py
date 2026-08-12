"""SQLite data layer for companies, founders, funding rounds, and notes."""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional

from .config import DB_PATH

_CIK_FROM_URL_RE = re.compile(r"/data/(\d+)/")

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
    cik             TEXT,
    matched_entity_name TEXT,
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

CREATE TABLE IF NOT EXISTS rejected_ciks (
    company_id INTEGER NOT NULL REFERENCES companies(id),
    cik        TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (company_id, cik)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema to pre-existing databases.

    CREATE TABLE IF NOT EXISTS never adds new columns to a table that already
    exists, so anything added post-launch needs an explicit ALTER TABLE guarded
    by a column-existence check.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(funding_rounds)")}
    if "cik" not in columns:
        conn.execute("ALTER TABLE funding_rounds ADD COLUMN cik TEXT")
    if "matched_entity_name" not in columns:
        conn.execute("ALTER TABLE funding_rounds ADD COLUMN matched_entity_name TEXT")

    company_columns = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
    if "last_researched_at" not in company_columns:
        conn.execute("ALTER TABLE companies ADD COLUMN last_researched_at TEXT")

    # Backfill CIK for sec_edgar rows that predate this column (or predate this
    # backfill itself) — it's embedded in source_url (.../data/<cik>/...), so no
    # data is lost. Gated on "cik IS NULL" rather than "just added the column", so
    # it's safe (and a no-op) to run on every init_db() call.
    rows = conn.execute(
        "SELECT id, source_url FROM funding_rounds WHERE source = 'sec_edgar' AND cik IS NULL"
    ).fetchall()
    for row in rows:
        match = _CIK_FROM_URL_RE.search(row["source_url"] or "")
        if match:
            conn.execute(
                "UPDATE funding_rounds SET cik = ? WHERE id = ?", (match.group(1), row["id"])
            )


def init_db() -> None:
    """Create the database and tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


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
            WHERE company_id = ? AND status != 'rejected'
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


def get_total_funding(company_id: int, conn: Optional[sqlite3.Connection] = None) -> int:
    """Return the sum of confirmed funding amounts (USD) for a company.

    Unconfirmed/rejected rounds don't count — they haven't been verified as
    real or as belonging to this company. Undisclosed amounts (NULL) are
    excluded from the sum rather than treated as zero.
    """
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount_usd), 0) AS total FROM funding_rounds
            WHERE company_id = ? AND status = 'confirmed'
            """,
            (company_id,),
        ).fetchone()
        return row["total"]
    finally:
        if owns_conn:
            conn.close()


def get_grand_total_funding() -> int:
    """Return the sum of confirmed funding amounts (USD) across all companies."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM funding_rounds WHERE status = 'confirmed'"
        ).fetchone()
        return row["total"]


def get_companies() -> List[dict]:
    """Return all companies with founders, latest round, and total funding attached."""
    with _connect() as conn:
        companies = [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY name").fetchall()]
        for company in companies:
            company["founders"] = get_founders_for_company(company["id"], conn)
            company["latest_round"] = get_latest_round(company["id"], conn)
            company["total_funding"] = get_total_funding(company["id"], conn)
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
        company["total_funding"] = get_total_funding(company_id, conn)
        return company


def set_company_cik(company_id: int, sec_cik: str) -> None:
    """Record the resolved SEC CIK for a company, once known."""
    with _connect() as conn:
        conn.execute("UPDATE companies SET sec_cik = ? WHERE id = ?", (sec_cik, company_id))


def mark_researched(company_id: int) -> None:
    """Record that a research pass has been run for this company, regardless of
    whether it found anything — lets the UI distinguish "never researched" from
    "researched, nothing found"."""
    with _connect() as conn:
        conn.execute(
            "UPDATE companies SET last_researched_at = datetime('now') WHERE id = ?",
            (company_id,),
        )


def add_funding_round(
    company_id: int,
    round_type: Optional[str],
    amount_usd: Optional[int],
    announced_date: Optional[str],
    investors: Optional[str],
    source: str,
    source_url: Optional[str],
    status: Optional[str] = None,
    cik: Optional[str] = None,
    matched_entity_name: Optional[str] = None,
) -> bool:
    """Insert a funding round. Returns True if a new row was inserted (False if it matched an
    existing row by the dedupe key).

    Only 'internal' entries (self-reported by the user) are confirmed by default.
    SEC EDGAR matches come from a fuzzy company-name text search — the filing
    content is official, but which company it belongs to isn't verified
    automatically, so it needs review just like web-research results do.

    On a dedupe hit, backfills cik/matched_entity_name onto the existing row if it's
    missing them — e.g. rows inserted before those columns existed only get that data
    filled in the next time research turns up the same filing.
    """
    if status is None:
        status = "confirmed" if source == "internal" else "unconfirmed"
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT id, cik, matched_entity_name FROM funding_rounds
            WHERE company_id = ? AND source = ? AND round_type IS ?
              AND announced_date IS ? AND amount_usd IS ?
            """,
            (company_id, source, round_type, announced_date, amount_usd),
        ).fetchone()

        if existing is not None:
            if (not existing["cik"] and cik) or (not existing["matched_entity_name"] and matched_entity_name):
                conn.execute(
                    """
                    UPDATE funding_rounds
                    SET cik = COALESCE(cik, ?), matched_entity_name = COALESCE(matched_entity_name, ?)
                    WHERE id = ?
                    """,
                    (cik, matched_entity_name, existing["id"]),
                )
            return False

        conn.execute(
            """
            INSERT INTO funding_rounds
                (company_id, round_type, amount_usd, announced_date, investors, source, source_url,
                 status, cik, matched_entity_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id, round_type, amount_usd, announced_date, investors, source, source_url,
                status, cik, matched_entity_name,
            ),
        )
        return True


def confirm_round(round_id: int) -> None:
    """Mark a funding round as confirmed after manual review.

    If it's a SEC EDGAR round, also record its CIK on the company — a human
    confirming a round is a much stronger "this is the right company" signal
    than the fuzzy name search that found it in the first place.
    """
    with _connect() as conn:
        conn.execute("UPDATE funding_rounds SET status = 'confirmed' WHERE id = ?", (round_id,))
        row = conn.execute(
            "SELECT company_id, source, cik FROM funding_rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row and row["source"] == "sec_edgar" and row["cik"]:
            conn.execute(
                "UPDATE companies SET sec_cik = ? WHERE id = ? AND sec_cik IS NULL",
                (row["cik"], row["company_id"]),
            )


def reject_round(round_id: int) -> None:
    """Mark a funding round as not belonging to this company.

    If it's a SEC EDGAR round, also blocklist its CIK for this company so future
    research passes skip that filer entirely instead of re-surfacing the same
    wrong-company match every time.
    """
    with _connect() as conn:
        conn.execute("UPDATE funding_rounds SET status = 'rejected' WHERE id = ?", (round_id,))
        row = conn.execute(
            "SELECT company_id, source, cik FROM funding_rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row and row["source"] == "sec_edgar" and row["cik"]:
            conn.execute(
                "INSERT OR IGNORE INTO rejected_ciks (company_id, cik) VALUES (?, ?)",
                (row["company_id"], row["cik"]),
            )


def unreject_round(round_id: int) -> None:
    """Undo a rejection: back to unconfirmed, and un-blocklist its CIK if any."""
    with _connect() as conn:
        conn.execute("UPDATE funding_rounds SET status = 'unconfirmed' WHERE id = ?", (round_id,))
        row = conn.execute(
            "SELECT company_id, source, cik FROM funding_rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row and row["source"] == "sec_edgar" and row["cik"]:
            conn.execute(
                "DELETE FROM rejected_ciks WHERE company_id = ? AND cik = ?",
                (row["company_id"], row["cik"]),
            )


def get_rejected_ciks(company_id: int) -> List[str]:
    """Return CIKs that have been marked as 'not this company' for a company."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT cik FROM rejected_ciks WHERE company_id = ?", (company_id,)
        ).fetchall()
        return [r["cik"] for r in rows]


def update_company(company_id: int, name: str, website: str, dartmouth_ip: bool) -> None:
    """Update a company's name, website, and Dartmouth IP flag."""
    with _connect() as conn:
        conn.execute(
            "UPDATE companies SET name = ?, website = ?, dartmouth_ip = ? WHERE id = ?",
            (name, website, 1 if dartmouth_ip else 0, company_id),
        )


def add_founder(company_id: int, name: str, class_year: Optional[int]) -> None:
    """Add a new founder and link them to an existing company."""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO founders (name, class_year) VALUES (?, ?)",
            (name, class_year),
        )
        conn.execute(
            "INSERT INTO founder_companies (founder_id, company_id) VALUES (?, ?)",
            (cursor.lastrowid, company_id),
        )


def update_founder(founder_id: int, name: str, class_year: Optional[int]) -> None:
    """Update a founder's name and class year."""
    with _connect() as conn:
        conn.execute(
            "UPDATE founders SET name = ?, class_year = ? WHERE id = ?",
            (name, class_year, founder_id),
        )


def delete_founder(founder_id: int) -> None:
    """Delete a founder and their company link(s)."""
    with _connect() as conn:
        conn.execute("DELETE FROM founder_companies WHERE founder_id = ?", (founder_id,))
        conn.execute("DELETE FROM founders WHERE id = ?", (founder_id,))


def update_funding_round(
    round_id: int,
    round_type: Optional[str],
    amount_usd: Optional[int],
    announced_date: Optional[str],
    investors: Optional[str],
) -> None:
    """Update the editable fields of a funding round (status/source are untouched)."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE funding_rounds
            SET round_type = ?, amount_usd = ?, announced_date = ?, investors = ?
            WHERE id = ?
            """,
            (round_type, amount_usd, announced_date, investors, round_id),
        )


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
