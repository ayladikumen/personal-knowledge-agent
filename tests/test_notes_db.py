"""
Tests for the SQLite note store and its hybrid search, with embedding calls
stubbed.

The behaviour worth pinning here is mostly about partial failure: an embedding
outage must not leave a duplicate behind for the Telegram retry, and it must not
take keyword search down with it.
"""

import sqlite3

import pytest
from google.genai import errors as genai_errors

from notes_db import NotesDB


def fake_embedder(text: str) -> list[float]:
    """Deterministic 3-d 'embedding' so similarity ordering is predictable."""
    lowered = text.lower()
    return [
        float(lowered.count("agent")),
        float(lowered.count("css")),
        float(lowered.count("database")),
    ]


def gemini_error(code, status):
    return genai_errors.ServerError(code, {"error": {"status": status}})


@pytest.fixture
def db(tmp_path):
    return NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=fake_embedder,
        embed_model="test-embed-1",
    )


# ── Storage basics ──────────────────────────────────────────────────────────


def test_empty_db_returns_no_results(db):
    assert db.search("anything").results == []
    assert db.count() == 0


def test_note_is_persisted(db, tmp_path):
    note_id = db.add_note("Agent Framework", "about agent workflows")

    assert db.count() == 1
    assert (tmp_path / "data" / "notes.db").exists()
    assert db.all_notes()[0]["id"] == note_id


def test_db_survives_a_restart(db, tmp_path):
    db.add_note("Agent", "agent stuff")

    reopened = NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=fake_embedder,
        embed_model="test-embed-1",
    )

    assert reopened.count() == 1
    assert reopened.search("agent").results[0]["title"] == "Agent"


def test_url_and_tags_round_trip(db):
    db.add_note(
        "Repo", "agent", url="https://github.com/a/b", tags=["py", "ai"]
    )

    result = db.search("agent").results[0]

    assert result["url"] == "https://github.com/a/b"
    assert result["tags"] == ["ai", "py"]  # returned sorted


def test_saving_identical_text_twice_updates_one_note(db):
    """The content hash is the dedup key that makes an outage retry safe."""
    first  = db.add_note("Same", "same body", tags=["one"])
    second = db.add_note("Same", "same body", tags=["two"])

    assert first == second
    assert db.count() == 1
    assert db.all_notes()[0]["tags"] == ["two"]


def test_different_notes_sharing_a_title_are_both_kept(db):
    db.add_note("Same", "first body")
    db.add_note("Same", "second body")

    assert db.count() == 2


def test_created_at_is_not_bumped_by_a_resave(db):
    db.add_note("A", "agent", created_at="2020-01-01 00:00:00")
    db.add_note("A", "agent", url="https://example.com")

    assert db.all_notes()[0]["created_at"] == "2020-01-01 00:00:00"


def test_tags_are_replaced_not_accumulated(db):
    db.add_note("A", "agent", tags=["old"])
    db.add_note("A", "agent", tags=["new"])

    assert db.all_notes()[0]["tags"] == ["new"]


# ── Outage behaviour ────────────────────────────────────────────────────────


def test_a_transient_embedding_failure_saves_nothing(db):
    """The Telegram message stays queued, so a half-saved note would duplicate."""
    def overloaded(text):
        raise gemini_error(503, "UNAVAILABLE")

    db._embedder = overloaded

    with pytest.raises(genai_errors.ServerError):
        db.add_note("Dropped", "agent")

    assert db.count() == 0


def test_a_permanent_embedding_failure_keeps_the_note(db):
    """The message is consumed either way, so the text must survive."""
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key

    with pytest.raises(genai_errors.ClientError):
        db.add_note("Kept", "agent")

    assert db.count() == 1
    assert db.embedded_count() == 0


def test_a_note_saved_during_an_outage_is_still_found_by_keyword(db):
    """This is the whole point of hybrid search — no embedding, still findable."""
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key
    with pytest.raises(genai_errors.ClientError):
        db.add_note("Raspberry Pi Notes", "home assistant on a pi zero")

    db._embedder = fake_embedder
    outcome = db.search("raspberry")

    assert [r["title"] for r in outcome.results] == ["Raspberry Pi Notes"]
    assert outcome.results[0]["matched_by"] == ["keyword"]


def test_retrying_after_an_outage_produces_a_single_note(db):
    """End to end: the same message saved twice must not become two rows."""
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise gemini_error(503, "UNAVAILABLE")
        return fake_embedder(text)

    db._embedder = flaky

    with pytest.raises(genai_errors.ServerError):
        db.add_note("Agent Framework", "agent orchestration")
    db.add_note("Agent Framework", "agent orchestration")

    assert db.count() == 1
    assert db.embedded_count() == 1


# ── Search ──────────────────────────────────────────────────────────────────


def test_keyword_search_finds_a_note_semantics_would_miss(db):
    db.add_note("Zphisher", "a phishing toolkit written in bash")

    assert db.search("zphisher").results[0]["title"] == "Zphisher"


def test_semantic_search_ranks_the_closest_note_first(db):
    db.add_note("Agents", "agent agent agent orchestration")
    db.add_note("Tailwind", "css css utility classes")

    # "styling" shares no words with either note, so only the vectors can rank
    # these — and the fake embedder maps neither. Use a term that embeds.
    results = db.search("css").results

    assert results[0]["title"] == "Tailwind"


def test_both_methods_contribute_to_matched_by(db):
    db.add_note("Agents", "agent orchestration")

    result = db.search("agent").results[0]

    assert sorted(result["matched_by"]) == ["keyword", "semantic"]


def test_semantic_matches_carry_their_cosine_similarity(db):
    """RRF scores aren't interpretable, so the raw similarity has to survive."""
    db.add_note("Agents", "agent")

    result = db.search("agent").results[0]

    assert result["similarity"] == pytest.approx(1.0)


def test_a_keyword_only_match_has_no_similarity(db):
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key
    with pytest.raises(genai_errors.ClientError):
        db.add_note("Zphisher", "a phishing toolkit")

    db._embedder = fake_embedder
    result = db.search("zphisher").results[0]

    assert result["matched_by"] == ["keyword"]
    assert result["similarity"] is None


def test_n_results_limits_output(db):
    for i in range(5):
        db.add_note(f"Note {i}", "agent")

    assert len(db.search("agent", n_results=2).results) == 2


def test_a_semantic_outage_degrades_instead_of_raising(db):
    db.add_note("Raspberry Pi", "home assistant on a pi")

    def overloaded(text):
        raise gemini_error(503, "UNAVAILABLE")

    db._embedder = overloaded
    outcome = db.search("raspberry")

    assert [r["title"] for r in outcome.results] == ["Raspberry Pi"]
    assert "unavailable" in outcome.degraded.lower()
    assert outcome.degraded_retryable is True


def test_a_permanent_search_failure_is_not_marked_retryable(db):
    db.add_note("Raspberry Pi", "home assistant")

    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key
    outcome = db.search("raspberry")

    assert outcome.degraded is not None
    assert outcome.degraded_retryable is False


def test_blank_query_returns_nothing(db):
    db.add_note("A", "agent")

    assert db.search("   ").results == []


def test_fts_operators_in_a_query_are_treated_as_text(db):
    """A stray hyphen or AND used to be FTS5 syntax, and could raise mid-search."""
    db.add_note("Home Assistant", "running on a raspberry pi zero 2 w")

    for query in ["raspberry -pi", "home AND assistant", 'pi "zero', "pi*", "NEAR(a b)"]:
        outcome = db.search(query)
        assert isinstance(outcome.results, list)


def test_short_snippets_are_not_given_an_ellipsis(db):
    db.add_note("Short", "agent")

    assert db.search("agent").results[0]["content_snippet"] == "agent"


def test_long_snippets_are_truncated_with_an_ellipsis(db):
    db.add_note("Long", "agent " + "x" * 600)

    snippet = db.search("agent").results[0]["content_snippet"]

    assert snippet.endswith("...")
    assert len(snippet) == 303


# ── Model changes ───────────────────────────────────────────────────────────


def test_vectors_from_another_model_are_reported_as_stale(db, tmp_path):
    db.add_note("Agents", "agent orchestration")
    assert db.stale_count() == 0

    swapped = NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=fake_embedder,
        embed_model="test-embed-2",
    )

    assert swapped.stale_count() == 1
    assert swapped.embedded_count() == 0


def test_a_stale_note_is_excluded_from_semantic_but_kept_in_keyword(db, tmp_path):
    """
    A retired embedding model is what silently broke the old search. Now the
    note simply falls back to keyword matching.
    """
    db.add_note("Agents", "agent orchestration")

    swapped = NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=fake_embedder,
        embed_model="test-embed-2",
    )

    result = swapped.search("agent").results[0]
    assert result["matched_by"] == ["keyword"]


def test_reindex_rebuilds_stale_vectors(db, tmp_path):
    db.add_note("Agents", "agent orchestration")

    swapped = NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=fake_embedder,
        embed_model="test-embed-2",
    )
    report = swapped.reindex()

    assert report == {"embedded": 1, "pending": 1, "failed": [], "outage": None}
    assert swapped.embedded_count() == 1
    assert swapped.stale_count() == 0


def test_reindex_is_a_no_op_when_everything_is_current(db):
    db.add_note("Agents", "agent orchestration")

    assert db.reindex()["pending"] == 0


def test_reindex_embeds_a_note_that_missed_out(db):
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key
    with pytest.raises(genai_errors.ClientError):
        db.add_note("Kept", "agent")

    db._embedder = fake_embedder
    assert db.reindex()["embedded"] == 1
    assert db.embedded_count() == 1


def test_reindex_stops_on_an_outage_and_keeps_its_progress(db):
    # Save with a permanently failing embedder so all three notes land with no
    # vector at all — otherwise a re-embed can't be told from the original one.
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db._embedder = bad_key
    for i in range(3):
        with pytest.raises(genai_errors.ClientError):
            db.add_note(f"Note {i}", f"agent {i}")
    assert db.embedded_count() == 0

    calls = []

    def dies_after_one(text):
        calls.append(text)
        if len(calls) > 1:
            raise gemini_error(503, "UNAVAILABLE")
        return fake_embedder(text)

    db._embedder = dies_after_one
    report = db.reindex()

    assert report["embedded"] == 1
    assert report["outage"] is not None
    # The one that succeeded is committed, not rolled back with the run.
    assert db.embedded_count() == 1


def test_unembedded_count_covers_both_reasons_a_note_is_unreachable(db, tmp_path):
    """An outage note and a stale-model note look identical to the user."""
    def bad_key(text):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    db.add_note("Embedded", "agent")           # fine
    db._embedder = bad_key
    with pytest.raises(genai_errors.ClientError):
        db.add_note("No vector", "css")        # never embedded
    db._embedder = fake_embedder

    assert db.unembedded_count() == 1

    swapped = NotesDB(
        db_path=str(tmp_path / "data"),
        embedder=fake_embedder,
        embed_model="test-embed-2",
    )

    # Now one is stale and one was never embedded — both unreachable.
    assert swapped.stale_count() == 1
    assert swapped.unembedded_count() == 2


def test_a_rolled_back_save_leaves_no_phantom_in_the_keyword_index(db):
    """
    The note and its FTS row are written together, so a rollback must drop both
    — an orphaned FTS row would rank a note that no longer exists.
    """
    def overloaded(text):
        raise gemini_error(503, "UNAVAILABLE")

    db._embedder = overloaded
    with pytest.raises(genai_errors.ServerError):
        db.add_note("Vanished", "a very distinctive phrase")

    db._embedder = fake_embedder
    outcome = db.search("distinctive")

    assert outcome.results == []
    conn = db._connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_orphaned_keyword_row_is_skipped_rather_than_crashing(db):
    """Defence in depth: a drifted index degrades one result, not the search."""
    db.add_note("Real", "agent")

    conn = db._connect()
    try:
        conn.execute(
            "INSERT INTO notes_fts (rowid, title, content) VALUES (?, ?, ?)",
            (999, "Ghost", "agent"),
        )
        conn.commit()
    finally:
        conn.close()

    assert [r["title"] for r in db.search("agent").results] == ["Real"]


def test_mismatched_vector_widths_score_zero_rather_than_comparing_a_prefix(db):
    """zip() would silently compare the overlap and return a confident number."""
    assert db._cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


# ── Robustness ──────────────────────────────────────────────────────────────


def test_a_corrupt_database_file_is_reported_not_silently_emptied(tmp_path):
    """
    The JSON index used to swallow a corrupt file and come back empty, which
    looked exactly like "you have no notes". SQLite refusing loudly is better.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "notes.db").write_text("this is not a database", encoding="utf-8")

    with pytest.raises(sqlite3.DatabaseError):
        NotesDB(db_path=str(data_dir), embedder=fake_embedder)


def test_tags_survive_a_note_being_deleted_by_cascade(db):
    note_id = db.add_note("A", "agent", tags=["x"])

    conn = db._connect()
    try:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
    finally:
        conn.close()
