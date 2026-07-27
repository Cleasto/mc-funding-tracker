"""Tests for Form D XML parsing and blocklist filtering. No network calls —
parses a saved sample filing and monkeypatches the network-calling functions."""
from pathlib import Path

from mc_funding_tracker import edgar
from mc_funding_tracker.edgar import parse_form_d_xml

FIXTURE = Path(__file__).parent / "fixtures" / "form_d_sample.xml"


def test_parse_form_d_xml_extracts_expected_fields():
    xml_text = FIXTURE.read_text()
    parsed = parse_form_d_xml(xml_text)

    assert parsed is not None
    assert parsed["entity_name"] == "Notion Labs, Inc."
    assert parsed["announced_date"] == "2020-03-30"
    assert parsed["amount_usd"] == 50000000
    assert parsed["exemption"] == "06b"
    assert "Simon Last" in parsed["related_persons"]
    assert "Akshay Kothari" in parsed["related_persons"]


def test_parse_form_d_xml_handles_garbage_input():
    assert parse_form_d_xml("not xml at all") is None


def test_get_form_d_rounds_skips_blocklisted_ciks(monkeypatch):
    hits = [
        {"cik": "111", "accession_no": "0001", "entity_name": "Right Co", "file_date": "2026-01-01"},
        {"cik": "222", "accession_no": "0002", "entity_name": "Wrong Co", "file_date": "2026-02-01"},
    ]
    filings = {
        "111": {"entity_name": "Right Co", "announced_date": "2026-01-01", "amount_usd": 1000,
                "exemption": "06b", "related_persons": [], "filing_url": "https://sec.gov/111"},
        "222": {"entity_name": "Wrong Co", "announced_date": "2026-02-01", "amount_usd": 2000,
                "exemption": "06b", "related_persons": [], "filing_url": "https://sec.gov/222"},
    }

    monkeypatch.setattr(edgar, "search_form_d", lambda *a, **kw: hits)
    monkeypatch.setattr(edgar, "fetch_form_d_filing", lambda cik, accession_no, contact_email: filings[cik])

    rounds = edgar.get_form_d_rounds("Right Co", "test@example.com", blocked_ciks=["222"])

    assert len(rounds) == 1
    assert rounds[0]["cik"] == "111"
    assert rounds[0]["entity_name"] == "Right Co"
