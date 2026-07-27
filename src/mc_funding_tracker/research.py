"""Research orchestration: SEC EDGAR Form D lookup + Claude-driven web search.

Both sources write into the same funding_rounds table via db.add_funding_round(),
which dedupes on (company_id, source, round_type, announced_date, amount_usd) and
defaults web-sourced rows to status='unconfirmed' so they get reviewed before being
treated as fact.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import anthropic

from . import db, edgar

logger = logging.getLogger(__name__)

SUBMIT_ROUNDS_TOOL = {
    "name": "submit_funding_rounds",
    "description": "Submit the funding rounds found for this company based on web search results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rounds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "round_type": {
                            "type": "string",
                            "description": "e.g. Seed, Series A, Series B, Bridge",
                        },
                        "amount_usd": {
                            "type": ["integer", "null"],
                            "description": "Total amount raised in USD, or null if undisclosed",
                        },
                        "announced_date": {
                            "type": ["string", "null"],
                            "description": "YYYY-MM-DD if known, otherwise null",
                        },
                        "investors": {
                            "type": ["string", "null"],
                            "description": "Comma-separated investor names, or null if unknown",
                        },
                        "source_url": {
                            "type": "string",
                            "description": "URL of the article/press release this came from",
                        },
                    },
                    "required": ["round_type", "source_url"],
                },
            }
        },
        "required": ["rounds"],
    },
}


def search_web_for_funding(company_name: str, founder_names: str, config: Dict[str, Any]) -> List[dict]:
    """Ask Claude to search the web for funding news and return structured rounds."""
    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        raise RuntimeError(
            "Anthropic API key not configured — run `mc-funding-tracker configure --api-key ...` "
            "or set ANTHROPIC_API_KEY."
        )

    client = anthropic.Anthropic(api_key=api_key, timeout=90.0, max_retries=1)
    model = config.get("claude_model", "claude-sonnet-5")

    founder_clause = f", founded by {founder_names}," if founder_names else ""
    prompt = (
        f'Research recent fundraising for the startup "{company_name}"{founder_clause} '
        "by searching the web for funding announcements, press releases, and news coverage. "
        "For each distinct funding round you find, note the round type (e.g. Seed, Series A), "
        "the amount raised in USD if disclosed, the announcement date, investors involved, "
        "and the source URL. Once you've finished searching, call submit_funding_rounds with "
        "everything you found — call it with an empty rounds list if you find nothing."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=[
                {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
                SUBMIT_ROUNDS_TOOL,
            ],
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.NotFoundError as e:
        raise RuntimeError(
            f"Claude model '{model}' was not found (HTTP 404) — it has likely been retired "
            "or renamed. Update claude_model in ~/.config/mc-funding-tracker/config.yaml."
        ) from e

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_funding_rounds":
            return block.input.get("rounds", [])

    logger.info(
        f"Claude did not submit structured rounds for {company_name!r} "
        f"(stop_reason={response.stop_reason})"
    )
    return []


def run_research(company_id: int, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run SEC EDGAR + web research for a company and store results.

    Returns a summary dict: {edgar_found, edgar_inserted, web_found, web_inserted, errors}.
    """
    company = db.get_company(company_id)
    if company is None:
        raise ValueError(f"No company with id {company_id}")

    founder_names = ", ".join(f["name"] for f in company["founders"])
    summary = {"edgar_found": 0, "edgar_inserted": 0, "web_found": 0, "web_inserted": 0, "errors": []}

    logger.info(f"Starting research for company_id={company_id} ({company['name']})")

    try:
        blocked_ciks = db.get_rejected_ciks(company_id)
        edgar_rounds = edgar.get_form_d_rounds(
            company["name"], config.get("sec_contact_email", ""), blocked_ciks=blocked_ciks
        )
        summary["edgar_found"] = len(edgar_rounds)
        for r in edgar_rounds:
            inserted = db.add_funding_round(
                company_id=company_id,
                round_type=r["round_type"],
                amount_usd=r["amount_usd"],
                announced_date=r["announced_date"],
                investors=r["investors"],
                source="sec_edgar",
                source_url=r["source_url"],
                cik=r["cik"],
                matched_entity_name=r["entity_name"],
            )
            if inserted:
                summary["edgar_inserted"] += 1
    except Exception as e:
        logger.exception(f"SEC EDGAR lookup failed for {company['name']}")
        summary["errors"].append(f"SEC EDGAR: {e}")

    try:
        web_rounds = search_web_for_funding(company["name"], founder_names, config)
        summary["web_found"] = len(web_rounds)
        for r in web_rounds:
            inserted = db.add_funding_round(
                company_id=company_id,
                round_type=r.get("round_type"),
                amount_usd=r.get("amount_usd"),
                announced_date=r.get("announced_date"),
                investors=r.get("investors"),
                source="web_research",
                source_url=r.get("source_url"),
            )
            if inserted:
                summary["web_inserted"] += 1
    except Exception as e:
        logger.exception(f"Web research failed for {company['name']}")
        summary["errors"].append(f"Web research: {e}")

    logger.info(f"Finished research for company_id={company_id}: {summary}")
    return summary
