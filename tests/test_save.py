"""
Tests for the save + index step.

Because a transient failure now leaves the Telegram message queued for a retry,
a half-finished save has to leave nothing behind that the retry would duplicate.
"""

import os

import pytest
from google.genai import errors as genai_errors

import mcp_server
from rag import RAGSearch
from storage import StorageManager


def gemini_error(code, status):
    return genai_errors.ServerError(code, {"error": {"status": status}})


ANALYSIS = {
    "title": "Agent Framework",
    "markdown_content": "# Agent Framework\n\nUseful for orchestration.",
    "tags": ["ai", "agents"],
}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "storage", StorageManager(str(tmp_path / "vault")))
    return tmp_path / "vault"


def test_a_successful_save_writes_and_indexes(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(
        mcp_server, "rag",
        RAGSearch(db_path=str(tmp_path / "data"), embedder=lambda t: [1.0, 0.0]),
    )

    result = mcp_server._save(ANALYSIS, "https://example.com")

    assert result["file"] == "Agent Framework.md"
    assert os.listdir(vault) == ["Agent Framework.md"]
    assert mcp_server.rag.count() == 1


def test_an_outage_while_indexing_leaves_no_note_to_duplicate(vault, tmp_path, monkeypatch):
    def overloaded(text):
        raise gemini_error(503, "UNAVAILABLE")

    monkeypatch.setattr(
        mcp_server, "rag", RAGSearch(db_path=str(tmp_path / "data"), embedder=overloaded)
    )

    with pytest.raises(genai_errors.ServerError):
        mcp_server._save(ANALYSIS)

    # The retry re-saves this note from Telegram, so nothing may be left behind.
    assert os.listdir(vault) == []


def test_a_permanent_indexing_failure_keeps_the_note_on_disk(vault, tmp_path, monkeypatch):
    """The message is consumed either way, so the note must survive for reindexing."""
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    monkeypatch.setattr(
        mcp_server, "rag", RAGSearch(db_path=str(tmp_path / "data"), embedder=bad_key)
    )

    with pytest.raises(genai_errors.ClientError):
        mcp_server._save(ANALYSIS)

    assert os.listdir(vault) == ["Agent Framework.md"]


def test_retrying_after_an_outage_produces_a_single_note(vault, tmp_path, monkeypatch):
    """End to end: the same message saved twice must not become 'Title (1).md'."""
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise gemini_error(503, "UNAVAILABLE")
        return [1.0, 0.0]

    monkeypatch.setattr(
        mcp_server, "rag", RAGSearch(db_path=str(tmp_path / "data"), embedder=flaky)
    )

    with pytest.raises(genai_errors.ServerError):
        mcp_server._save(ANALYSIS)
    mcp_server._save(ANALYSIS)

    assert os.listdir(vault) == ["Agent Framework.md"]
    assert mcp_server.rag.count() == 1
