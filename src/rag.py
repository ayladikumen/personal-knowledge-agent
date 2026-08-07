"""
Lightweight vector search using Gemini embeddings and a local JSON file.
No heavy dependencies — replaces ChromaDB entirely.
"""

import json
import math
import os

import config
from transient import with_retries

DB_FILENAME = "embeddings.json"
EMBED_MODEL = "text-embedding-004"

# Embedding inputs are capped by the model; notes get truncated to fit.
MAX_EMBED_CHARS = 2048

# How much of the note body to keep for displaying results.
STORED_SNIPPET_CHARS = 500
SNIPPET_CHARS = 300


class RAGSearch:
    def __init__(self, db_path: str = None, embedder=None):
        self.db_file = os.path.join(db_path or config.DATA_PATH, DB_FILENAME)
        # Injectable so tests (and future backends) don't need a live API key.
        self._embedder = embedder
        self.entries = self._load()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        if not os.path.exists(self.db_file):
            return []
        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # A truncated or hand-edited index shouldn't take the server down;
            # reindex_vault can rebuild it from the vault.
            return []
        return data if isinstance(data, list) else []

    def _save(self):
        os.makedirs(os.path.dirname(self.db_file) or ".", exist_ok=True)
        # Write via a temp file so an interrupted save can't corrupt the index.
        tmp = f"{self.db_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.db_file)

    # ── Embeddings ──────────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector using Gemini."""
        if self._embedder is not None:
            return self._embedder(text)

        from ai import AIEngine  # imported lazily to keep this module testable

        client = AIEngine().client
        response = with_retries(
            lambda: client.models.embed_content(
                model=EMBED_MODEL,
                contents=text[:MAX_EMBED_CHARS],
            )
        )
        return list(response.embeddings[0].values)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ── Index ───────────────────────────────────────────────────────────────

    def count(self) -> int:
        return len(self.entries)

    def add_note(
        self,
        filepath: str,
        title: str,
        content: str,
        url: str = None,
        tags: list = None,
    ):
        """Embed and store a note, replacing any existing entry for that file."""
        entry = {
            "filepath": filepath,
            "title": title,
            "content": (content or "")[:STORED_SNIPPET_CHARS],
            "url": url or "",
            "tags": tags or [],
            "embedding": self._embed(f"{title}\n{content}"),
        }

        # Replace rather than skip, so reindex_vault refreshes stale entries
        # instead of silently doing nothing.
        for i, existing in enumerate(self.entries):
            if existing.get("filepath") == filepath:
                self.entries[i] = entry
                break
        else:
            self.entries.append(entry)

        self._save()

    def search(self, query: str, n_results: int = 3) -> list[dict]:
        """Search notes by semantic similarity."""
        if not self.entries:
            return []

        query_embedding = self._embed(query)

        scored = []
        for entry in self.entries:
            embedding = entry.get("embedding") or []
            scored.append((self._cosine_similarity(query_embedding, embedding), entry))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        results = []
        for score, entry in scored[:n_results]:
            body = entry.get("content", "")
            snippet = body[:SNIPPET_CHARS]
            if len(body) > SNIPPET_CHARS:
                snippet += "..."

            results.append({
                "title": entry.get("title", "Untitled"),
                "filepath": entry.get("filepath", ""),
                "url": entry.get("url") or None,
                "tags": entry.get("tags") or [],
                "score": score,
                "content_snippet": snippet,
            })

        return results
