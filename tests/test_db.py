"""Tests for the SQLite data layer: schema creation, dedupe, and basic CRUD."""
import pytest

from mc_funding_tracker import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "tracker.db")
    db.init_db()


def test_add_company_creates_founders_and_link():
    company_id = db.add_company(
        "Acme Inc",
        "https://acme.example",
        True,
        [{"name": "Jane Doe", "class_year": 2018}, {"name": "Alex Smith", "class_year": 2019}],
    )
    company = db.get_company(company_id)

    assert company["name"] == "Acme Inc"
    assert company["dartmouth_ip"] == 1
    assert {f["name"] for f in company["founders"]} == {"Jane Doe", "Alex Smith"}


def test_add_funding_round_dedupes_identical_rows():
    company_id = db.add_company("Acme Inc", "", False, [])

    first = db.add_funding_round(
        company_id, "Seed", 2_000_000, "2026-01-15", "Some VC", "manual", None
    )
    second = db.add_funding_round(
        company_id, "Seed", 2_000_000, "2026-01-15", "Some VC", "manual", None
    )

    assert first is True
    assert second is False
    assert len(db.get_company(company_id)["funding_rounds"]) == 1


def test_manual_rounds_are_confirmed_by_default_web_research_is_not():
    company_id = db.add_company("Acme Inc", "", False, [])

    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "manual", None)
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-02-01", None, "web_research", "https://example.com")

    rounds = {r["source"]: r["status"] for r in db.get_company(company_id)["funding_rounds"]}
    assert rounds["manual"] == "confirmed"
    assert rounds["web_research"] == "unconfirmed"


def test_confirm_round_updates_status():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "web_research", "https://example.com")
    round_id = db.get_company(company_id)["funding_rounds"][0]["id"]

    db.confirm_round(round_id)

    assert db.get_company(company_id)["funding_rounds"][0]["status"] == "confirmed"


def test_add_note_and_notes_count():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_note(company_id, "Heard they're raising a seed round")

    company = db.get_company(company_id)
    assert len(company["notes"]) == 1
    assert db.get_notes_count(company_id) == 1


def test_get_companies_lists_latest_round_and_notes_count():
    company_id = db.add_company("Acme Inc", "", False, [{"name": "Jane Doe", "class_year": 2018}])
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "manual", None)
    db.add_funding_round(company_id, "Series A", 5_000_000, "2026-06-01", None, "manual", None)
    db.add_note(company_id, "note")

    companies = db.get_companies()
    assert len(companies) == 1
    assert companies[0]["latest_round"]["round_type"] == "Series A"
    assert companies[0]["notes_count"] == 1
