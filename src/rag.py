import os
import chromadb
from chromadb.utils import embedding_functions

class RAGSearch:
    def __init__(self, db_path: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name="personal_knowledge")
        
    def add_note(self, filepath: str, title: str, content: str, url: str = None, tags: list = None):
        """Adds a processed note to the vector database."""
        metadata = {"filepath": filepath, "title": title}
        if url:
            metadata["url"] = url
        if tags:
            metadata["tags"] = ",".join(tags)
            
        self.collection.add(
            documents=[content],
            metadatas=[metadata],
            ids=[filepath]
        )
        
    def search(self, query: str, n_results: int = 3) -> list:
        """Searches the vector database for relevant notes."""
        if self.collection.count() == 0:
            return []
            
        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count())
        )
        
        docs = results['documents'][0]
        metadatas = results['metadatas'][0]
        
        output = []
        for doc, meta in zip(docs, metadatas):
            output.append({
                "title": meta.get("title", "Untitled"),
                "filepath": meta.get("filepath", ""),
                "content_snippet": doc[:300] + "..."
            })
            
        return output
