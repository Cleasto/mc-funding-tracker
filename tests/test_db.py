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
        company_id, "Seed", 2_000_000, "2026-01-15", "Some VC", "internal", None
    )
    second = db.add_funding_round(
        company_id, "Seed", 2_000_000, "2026-01-15", "Some VC", "internal", None
    )

    assert first is True
    assert second is False
    assert len(db.get_company(company_id)["funding_rounds"]) == 1


def test_add_funding_round_backfills_cik_on_existing_row():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(
        company_id, "Form D Offering", 2_000_000, "2026-01-15", None, "sec_edgar", "https://sec.gov/x"
    )

    reinserted = db.add_funding_round(
        company_id, "Form D Offering", 2_000_000, "2026-01-15", None, "sec_edgar", "https://sec.gov/x",
        cik="1234567", matched_entity_name="Acme Inc",
    )

    round_ = db.get_company(company_id)["funding_rounds"][0]
    assert reinserted is False
    assert round_["cik"] == "1234567"
    assert round_["matched_entity_name"] == "Acme Inc"


def test_internal_rounds_are_confirmed_by_default_others_are_not():
    company_id = db.add_company("Acme Inc", "", False, [])

    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "internal", None)
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-02-01", None, "web_research", "https://example.com")
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-03-01", None, "sec_edgar", "https://sec.gov/x", cik="123")

    rounds = {r["source"]: r["status"] for r in db.get_company(company_id)["funding_rounds"]}
    assert rounds["internal"] == "confirmed"
    assert rounds["web_research"] == "unconfirmed"
    assert rounds["sec_edgar"] == "unconfirmed"


def test_confirm_round_updates_status():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "web_research", "https://example.com")
    round_id = db.get_company(company_id)["funding_rounds"][0]["id"]

    db.confirm_round(round_id)

    assert db.get_company(company_id)["funding_rounds"][0]["status"] == "confirmed"


def test_reject_round_blocklists_cik_and_hides_from_latest_round():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(
        company_id, "Seed", 1_000_000, "2026-05-01", None, "sec_edgar", "https://sec.gov/x",
        cik="0001234567", matched_entity_name="Wrong Company Inc",
    )
    round_id = db.get_company(company_id)["funding_rounds"][0]["id"]

    db.reject_round(round_id)

    company = db.get_company(company_id)
    assert company["funding_rounds"][0]["status"] == "rejected"
    assert db.get_latest_round(company_id) is None
    assert db.get_rejected_ciks(company_id) == ["0001234567"]


def test_unreject_round_reverses_status_and_blocklist():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(
        company_id, "Seed", 1_000_000, "2026-05-01", None, "sec_edgar", "https://sec.gov/x",
        cik="0001234567",
    )
    round_id = db.get_company(company_id)["funding_rounds"][0]["id"]
    db.reject_round(round_id)

    db.unreject_round(round_id)

    assert db.get_company(company_id)["funding_rounds"][0]["status"] == "unconfirmed"
    assert db.get_rejected_ciks(company_id) == []


def test_confirm_sec_edgar_round_records_company_cik():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(
        company_id, "Seed", 1_000_000, "2026-05-01", None, "sec_edgar", "https://sec.gov/x",
        cik="0001234567",
    )
    round_id = db.get_company(company_id)["funding_rounds"][0]["id"]

    db.confirm_round(round_id)

    assert db.get_company(company_id)["sec_cik"] == "0001234567"


def test_total_funding_only_counts_confirmed_amounts():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "internal", None)  # confirmed
    db.add_funding_round(company_id, "Series A", 5_000_000, "2026-06-01", None, "web_research", "https://x")  # unconfirmed
    round_id = [
        r["id"] for r in db.get_company(company_id)["funding_rounds"] if r["round_type"] == "Series A"
    ][0]
    db.reject_round(round_id)  # rejected, still shouldn't count even if amount were confirmed

    assert db.get_total_funding(company_id) == 1_000_000


def test_grand_total_sums_across_companies():
    c1 = db.add_company("Acme Inc", "", False, [])
    c2 = db.add_company("Beta Co", "", False, [])
    db.add_funding_round(c1, "Seed", 1_000_000, "2026-01-01", None, "internal", None)
    db.add_funding_round(c2, "Seed", 2_000_000, "2026-01-01", None, "internal", None)
    db.add_funding_round(c2, "Series A", 5_000_000, "2026-06-01", None, "web_research", "https://x")  # unconfirmed

    assert db.get_grand_total_funding() == 3_000_000


def test_add_note_and_notes_count():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_note(company_id, "Heard they're raising a seed round")

    company = db.get_company(company_id)
    assert len(company["notes"]) == 1
    assert db.get_notes_count(company_id) == 1


def test_update_founder_changes_name_and_class_year():
    company_id = db.add_company("Acme Inc", "", False, [{"name": "Jane Doe", "class_year": 2018}])
    founder_id = db.get_company(company_id)["founders"][0]["id"]

    db.update_founder(founder_id, "Jane D. Smith", 2019)

    founder = db.get_company(company_id)["founders"][0]
    assert founder["name"] == "Jane D. Smith"
    assert founder["class_year"] == 2019


def test_update_funding_round_changes_editable_fields():
    company_id = db.add_company("Acme Inc", "", False, [])
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "internal", None)
    round_id = db.get_company(company_id)["funding_rounds"][0]["id"]

    db.update_funding_round(round_id, "Series A", 5_000_000, "2026-06-01", "Acme Ventures")

    round_ = db.get_company(company_id)["funding_rounds"][0]
    assert round_["round_type"] == "Series A"
    assert round_["amount_usd"] == 5_000_000
    assert round_["announced_date"] == "2026-06-01"
    assert round_["investors"] == "Acme Ventures"


def test_get_companies_lists_latest_round():
    company_id = db.add_company("Acme Inc", "", False, [{"name": "Jane Doe", "class_year": 2018}])
    db.add_funding_round(company_id, "Seed", 1_000_000, "2026-01-01", None, "internal", None)
    db.add_funding_round(company_id, "Series A", 5_000_000, "2026-06-01", None, "internal", None)

    companies = db.get_companies()
    assert len(companies) == 1
    assert companies[0]["latest_round"]["round_type"] == "Series A"
