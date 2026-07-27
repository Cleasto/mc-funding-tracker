"""Tests for research.parse_funding_update. No real network/API calls —
the Anthropic client is monkeypatched with a fake that returns canned responses."""
import pytest

from mc_funding_tracker import research


class _FakeBlock:
    def __init__(self, type_, name=None, input=None):
        self.type = type_
        self.name = name
        self.input = input


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
