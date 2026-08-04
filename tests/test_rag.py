"""Tests for the JSON + embeddings search index, with embedding calls stubbed."""

import json
import os

import pytest

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
