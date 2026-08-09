"""Research orchestration: Claude-driven web search for funding news.

SEC EDGAR Form D lookup (edgar.py) is no longer run as part of this pipeline —
in practice it rarely surfaced anything useful for these companies and mostly
added noise (including outright wrong-company matches from its fuzzy
company-name search). The module and its tests are left in place in case it's
worth revisiting later; run_research() just doesn't call it anymore.

Results write into the funding_rounds table via db.add_funding_round(), which
dedupes on (company_id, source, round_type, announced_date, amount_usd) and
defaults web-sourced rows to status='unconfirmed' so they get reviewed before
being treated as fact.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import anthropic

from . import db

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

    # Runs on a background thread (see jobs.py), so there's no browser-facing
    # pressure to keep this short — a multi-search research pass can legitimately
    # take a couple of minutes. max_tokens=8192 below means large/noisy companies
    # can legitimately take longer than 180s to generate (non-streaming, so httpx's
    # read timeout is one long wait for the full response) — give it more room.
    client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=1)
    model = config.get("claude_model", "claude-sonnet-5")

    founder_clause = f", founded by {founder_names}," if founder_names else ""
    prompt = (
        f'Research the fundraising history for the company "{company_name}"{founder_clause}. '
        "This is about its funding history, not its current status — report its private "
        "funding rounds even if it has since IPO'd, been acquired, or grown into a large, "
        "well-known company. Being public or well-established now doesn't make its earlier "
        "venture rounds any less relevant; do not treat that as a reason to report nothing. "
        "Start by searching for a funding-history aggregator page for this company — e.g. its "
        "Crunchbase, Tracxn, PitchBook, or CB Insights profile — since these compile a company's "
        "entire round-by-round funding history in one place. For an established company with many "
        "rounds, one good aggregator page can cover everything and is far more efficient than "
        "searching for each round's news coverage individually; only fall back to searching for "
        "individual press releases/news articles per round if no aggregator page turns up, or to "
        "confirm/fill in specifics an aggregator left vague. "
        "A search can return many results, especially for a large or heavily-covered company — "
        "don't try to review all of them. Look at the top 2-3 most relevant results per search "
        "(the aggregator profile itself, or clearly on-topic press coverage) and move on; skip "
        "results that are duplicates, off-topic, or about a different company with a similar name. "
        "Never repeat the same or near-identical query — a search engine returns essentially the "
        "same results for the same query, so re-issuing it wastes a search without learning anything "
        "new. If the aggregator page you already found doesn't have enough detail on a specific round, "
        "search for that round specifically (e.g. its round letter, year, or an investor name) rather "
        "than searching the same generic terms again. "
        "Include past rounds, not just recent news — a round from several years ago is just as "
        "relevant here as one announced this week; do not limit yourself to only what's new. "
        "For each distinct funding round you find, note the round type (e.g. Seed, Series A), "
        "the amount raised in USD if disclosed, the announcement date, investors involved, "
        "and the source URL. "
        "You have a maximum of 5 searches — once you've used them (or sooner, if you've already "
        "found what you need), stop searching and call submit_funding_rounds with what you found. "
        "Do not keep issuing new searches after you run out; report based on what you already have "
        "rather than retrying. Call it with an empty rounds list only if you find no funding "
        "history at all."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=8192,
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

    # Log the actual searches performed and what came back, so a "found nothing" result
    # can be debugged from the log later instead of having to manually replay the search
    # to figure out whether the model searched poorly or genuinely found nothing.
    found_rounds: Optional[List[dict]] = None
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "server_tool_use" and getattr(block, "name", None) == "web_search":
            logger.info(f"[{company_name}] web_search query: {block.input.get('query')!r}")
        elif block_type == "web_search_tool_result":
            content = block.content
            if isinstance(content, list):
                urls = [getattr(r, "url", None) for r in content]
                logger.info(f"[{company_name}] web_search returned {len(content)} result(s): {urls}")
            else:
                logger.info(f"[{company_name}] web_search error: {getattr(content, 'error_code', content)}")
        elif block_type == "tool_use" and block.name == "submit_funding_rounds":
            found_rounds = block.input.get("rounds", [])
            logger.info(f"[{company_name}] submit_funding_rounds called with {len(found_rounds)} round(s)")

    if found_rounds is not None:
        return found_rounds

    logger.info(
        f"Claude did not submit structured rounds for {company_name!r} "
        f"(stop_reason={response.stop_reason})"
    )
    return []


PARSE_UPDATE_TOOL = {
    "name": "submit_funding_rounds",
    "description": "Submit the funding round(s) described in the user's free-text update.",
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
                            "description": "e.g. Seed, Series A, Series B, Bridge, Grant",
                        },
                        "amount_usd": {
                            "type": ["integer", "null"],
                            "description": (
                                "Amount raised in USD, ONLY if a specific figure is explicitly "
                                "stated in the text (e.g. '$2M', '2 million dollars'). Null if no "
                                "figure is stated, even if you could estimate a plausible one — "
                                "never guess or infer an amount."
                            ),
                        },
                        "announced_date": {
                            "type": ["string", "null"],
                            "description": (
                                "YYYY-MM-DD if a specific day is given. If only a month/year is "
                                "mentioned (e.g. 'July 2025'), use the first of that month "
                                "(2025-07-01). Null if no date at all is mentioned — never guess "
                                "a date."
                            ),
                        },
                        "investors": {
                            "type": ["string", "null"],
                            "description": "Investor names explicitly named in the text, or null if none are named — never invent investor names.",
                        },
                    },
                    "required": ["round_type"],
                },
            }
        },
        "required": ["rounds"],
    },
}


def parse_funding_update(text: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse a free-text funding update into structured round fields via a forced Claude
    tool call. No web search — this is pure extraction from what the user already typed,
    not a lookup.

    The text may describe multiple rounds at once (e.g. one per line, pasted from a
    PitchBook/Crunchbase deal list) — each is returned as a separate entry."""
    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        raise RuntimeError(
            "Anthropic API key not configured — run `mc-funding-tracker configure --api-key ...` "
            "or set ANTHROPIC_API_KEY."
        )

    client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=1)
    model = config.get("claude_model", "claude-sonnet-5")

    prompt = (
        f'Parse this funding update into structured fields:\n"""\n{text}\n"""\n\n'
        "It may describe a single round or multiple rounds — e.g. one round pasted per "
        "line. Treat each line (or otherwise clearly distinct round mention) as a separate "
        "entry in the rounds array; skip blank lines and anything that isn't describing a "
        "funding round (don't force it into a round entry). "
        "This will be recorded as confirmed, trusted data with no further review, so extract "
        "only what is explicitly stated — do not fill in, estimate, or guess any field that "
        "isn't clearly present in the text. Use null for anything not stated."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            tools=[PARSE_UPDATE_TOOL],
            tool_choice={"type": "tool", "name": "submit_funding_rounds"},
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

    raise RuntimeError("Claude did not return structured data for this update.")


def run_research(company_id: int, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run web research for a company and store results.

    Returns a summary dict: {web_found, web_inserted, errors}.
    """
    company = db.get_company(company_id)
    if company is None:
        raise ValueError(f"No company with id {company_id}")

    founder_names = ", ".join(f["name"] for f in company["founders"])
    summary = {"web_found": 0, "web_inserted": 0, "errors": []}

    logger.info(f"Starting research for company_id={company_id} ({company['name']})")

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
