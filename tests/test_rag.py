"""Tests for the JSON + embeddings search index, with embedding calls stubbed."""

import json
import os

import pytest
from google.genai import errors as genai_errors

from rag import RAGSearch


def fake_embedder(text: str) -> list[float]:
    """Deterministic 3-d 'embedding' so similarity ordering is predictable."""
    lowered = text.lower()
    return [
        float(lowered.count("agent")),
        float(lowered.count("css")),
        float(lowered.count("database")),
    ]


@pytest.fixture
def rag(tmp_path):
    return RAGSearch(db_path=str(tmp_path / "data"), embedder=fake_embedder)


def test_empty_index_returns_no_results(rag):
    assert rag.search("anything") == []
    assert rag.count() == 0


def test_note_is_persisted_to_disk(rag, tmp_path):
    rag.add_note("/vault/a.md", "Agent Framework", "about agent workflows")

    saved = json.load(open(tmp_path / "data" / "embeddings.json", encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["filepath"] == "/vault/a.md"
    assert saved[0]["embedding"] == [2.0, 0.0, 0.0]


def test_index_survives_a_restart(rag, tmp_path):
    rag.add_note("/vault/a.md", "Agent", "agent stuff")

    reopened = RAGSearch(db_path=str(tmp_path / "data"), embedder=fake_embedder)

    assert reopened.count() == 1
    assert reopened.search("agent")[0]["title"] == "Agent"


def test_most_similar_note_ranks_first(rag):
    rag.add_note("/vault/agents.md", "Agents", "agent agent agent orchestration")
    rag.add_note("/vault/css.md", "Tailwind", "css css utility classes")

    results = rag.search("css framework")

    assert results[0]["title"] == "Tailwind"
    assert results[1]["title"] == "Agents"


def test_n_results_limits_output(rag):
    for i in range(5):
        rag.add_note(f"/vault/{i}.md", f"Note {i}", "agent")

    assert len(rag.search("agent", n_results=2)) == 2


def test_reindexing_replaces_rather_than_duplicates(rag):
    """The old implementation skipped known filepaths, making reindex a no-op."""
    rag.add_note("/vault/a.md", "Old Title", "database stuff")
    rag.add_note("/vault/a.md", "New Title", "css stuff")

    assert rag.count() == 1
    assert rag.search("css")[0]["title"] == "New Title"


def test_results_carry_url_and_tags(rag):
    rag.add_note(
        "/vault/a.md", "Repo", "agent", url="https://github.com/a/b", tags=["py", "ai"]
    )

    result = rag.search("agent")[0]

    assert result["url"] == "https://github.com/a/b"
    assert result["tags"] == ["py", "ai"]
    assert result["filepath"] == "/vault/a.md"


def test_short_snippets_are_not_given_an_ellipsis(rag):
    rag.add_note("/vault/a.md", "Short", "agent")

    assert rag.search("agent")[0]["content_snippet"] == "agent"


def test_long_snippets_are_truncated_with_an_ellipsis(rag):
    rag.add_note("/vault/a.md", "Long", "agent " + "x" * 600)

    snippet = rag.search("agent")[0]["content_snippet"]

    assert snippet.endswith("...")
    assert len(snippet) == 303


def test_zero_vector_does_not_divide_by_zero(rag):
    rag.add_note("/vault/a.md", "Unrelated", "nothing matching here")

    # Query embeds to [0, 0, 0]; similarity is defined as 0 rather than crashing.
    assert rag.search("nothing matching")[0]["score"] == 0.0


def test_corrupt_index_file_is_ignored_rather_than_fatal(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "embeddings.json").write_text("{not valid json", encoding="utf-8")

    rag = RAGSearch(db_path=str(data_dir), embedder=fake_embedder)

    assert rag.count() == 0
    rag.add_note("/vault/a.md", "Recovered", "agent")
    assert rag.count() == 1


def test_interrupted_save_leaves_no_partial_index(rag, tmp_path):
    rag.add_note("/vault/a.md", "A", "agent")

    # The atomic write should not leave its temp file behind.
    assert os.listdir(tmp_path / "data") == ["embeddings.json"]


# ── Embedding models ────────────────────────────────────────────────────────


def model_not_found(model):
    return genai_errors.ClientError(
        404, {"error": {"status": "NOT_FOUND", "message": f"{model} is not found"}}
    )


class FakeModels:
    """Stands in for client.models, failing for every name but `working`."""

    def __init__(self, working: str, failure=model_not_found):
        self.working = working
        self.failure = failure
        self.asked = []

    def embed_content(self, model, contents):
        self.asked.append(model)
        if model != self.working:
            raise self.failure(model)
        return type("Response", (), {
            "embeddings": [type("E", (), {"values": [0.1, 0.2, 0.3]})()]
        })()


@pytest.fixture
def live_rag(tmp_path, monkeypatch):
    """A RAGSearch that goes through the real model-selection path."""
    def build(working, failure=model_not_found):
        rag = RAGSearch(db_path=str(tmp_path / "data"), model="text-embedding-004")
        models = FakeModels(working, failure)
        monkeypatch.setattr(
            "ai.AIEngine.client",
            property(lambda self: type("Client", (), {"models": models})()),
        )
        return rag, models

    return build


def test_a_retired_embedding_model_falls_through_to_one_that_exists(live_rag):
    """
    A retired model answers 404, not an outage — which is how every note ends
    up failing to index while every other Gemini call still works.
    """
    rag, models = live_rag("gemini-embedding-001")

    rag.add_note("/vault/a.md", "A", "some content")

    assert models.asked == ["text-embedding-004", "gemini-embedding-001"]
    assert rag.count() == 1


def test_the_model_that_answered_is_remembered_for_the_rest_of_the_sync(live_rag):
    rag, models = live_rag("gemini-embedding-001")

    rag.add_note("/vault/a.md", "A", "one")
    rag.add_note("/vault/b.md", "B", "two")

    # Not four calls: the dead model is asked once, not once per note.
    assert models.asked.count("text-embedding-004") == 1
    assert rag.entries[-1]["model"] == "gemini-embedding-001"


def test_a_bad_key_is_not_mistaken_for_a_retired_model(live_rag):
    """Falling through on a 401 would hide the one error worth reporting."""
    def unauthenticated(model):
        return genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    rag, models = live_rag("nothing-works", failure=unauthenticated)

    with pytest.raises(genai_errors.ClientError):
        rag.add_note("/vault/a.md", "A", "content")

    # Stopped at the first model rather than working down the list.
    assert len(models.asked) == 1


def test_notes_embedded_by_a_different_model_are_skipped_not_ranked(rag):
    """
    Vectors of different widths are not comparable. Scoring them anyway
    truncates to the shorter one and ranks noise above real matches.
    """
    rag.add_note("/vault/current.md", "Agents", "agent agent")
    rag.entries.append({
        "filepath": "/vault/stale.md",
        "title": "Stale",
        "content": "agent agent agent",
        "embedding": [0.5] * 768,  # indexed by a different model
        "tags": [],
        "url": "",
    })

    results = rag.search("agent")

    assert [r["title"] for r in results] == ["Agents"]
    assert rag.skipped == 1
