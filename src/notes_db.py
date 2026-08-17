"""
Single-file SQLite store for the personal knowledge base.

This replaces both halves of the old design — the Obsidian markdown vault and
the JSON embeddings index — with one file, data/notes.db, holding each note's
text, tags, and embedding vector.

Search is hybrid. FTS5 keyword matching always runs, locally and for free;
semantic results from Gemini embeddings are fused in whenever the API is
reachable. A note with no usable embedding — saved during an outage, or indexed
by a since-retired model — is still findable by keyword, so an API failure or a
model swap degrades search instead of silently breaking it. That mattered:
text-embedding-004's retirement took the old search offline without a single
visible error.
"""

import hashlib
import math
import os
import re
import sqlite3
import struct
from datetime import datetime
from typing import NamedTuple

import config
import transient

DB_FILENAME = "notes.db"

# Embedding inputs are capped by the model; notes get truncated to fit.
MAX_EMBED_CHARS = 2048

# How much of the note body a search result shows.
SNIPPET_CHARS = 300

# Reciprocal-rank-fusion constant, from the paper the technique comes from.
# It flattens the top of each ranking enough that one strong keyword hit can't
# bury a genuinely better semantic match.
RRF_K = 60

# How many candidates each half of the search pulls before fusion. Wider than
# the requested page so a note ranked poorly by one method can still be rescued
# by the other.
CANDIDATE_FACTOR = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    url          TEXT,
    source_type  TEXT,
    created_at   TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);

CREATE TABLE IF NOT EXISTS embeddings (
    note_id INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    model   TEXT NOT NULL,
    dims    INTEGER NOT NULL,
    vector  BLOB NOT NULL
);
"""

_WORD_SPLIT = re.compile(r"\W+", re.UNICODE)


def _pack(vector: list[float]) -> bytes:
    """Store a vector as packed float32 — a third the size of JSON floats."""
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _fts_match_query(query: str) -> str | None:
    """
    Turn free text into a safe FTS5 MATCH expression.

    Each word becomes its own quoted phrase, so FTS5 syntax a user might type
    by accident — a bare hyphen, `*`, `AND`, `NEAR` — is matched literally
    instead of rewriting the query or raising a syntax error mid-search.
    """
    tokens = [t for t in _WORD_SPLIT.split(query or "") if t]
    if not tokens:
        return None
    return " OR ".join(f'"{token}"' for token in tokens)


class SearchOutcome(NamedTuple):
    """
    Search results, plus why the semantic half was skipped if it was.

    Hybrid search has a partial-failure state the old all-or-nothing search
    didn't: keyword hits are real results even when embedding the query fails,
    so the reason has to travel alongside them rather than as an exception.
    """

    results: list[dict]
    degraded: str | None = None
    degraded_retryable: bool = False


class NotesDB:
    def __init__(
        self,
        db_path: str = None,
        embedder=None,
        embed_model: str = None,
    ):
        self.db_file = os.path.join(db_path or config.DATA_PATH, DB_FILENAME)
        os.makedirs(os.path.dirname(self.db_file) or ".", exist_ok=True)
        # Injectable so tests (and future backends) don't need a live API key.
        self._embedder = embedder
        self.embed_model = embed_model or config.GEMINI_EMBED_MODEL
        self.fts_enabled = True
        self._init_schema()

    # ── Connection & schema ─────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        # A fresh connection per call: the MCP server can service tools from
        # more than one thread, and SQLite objects can't cross threads.
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self):
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
                    "USING fts5(title, content)"
                )
            except sqlite3.OperationalError:
                # A Python built without FTS5 still gets keyword search, just
                # as a slower LIKE scan. Better than refusing to start.
                self.fts_enabled = False
            conn.commit()
        finally:
            conn.close()

    # ── Embeddings ──────────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector using Gemini."""
        if self._embedder is not None:
            return self._embedder(text)

        from ai import AIEngine  # imported lazily to keep this module testable

        client = AIEngine().client
        response = transient.with_retries(
            lambda: client.models.embed_content(
                model=self.embed_model,
                contents=text[:MAX_EMBED_CHARS],
            )
        )
        return list(response.embeddings[0].values)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            # Vectors from different models live in different spaces and often
            # have different widths. Comparing the overlap — which is what
            # zip() would quietly do — returns a confident-looking number with
            # no meaning behind it, so refuse instead.
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ── Writing ─────────────────────────────────────────────────────────────

    def add_note(
        self,
        title: str,
        content: str,
        url: str = None,
        tags: list = None,
        source_type: str = None,
        created_at: str = None,
    ) -> int:
        """
        Store a note and its embedding, returning the note id.

        Notes are keyed by a hash of title + content, so saving the same note
        twice updates one row instead of adding a near-duplicate. That is what
        makes a retry after an outage safe — the old vault had to delete the
        half-written file to avoid coming back as "Title (1).md".

        created_at defaults to now, and exists so a markdown import can keep
        the note's original date instead of stamping the whole vault today.
        """
        conn = self._connect()
        try:
            note_id = self._upsert_note(
                conn, title, content, url, tags, source_type, created_at
            )

            try:
                vector = self._embed(f"{title}\n{content}")
            except Exception as exc:
                if transient.is_transient(exc):
                    # The Telegram message is still queued, so the retry will
                    # re-save this note from scratch.
                    conn.rollback()
                else:
                    # Permanent failure: the message is consumed either way, so
                    # keep the note. It stays findable by keyword, and
                    # reindex() can embed it once the cause is fixed.
                    conn.commit()
                raise

            conn.execute(
                "INSERT INTO embeddings (note_id, model, dims, vector) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(note_id) DO UPDATE SET "
                "model = excluded.model, dims = excluded.dims, "
                "vector = excluded.vector",
                (note_id, self.embed_model, len(vector), _pack(vector)),
            )
            conn.commit()
            return note_id
        finally:
            conn.close()

    def _upsert_note(
        self,
        conn: sqlite3.Connection,
        title: str,
        content: str,
        url: str,
        tags: list,
        source_type: str,
        created_at: str = None,
    ) -> int:
        title = title or "Untitled"
        content = content or ""
        digest = hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()

        row = conn.execute(
            "SELECT id FROM notes WHERE content_hash = ?", (digest,)
        ).fetchone()

        if row:
            note_id = row["id"]
            # created_at is deliberately left alone: re-saving the same text
            # shouldn't make an old note look new.
            conn.execute(
                "UPDATE notes SET title = ?, content = ?, url = ?, source_type = ? "
                "WHERE id = ?",
                (title, content, url, source_type, note_id),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO notes "
                "(title, content, url, source_type, created_at, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    title,
                    content,
                    url,
                    source_type,
                    created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    digest,
                ),
            )
            note_id = cursor.lastrowid

        conn.execute("DELETE FROM tags WHERE note_id = ?", (note_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?, ?)",
            [(note_id, tag) for tag in (tags or []) if tag],
        )

        if self.fts_enabled:
            # An FTS5 row is replaced rather than updated in place.
            conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
            conn.execute(
                "INSERT INTO notes_fts (rowid, title, content) VALUES (?, ?, ?)",
                (note_id, title, content),
            )

        return note_id

    # ── Counts ──────────────────────────────────────────────────────────────

    def count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        finally:
            conn.close()

    def embedded_count(self) -> int:
        """Notes carrying an embedding from the model currently configured."""
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model = ?", (self.embed_model,)
            ).fetchone()[0]
        finally:
            conn.close()

    def stale_count(self) -> int:
        """Notes whose only embedding came from a different model."""
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model != ?", (self.embed_model,)
            ).fetchone()[0]
        finally:
            conn.close()

    def unembedded_count(self) -> int:
        """
        Notes semantic search cannot reach, whatever the reason.

        Covers both causes at once — never embedded (saved during an outage) and
        embedded by a since-retired model — because to the user they look the
        same: a note that only turns up on an exact word match. Silently
        unreachable notes are how the last search bug stayed hidden.
        """
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM notes n "
                "LEFT JOIN embeddings e ON e.note_id = n.id "
                "WHERE e.note_id IS NULL OR e.model != ?",
                (self.embed_model,),
            ).fetchone()[0]
        finally:
            conn.close()

    # ── Reading ─────────────────────────────────────────────────────────────

    def all_notes(self) -> list[dict]:
        """Every note with its tags, oldest first — used by the exporter."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, title, content, url, source_type, created_at "
                "FROM notes ORDER BY id"
            ).fetchall()
            return [self._with_tags(conn, dict(row)) for row in rows]
        finally:
            conn.close()

    def _with_tags(self, conn: sqlite3.Connection, note: dict) -> dict:
        note["tags"] = [
            row["tag"]
            for row in conn.execute(
                "SELECT tag FROM tags WHERE note_id = ? ORDER BY tag", (note["id"],)
            )
        ]
        return note

    # ── Search ──────────────────────────────────────────────────────────────

    def search(self, query: str, n_results: int = 5) -> SearchOutcome:
        """
        Find notes by keyword and, when Gemini is reachable, by meaning.

        Returns a SearchOutcome so a semantic outage can be reported next to
        the keyword results that did come back, rather than thrown.
        """
        if not (query or "").strip():
            return SearchOutcome([])

        conn = self._connect()
        try:
            if not conn.execute("SELECT 1 FROM notes LIMIT 1").fetchone():
                return SearchOutcome([])

            limit = max(n_results * CANDIDATE_FACTOR, n_results)
            keyword = self._keyword_search(conn, query, limit)

            scored: list[tuple[int, float]] = []
            degraded, retryable = None, False
            try:
                scored = self._semantic_search(conn, self._embed(query), limit)
            except Exception as exc:
                degraded = transient.describe(exc)
                retryable = transient.is_transient(exc)

            # Cosine is kept per note alongside the fused rank: vector search
            # ranks *everything*, so without a visible similarity a query that
            # matches nothing still returns a confident-looking list.
            similarity = dict(scored)

            fused = self._fuse(
                [("keyword", keyword), ("semantic", [nid for nid, _ in scored])]
            )
            results = []
            for note_id, score, matched in fused:
                note = self._hydrate(conn, note_id, score, matched,
                                     similarity.get(note_id))
                # A ranking can only name a row the notes table no longer has if
                # the FTS index has drifted out of step. Skip it rather than
                # failing the whole search on one orphan.
                if note is not None:
                    results.append(note)
                if len(results) == n_results:
                    break

            return SearchOutcome(results, degraded, retryable)
        finally:
            conn.close()

    def _keyword_search(
        self, conn: sqlite3.Connection, query: str, limit: int
    ) -> list[int]:
        if self.fts_enabled:
            match = _fts_match_query(query)
            if not match:
                return []
            rows = conn.execute(
                "SELECT rowid AS id FROM notes_fts WHERE notes_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
            return [row["id"] for row in rows]

        tokens = [t for t in _WORD_SPLIT.split(query) if t]
        if not tokens:
            return []
        clause = " OR ".join("title LIKE ? OR content LIKE ?" for _ in tokens)
        params: list = []
        for token in tokens:
            params += [f"%{token}%", f"%{token}%"]
        rows = conn.execute(
            f"SELECT id FROM notes WHERE {clause} ORDER BY id LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [row["id"] for row in rows]

    def _semantic_search(
        self, conn: sqlite3.Connection, query_vector: list[float], limit: int
    ) -> list[tuple[int, float]]:
        """Rank notes by cosine similarity, returning (note_id, similarity)."""
        rows = conn.execute(
            "SELECT note_id, vector FROM embeddings WHERE model = ?",
            (self.embed_model,),
        ).fetchall()

        scored = [
            (
                self._cosine_similarity(query_vector, _unpack(row["vector"])),
                row["note_id"],
            )
            for row in rows
        ]
        # Ties break on id so results don't reshuffle between identical queries.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [(note_id, score) for score, note_id in scored[:limit]]

    @staticmethod
    def _fuse(
        ranked_lists: list[tuple[str, list[int]]]
    ) -> list[tuple[int, float, list[str]]]:
        """
        Merge rankings by reciprocal rank fusion.

        bm25 relevance and cosine similarity aren't on comparable scales — one
        is unbounded and negative, the other is -1..1 — so the lists are fused
        by position rather than by score.
        """
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}

        for name, note_ids in ranked_lists:
            for rank, note_id in enumerate(note_ids):
                scores[note_id] = scores.get(note_id, 0.0) + 1.0 / (RRF_K + rank + 1)
                matched.setdefault(note_id, []).append(name)

        order = sorted(scores, key=lambda note_id: (-scores[note_id], note_id))
        return [(note_id, scores[note_id], matched[note_id]) for note_id in order]

    def _hydrate(
        self,
        conn: sqlite3.Connection,
        note_id: int,
        score: float,
        matched: list[str],
        similarity: float = None,
    ) -> dict | None:
        row = conn.execute(
            "SELECT id, title, content, url, source_type, created_at "
            "FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None

        body = row["content"] or ""
        snippet = body[:SNIPPET_CHARS]
        if len(body) > SNIPPET_CHARS:
            snippet += "..."

        note = self._with_tags(conn, dict(row))
        return {
            "id": note["id"],
            "title": note["title"],
            "url": note["url"] or None,
            "tags": note["tags"],
            "source_type": note["source_type"],
            "created_at": note["created_at"],
            "score": score,
            "similarity": similarity,
            "matched_by": matched,
            "content_snippet": snippet,
        }

    # ── Reindexing ──────────────────────────────────────────────────────────

    def reindex(self, force: bool = False) -> dict:
        """
        Embed notes that have no usable vector, or every note when forced.

        A vector from a retired model is unusable but indistinguishable from a
        good one by inspection, so the model name is stored with it and
        anything from another model counts as missing.
        """
        conn = self._connect()
        try:
            pending = self._pending_ids(conn, force)
            embedded, failed, outage = 0, [], None

            for note_id in pending:
                row = conn.execute(
                    "SELECT title, content FROM notes WHERE id = ?", (note_id,)
                ).fetchone()
                try:
                    vector = self._embed(f"{row['title']}\n{row['content']}")
                except Exception as exc:
                    if transient.is_transient(exc):
                        # Every remaining note would hit the same outage.
                        outage = transient.describe(exc)
                        break
                    failed.append(f"note #{note_id} ({row['title']}): {exc}")
                    continue

                conn.execute(
                    "INSERT INTO embeddings (note_id, model, dims, vector) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(note_id) DO UPDATE SET "
                    "model = excluded.model, dims = excluded.dims, "
                    "vector = excluded.vector",
                    (note_id, self.embed_model, len(vector), _pack(vector)),
                )
                # Commit per note so an outage halfway through keeps its
                # progress instead of discarding the whole run.
                conn.commit()
                embedded += 1

            return {
                "embedded": embedded,
                "pending": len(pending),
                "failed": failed,
                "outage": outage,
            }
        finally:
            conn.close()

    def _pending_ids(self, conn: sqlite3.Connection, force: bool) -> list[int]:
        if force:
            rows = conn.execute("SELECT id FROM notes ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT n.id FROM notes n "
                "LEFT JOIN embeddings e ON e.note_id = n.id "
                "WHERE e.note_id IS NULL OR e.model != ? "
                "ORDER BY n.id",
                (self.embed_model,),
            ).fetchall()
        return [row["id"] for row in rows]
