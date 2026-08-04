import chromadb

import config

SNIPPET_CHARS = 300


class RAGSearch:
    def __init__(self, db_path: str = None):
        self.client = chromadb.PersistentClient(path=db_path or config.CHROMA_PATH)
        self.collection = self.client.get_or_create_collection(
            name="personal_knowledge"
        )

    def count(self) -> int:
        return self.collection.count()

    def add_note(
        self,
        filepath: str,
        title: str,
        content: str,
        url: str = None,
        tags: list = None,
    ):
        """Index a note, replacing any previous entry for the same file."""
        metadata = {"filepath": filepath, "title": title}
        if url:
            metadata["url"] = url
        if tags:
            metadata["tags"] = ",".join(tags)

        # upsert rather than add so re-indexing an existing vault is idempotent.
        self.collection.upsert(
            documents=[content],
            metadatas=[metadata],
            ids=[filepath],
        )

    def search(self, query: str, n_results: int = 3) -> list:
        """Semantically search indexed notes."""
        total = self.collection.count()
        if total == 0:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, total),
        )

        documents = results.get("documents") or [[]]
        metadatas = results.get("metadatas") or [[]]
        distances = results.get("distances") or [[]]

        docs = documents[0] if documents else []
        metas = metadatas[0] if metadatas else []
        dists = distances[0] if distances else [None] * len(docs)

        output = []
        for doc, meta, dist in zip(docs, metas, dists):
            meta = meta or {}
            snippet = (doc or "")[:SNIPPET_CHARS]
            if doc and len(doc) > SNIPPET_CHARS:
                snippet += "..."

            output.append({
                "title": meta.get("title", "Untitled"),
                "filepath": meta.get("filepath", ""),
                "url": meta.get("url"),
                "tags": [t for t in (meta.get("tags") or "").split(",") if t],
                "distance": dist,
                "content_snippet": snippet,
            })

        return output
