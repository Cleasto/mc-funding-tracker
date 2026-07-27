"""SEC EDGAR Form D lookup. Free, no API key — just a descriptive User-Agent.

Form D is what companies file for Reg D exempt securities offerings (i.e. most
VC/angel rounds), typically within 15 days of the first sale. It gives structured,
official data (offering amount, date, related persons) but no narrative color
(round name, lead investor, valuation) — that's what the web-research side of
research.py is for.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{accession_nodash}/primary_doc.xml"


def _headers(contact_email: str) -> Dict[str, str]:
    contact = contact_email or "no-contact-email-configured@example.com"
    return {"User-Agent": f"mc-funding-tracker {contact}"}


def search_form_d(company_name: str, contact_email: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search EDGAR's full-text search index for Form D filings mentioning a company name.

    Returns a list of {cik, accession_no, entity_name, file_date} dicts, most recent first
    (the API already ranks/orders results; we just cap how many we bother fetching).
    """
    resp = requests.get(
        SEARCH_URL,
        params={"q": f'"{company_name}"', "forms": "D"},
        headers=_headers(contact_email),
        timeout=20,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", {}).get("hits", [])

    results = []
    for hit in hits[:limit]:
        source = hit.get("_source", {})
        ciks = source.get("ciks") or []
        if not ciks or not source.get("adsh"):
            continue
        results.append(
            {
                # Normalized to unpadded (e.g. "1781814" not "0001781814") — the API
                # returns zero-padded CIKs, but URLs and the blocklist use unpadded,
                # so this needs to match consistently everywhere.
                "cik": str(int(ciks[0])),
                "accession_no": source["adsh"],
                "entity_name": (source.get("display_names") or [company_name])[0],
                "file_date": source.get("file_date"),
            }
        )
    return results


def parse_form_d_xml(xml_text: str) -> Optional[Dict[str, Any]]:
    """Parse a Form D primary_doc.xml document body into a plain dict.

    Pulled out from fetch_form_d_filing so it can be unit-tested against a saved
    sample filing without hitting the network.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Could not parse Form D XML")
        return None

    def _text(path: str) -> Optional[str]:
        return root.findtext(path)

    date_of_first_sale = _text("offeringData/typeOfFiling/dateOfFirstSale/value")
    total_offering_amount = _text("offeringData/offeringSalesAmounts/totalOfferingAmount")
    exemption = _text("offeringData/federalExemptionsExclusions/item")

    amount_usd = None
    if total_offering_amount and total_offering_amount.strip().isdigit():
        amount_usd = int(total_offering_amount)

    related_persons = []
    for person in root.findall("relatedPersonsList/relatedPersonInfo"):
        first = person.findtext("relatedPersonName/firstName") or ""
        last = person.findtext("relatedPersonName/lastName") or ""
        name = f"{first} {last}".strip()
        if name:
            related_persons.append(name)

    return {
        "entity_name": _text("primaryIssuer/entityName"),
        "announced_date": date_of_first_sale,
        "amount_usd": amount_usd,
        "exemption": exemption,
        "related_persons": related_persons,
    }


def fetch_form_d_filing(cik: str, accession_no: str, contact_email: str) -> Optional[Dict[str, Any]]:
    """Fetch and parse a single Form D primary_doc.xml filing."""
    accession_nodash = accession_no.replace("-", "")
    cik_nolead = str(int(cik))
    url = ARCHIVE_URL.format(cik_nolead=cik_nolead, accession_nodash=accession_nodash)

    resp = requests.get(url, headers=_headers(contact_email), timeout=20)
    resp.raise_for_status()

    parsed = parse_form_d_xml(resp.text)
    if parsed is None:
        return None
    parsed["filing_url"] = url
    return parsed


def get_form_d_rounds(
    company_name: str,
    contact_email: str,
    blocked_ciks: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search + fetch Form D filings for a company, normalized as funding-round dicts
    ready for db.add_funding_round(**round, source='sec_edgar').

    `blocked_ciks` skips filers previously marked "not this company" — the search
    is a fuzzy company-name text match, so the same wrong company can otherwise
    keep resurfacing on every research run.
    """
    blocked = set(blocked_ciks or [])
    hits = search_form_d(company_name, contact_email)
    rounds = []
    for hit in hits:
        if hit["cik"] in blocked:
            logger.info(f"Skipping blocklisted CIK {hit['cik']} for {company_name!r}")
            continue
        filing = fetch_form_d_filing(hit["cik"], hit["accession_no"], contact_email)
        if filing is None:
            continue
        rounds.append(
            {
                "round_type": f"Form D Offering ({filing['exemption']})" if filing["exemption"] else "Form D Offering",
                "amount_usd": filing["amount_usd"],
                "announced_date": filing["announced_date"] or hit["file_date"],
                "investors": None,
                "source_url": filing["filing_url"],
                "cik": hit["cik"],
                "entity_name": filing["entity_name"],
                "related_persons": filing["related_persons"],
            }
        )
    return rounds
