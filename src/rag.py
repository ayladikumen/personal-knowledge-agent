"""
Lightweight vector search using Gemini embeddings and a local JSON file.
No heavy dependencies — replaces ChromaDB entirely.
"""

import os
import json
import math
import google.generativeai as genai

DB_FILENAME = "embeddings.json"


class RAGSearch:
    def __init__(self, db_path: str = "."):
        self.db_file = os.path.join(db_path, DB_FILENAME)
        self.entries = self._load()

    def _load(self) -> list[dict]:
        if os.path.exists(self.db_file):
            with open(self.db_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save(self):
        os.makedirs(os.path.dirname(self.db_file) or ".", exist_ok=True)
        with open(self.db_file, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)

    def _embed(self, text: str) -> list[float]:
        """Generate an embedding vector using Gemini."""
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text[:2048],  # limit input size
        )
        return result["embedding"]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def add_note(self, filepath: str, title: str, content: str, url: str = None, tags: list = None):
        """Embed and store a note."""
        # Skip if already indexed
        if any(e["filepath"] == filepath for e in self.entries):
            return

        embedding = self._embed(f"{title}\n{content}")

        entry = {
            "filepath": filepath,
            "title": title,
            "content": content[:500],  # store a snippet for display
            "url": url or "",
            "tags": tags or [],
            "embedding": embedding,
        }
        self.entries.append(entry)
        self._save()

    def search(self, query: str, n_results: int = 3) -> list[dict]:
        """Search notes by semantic similarity."""
        if not self.entries:
            return []

        query_embedding = self._embed(query)

        scored = []
        for entry in self.entries:
            sim = self._cosine_similarity(query_embedding, entry["embedding"])
            scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, entry in scored[:n_results]:
            results.append({
                "title": entry["title"],
                "filepath": entry["filepath"],
                "content_snippet": entry["content"][:300] + "...",
            })

        return results
