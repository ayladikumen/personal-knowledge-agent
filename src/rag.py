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

# Google retires embedding models, and a retired one answers 404 rather than
# anything that looks like an outage — which is how an index ends up empty
# while every other call still works. So the configured model is tried first
# and the known alternatives after it, rather than the whole sync failing.
FALLBACK_EMBED_MODELS = (
    "gemini-embedding-001",
    "text-embedding-004",
    "models/text-embedding-004",
)

# Embedding inputs are capped by the model; notes get truncated to fit.
MAX_EMBED_CHARS = 2048

# How much of the note body to keep for displaying results.
STORED_SNIPPET_CHARS = 500
SNIPPET_CHARS = 300


def _is_unknown_model(exc: BaseException) -> bool:
    """True if the API is saying the embedding model itself doesn't exist."""
    if getattr(exc, "code", None) == 404:
        return True
    haystack = f"{getattr(exc, 'status', '')} {exc}".lower()
    return "not_found" in haystack or "is not found" in haystack or (
        "not found" in haystack and "model" in haystack
    )


class RAGSearch:
    def __init__(self, db_path: str = None, embedder=None, model: str = None):
        self.db_file = os.path.join(db_path or config.DATA_PATH, DB_FILENAME)
        # Injectable so tests (and future backends) don't need a live API key.
        self._embedder = embedder
        self.model = model or config.EMBED_MODEL
        # How many notes the last search had to ignore because they were
        # embedded by a different model; the search tool turns this into a
        # "run reindex_vault" hint rather than leaving the user with silence.
        self.skipped = 0
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

    def _candidate_models(self) -> list[str]:
        """The configured model first, then the ones known to have existed."""
        candidates = [self.model]
        candidates += [m for m in FALLBACK_EMBED_MODELS if m != self.model]
        return candidates

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector using Gemini."""
        if self._embedder is not None:
            return self._embedder(text)

        from ai import AIEngine  # imported lazily to keep this module testable

        client = AIEngine().client
        candidates = self._candidate_models()

        for index, model in enumerate(candidates):
            try:
                response = with_retries(
                    lambda name=model: client.models.embed_content(
                        model=name,
                        contents=text[:MAX_EMBED_CHARS],
                    )
                )
            except Exception as exc:
                # A model that no longer exists is worth stepping past; a bad
                # key or an outage is not, and must reach the caller intact so
                # the Telegram message stays queued.
                if index < len(candidates) - 1 and _is_unknown_model(exc):
                    continue
                raise
            # Remember what answered so the rest of this sync skips the 404.
            self.model = model
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
        embedding = self._embed(f"{title}\n{content}")
        entry = {
            "filepath": filepath,
            "title": title,
            "content": (content or "")[:STORED_SNIPPET_CHARS],
            "url": url or "",
            "tags": tags or [],
            # Recorded because vectors from different models are not comparable
            # — it is what lets a search say "reindex" instead of ranking noise.
            "model": self.model,
            "embedding": embedding,
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
        self.skipped = 0
        for entry in self.entries:
            embedding = entry.get("embedding") or []
            # Vectors of different lengths come from different embedding
            # models. Comparing them would silently truncate to the shorter of
            # the two and rank noise; a stale entry is better left out until
            # reindex_vault rebuilds it.
            if len(embedding) != len(query_embedding):
                self.skipped += 1
                continue
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
