"""
Tests for the save step that sits between the AI and the note store.

The store's own outage handling is covered in test_notes_db.py; what matters
here is that _save wires the analysis into it correctly and reports back what
the sync summary needs.
"""

import pytest
from google.genai import errors as genai_errors

import mcp_server
from notes_db import NotesDB


def gemini_error(code, status):
    return genai_errors.ServerError(code, {"error": {"status": status}})


ANALYSIS = {
    "title": "Agent Framework",
    "markdown_content": "# Agent Framework\n\nUseful for orchestration.",
    "tags": ["ai", "agents"],
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    store = NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=lambda text: [1.0, 0.0],
        embed_model="test-embed-1",
    )
    monkeypatch.setattr(mcp_server, "notes", store)
    return store


def test_a_successful_save_stores_and_embeds(db):
    result = mcp_server._save(ANALYSIS, "https://example.com", "github")

    assert result["title"] == "Agent Framework"
    assert result["type"] == "github"
    assert result["id"] == 1
    assert db.count() == 1
    assert db.embedded_count() == 1


def test_the_analysis_metadata_reaches_the_store(db):
    mcp_server._save(ANALYSIS, "https://example.com", "github")

    stored = db.all_notes()[0]

    assert stored["title"] == "Agent Framework"
    assert stored["content"] == ANALYSIS["markdown_content"]
    assert stored["url"] == "https://example.com"
    assert stored["source_type"] == "github"
    assert stored["tags"] == ["agents", "ai"]


def test_an_outage_while_embedding_leaves_no_note_to_duplicate(db):
    def overloaded(text):
        raise gemini_error(503, "UNAVAILABLE")

    db._embedder = overloaded

    with pytest.raises(genai_errors.ServerError):
        mcp_server._save(ANALYSIS)

    # The retry re-saves this note from Telegram, so nothing may be left behind.
    assert db.count() == 0


def test_a_permanent_failure_keeps_the_note_for_later_indexing(db):
    """The message is consumed either way, so the note must survive."""
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key

    with pytest.raises(genai_errors.ClientError):
        mcp_server._save(ANALYSIS)

    assert db.count() == 1
    assert db.embedded_count() == 0


def test_retrying_after_an_outage_produces_a_single_note(db):
    """End to end: the same message saved twice must not become two notes."""
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise gemini_error(503, "UNAVAILABLE")
        return [1.0, 0.0]

    db._embedder = flaky

    with pytest.raises(genai_errors.ServerError):
        mcp_server._save(ANALYSIS)
    mcp_server._save(ANALYSIS)

    assert db.count() == 1
    assert db.embedded_count() == 1


def test_a_note_with_no_tags_is_reported_as_an_empty_list(db):
    result = mcp_server._save({"title": "Bare", "markdown_content": "text"})

    assert result["tags"] == []
    assert result["type"] is None
