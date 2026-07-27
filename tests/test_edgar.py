"""Tests for Form D XML parsing. No network calls — parses a saved sample filing."""
from pathlib import Path

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
