"""Tests for research.parse_funding_update. No real network/API calls —
the Anthropic client is monkeypatched with a fake that returns canned responses."""
import pytest

from mc_funding_tracker import research


class _FakeBlock:
    def __init__(self, type_, name=None, input=None, content=None):
        self.type = type_
        self.name = name
        self.input = input
        self.content = content


class _FakeSearchResult:
    def __init__(self, url):
        self.url = url


class _FakeResponse:
    def __init__(self, content, stop_reason="tool_use"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeClient:
    def __init__(self, response, **kwargs):
        self.messages = _FakeMessages(response)


def test_parse_funding_update_returns_tool_input(monkeypatch):
    parsed = {
        "round_type": "Seed",
        "amount_usd": 2_000_000,
        "announced_date": "2025-07-01",
        "investors": None,
    }
    response = _FakeResponse([_FakeBlock("tool_use", name="submit_funding_round", input=parsed)])
    monkeypatch.setattr(
        research.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response)
    )

    result = research.parse_funding_update(
        "closed a $2M seed round in July 2025", {"anthropic_api_key": "sk-fake"}
    )

    assert result == parsed


def test_parse_funding_update_requires_api_key():
    with pytest.raises(RuntimeError, match="API key not configured"):
        research.parse_funding_update("closed a seed round", {"anthropic_api_key": ""})


def test_parse_funding_update_raises_if_no_tool_use_returned(monkeypatch):
    response = _FakeResponse([_FakeBlock("text")], stop_reason="end_turn")
    monkeypatch.setattr(
        research.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response)
    )

    with pytest.raises(RuntimeError, match="did not return structured data"):
        research.parse_funding_update("closed a seed round", {"anthropic_api_key": "sk-fake"})


def test_search_web_for_funding_logs_and_extracts_rounds_from_mixed_blocks(monkeypatch):
    """A real response interleaves server_tool_use (the query) and
    web_search_tool_result (the hits) blocks before the final tool_use — make sure
    those don't break extraction and get logged for later debugging."""
    rounds = [{
        "round_type": "Series A",
        "amount_usd": 10_000_000,
        "announced_date": "2022-08-10",
        "investors": "Norwest Venture Partners",
        "source_url": "https://techcrunch.com/example",
    }]
    response = _FakeResponse([
        _FakeBlock("server_tool_use", name="web_search", input={"query": "Acme funding"}),
        _FakeBlock("web_search_tool_result", content=[_FakeSearchResult("https://example.com/a")]),
        _FakeBlock("tool_use", name="submit_funding_rounds", input={"rounds": rounds}),
    ])
    monkeypatch.setattr(
        research.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response)
    )

    result = research.search_web_for_funding("Acme", "Jane Doe", {"anthropic_api_key": "sk-fake"})

    assert result == rounds


def test_search_web_for_funding_handles_web_search_error_block(monkeypatch):
    """web_search_tool_result.content can be an error object (not a list) — must not crash."""
    response = _FakeResponse([
        _FakeBlock("server_tool_use", name="web_search", input={"query": "Acme funding"}),
        _FakeBlock("web_search_tool_result", content=type("Err", (), {"error_code": "max_uses_exceeded"})()),
        _FakeBlock("tool_use", name="submit_funding_rounds", input={"rounds": []}),
    ])
    monkeypatch.setattr(
        research.anthropic, "Anthropic", lambda **kwargs: _FakeClient(response)
    )

    result = research.search_web_for_funding("Acme", "", {"anthropic_api_key": "sk-fake"})

    assert result == []
