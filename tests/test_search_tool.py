"""
Tests for what search_knowledge_base tells the user when something is down.

Sync and search both run through Gemini, so an outage in one is easy to
misreport as a failure of the other. Hybrid search adds a third state — keyword
results came back but the semantic half didn't — which must read as a partial
result, not as a failure or as a clean success.
"""

import pytest
from google.genai import errors as genai_errors

import config
import mcp_server
from notes_db import SearchOutcome


def gemini_error(code, status, message="boom"):
    return genai_errors.ServerError(
        code, {"error": {"status": status, "message": message}}
    )


A_RESULT = {
    "id": 3,
    "title": "Agent Framework",
    "url": "https://github.com/a/b",
    "tags": ["ai"],
    "source_type": "github",
    "created_at": "2026-08-17 12:00:00",
    "score": 0.9,
    "matched_by": ["keyword", "semantic"],
    "content_snippet": "about agents",
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "test-token")
    monkeypatch.setattr(config, "GEMINI_KEY", "test-key")


@pytest.fixture
def no_new_messages(monkeypatch):
    monkeypatch.setattr(mcp_server, "_sync", lambda: [])


@pytest.fixture
def quiet_store(monkeypatch):
    """Neutral counts, so a notice only appears when a test asks for one."""
    monkeypatch.setattr(mcp_server.notes, "unembedded_count", lambda: 0)
    monkeypatch.setattr(mcp_server.notes, "count", lambda: 1)


def stub_search(monkeypatch, outcome):
    monkeypatch.setattr(mcp_server.notes, "search", lambda q, n_results=5: outcome)


def test_search_reports_the_reason_a_sync_failed(configured, quiet_store, monkeypatch):
    """A bare 'could not be processed' count leaves the user with nothing to act on."""
    monkeypatch.setattr(mcp_server, "_sync", lambda: [{
        "error": "Gemini is unavailable right now (503 UNAVAILABLE). Nothing was lost",
        "update_id": 1,
        "retryable": True,
    }])
    stub_search(monkeypatch, SearchOutcome([]))

    output = mcp_server.search_knowledge_base("agents")

    assert "Gemini" in output
    assert "503 UNAVAILABLE" in output
    assert "Nothing was lost" in output


def test_a_semantic_outage_is_reported_alongside_the_results_it_still_found(
    configured, no_new_messages, quiet_store, monkeypatch
):
    """Embedding the query needs Gemini; keyword matching doesn't."""
    stub_search(monkeypatch, SearchOutcome(
        [A_RESULT],
        degraded="Gemini is unavailable right now (503 UNAVAILABLE)",
        degraded_retryable=True,
    ))

    output = mcp_server.search_knowledge_base("agents")

    assert "Agent Framework" in output          # results still shown
    assert "Gemini" in output                   # and the reason is named
    assert "notes are intact" in output
    assert "again shortly" in output
    assert "No results found" not in output


def test_a_permanent_search_failure_is_not_called_temporary(
    configured, no_new_messages, quiet_store, monkeypatch
):
    stub_search(monkeypatch, SearchOutcome(
        [A_RESULT],
        degraded="401 UNAUTHENTICATED",
        degraded_retryable=False,
    ))

    output = mcp_server.search_knowledge_base("agents")

    assert "Semantic search unavailable" in output
    assert "again shortly" not in output


def test_an_unreadable_database_is_reported(configured, no_new_messages, monkeypatch):
    """Keyword search is local, so a failure here isn't an outage to wait out."""
    def broken(query, n_results=5):
        raise gemini_error(503, "UNAVAILABLE")

    monkeypatch.setattr(mcp_server.notes, "search", broken)

    output = mcp_server.search_knowledge_base("agents")

    assert "Could not search" in output


def test_an_empty_knowledge_base_suggests_importing(
    configured, no_new_messages, monkeypatch
):
    stub_search(monkeypatch, SearchOutcome([]))
    monkeypatch.setattr(mcp_server.notes, "count", lambda: 0)
    monkeypatch.setattr(mcp_server.notes, "unembedded_count", lambda: 0)

    output = mcp_server.search_knowledge_base("agents")

    assert "import_markdown" in output


def test_notes_unreachable_by_semantic_search_are_called_out(
    configured, no_new_messages, monkeypatch
):
    """Silently unreachable notes are exactly how the last search bug hid."""
    stub_search(monkeypatch, SearchOutcome([A_RESULT]))
    monkeypatch.setattr(mcp_server.notes, "count", lambda: 4)
    monkeypatch.setattr(mcp_server.notes, "unembedded_count", lambda: 3)

    output = mcp_server.search_knowledge_base("agents")

    assert "3 note(s)" in output
    assert "reindex_notes" in output


def test_results_are_returned_when_everything_works(
    configured, no_new_messages, quiet_store, monkeypatch
):
    stub_search(monkeypatch, SearchOutcome([A_RESULT]))

    output = mcp_server.search_knowledge_base("agents")

    assert "Agent Framework" in output
    assert "https://github.com/a/b" in output
    assert "keyword, semantic" in output
    assert "#3" in output
    assert "Semantic search unavailable" not in output
