"""
Tests for what search_knowledge_base tells the user when something is down.

Sync and search both run through Gemini, so an outage in one is easy to
misreport as a failure of the other. These pin the wording that keeps the two
apart.
"""

import pytest
from google.genai import errors as genai_errors

import config
import mcp_server


def gemini_error(code, status, message="boom"):
    return genai_errors.ServerError(
        code, {"error": {"status": status, "message": message}}
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "test-token")
    monkeypatch.setattr(config, "GEMINI_KEY", "test-key")


@pytest.fixture
def no_new_messages(monkeypatch):
    monkeypatch.setattr(mcp_server, "_sync", lambda: [])


def test_search_reports_the_reason_a_sync_failed(configured, monkeypatch):
    """A bare 'could not be processed' count leaves the user with nothing to act on."""
    monkeypatch.setattr(mcp_server, "_sync", lambda: [{
        "error": "Gemini is unavailable right now (503 UNAVAILABLE). Nothing was lost",
        "update_id": 1,
        "retryable": True,
    }])
    monkeypatch.setattr(mcp_server.rag, "search", lambda q, n_results=5: [])

    output = mcp_server.search_knowledge_base("agents")

    assert "Gemini" in output
    assert "503 UNAVAILABLE" in output
    assert "Nothing was lost" in output


def test_search_outage_does_not_masquerade_as_an_empty_vault(configured, no_new_messages, monkeypatch):
    """Embedding the query needs Gemini too — that must not read as 'no results'."""
    def down(query, n_results=5):
        raise gemini_error(503, "UNAVAILABLE")

    monkeypatch.setattr(mcp_server.rag, "search", down)

    output = mcp_server.search_knowledge_base("agents")

    assert "Gemini" in output
    assert "notes are intact" in output
    assert "No results found" not in output


def test_a_permanent_search_failure_is_not_called_temporary(configured, no_new_messages, monkeypatch):
    def bad_key(query, n_results=5):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    monkeypatch.setattr(mcp_server.rag, "search", bad_key)

    output = mcp_server.search_knowledge_base("agents")

    assert "Could not search" in output
    assert "try the search again shortly" not in output


def test_empty_index_still_suggests_reindexing(configured, no_new_messages, monkeypatch):
    monkeypatch.setattr(mcp_server.rag, "search", lambda q, n_results=5: [])
    monkeypatch.setattr(mcp_server.rag, "count", lambda: 0)

    output = mcp_server.search_knowledge_base("agents")

    assert "reindex_vault" in output


def test_notes_left_behind_by_a_model_change_are_explained(
    configured, no_new_messages, monkeypatch
):
    """
    A vault full of notes that all return nothing is the confusing case: the
    index is fine, it was just built by an embedding model that no longer
    answers, so the reason has to be said out loud.
    """
    monkeypatch.setattr(mcp_server.rag, "search", lambda q, n_results=5: [])
    monkeypatch.setattr(mcp_server.rag, "count", lambda: 2)
    monkeypatch.setattr(mcp_server.rag, "skipped", 2)

    output = mcp_server.search_knowledge_base("agents")

    assert "different embedding model" in output
    assert "reindex_vault" in output


def test_results_are_returned_when_everything_works(configured, no_new_messages, monkeypatch):
    monkeypatch.setattr(mcp_server.rag, "search", lambda q, n_results=5: [{
        "title": "Agent Framework",
        "filepath": "/vault/a.md",
        "url": "https://github.com/a/b",
        "tags": ["ai"],
        "score": 0.9,
        "content_snippet": "about agents",
    }])

    output = mcp_server.search_knowledge_base("agents")

    assert "Agent Framework" in output
    assert "https://github.com/a/b" in output
