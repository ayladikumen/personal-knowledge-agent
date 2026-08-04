"""
Personal Knowledge Base — MCP Server

This is the single entry point for the entire project.
It exposes two tools to your AI coding assistant via MCP:

  1. sync_telegram   — Pulls all unread Telegram messages, processes them
                       (GitHub, YouTube, images, text), summarizes with Gemini,
                       and saves them as Obsidian markdown notes.

  2. search_knowledge_base — Semantically searches your vault for past saves.
                             Automatically syncs Telegram first so you never
                             miss anything.

No background bot needed. The MCP server only runs while your IDE is open.
"""

import os
import sys
import json
import requests as http_requests

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Ensure sibling modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from processor import ContentProcessor
from ai import AIEngine
from storage import StorageManager
from rag import RAGSearch

# ── Config ──────────────────────────────────────────────────────────────────

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_KEY      = os.getenv("GEMINI_API_KEY", "")
VAULT_PATH      = os.getenv("OBSIDIAN_VAULT_PATH",
                             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vault"))
OFFSET_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".telegram_offset")

# ── Shared instances ────────────────────────────────────────────────────────

processor  = ContentProcessor()
ai_engine  = AIEngine(GEMINI_KEY)
storage    = StorageManager(VAULT_PATH)
rag        = RAGSearch(db_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))

# ── Telegram helpers ────────────────────────────────────────────────────────

def _read_offset() -> int:
    """Read the last processed Telegram update_id so we never reprocess."""
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE, "r") as f:
            try:
                return int(f.read().strip())
            except ValueError:
                return 0
    return 0


def _write_offset(offset: int):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def _telegram_api(method: str, params: dict = None) -> dict:
    """Call the Telegram Bot API directly via HTTP."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = http_requests.get(url, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def _download_telegram_file(file_id: str) -> bytes:
    """Download a file (photo) from Telegram by file_id."""
    info = _telegram_api("getFile", {"file_id": file_id})
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    r = http_requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content


def _process_and_save(content_text: str, source_url: str = None) -> dict:
    """Run AI analysis, save to vault, index in vector DB."""
    analysis = ai_engine.analyze_content(content_text, source_url)

    filepath = storage.save_note(
        title=analysis["title"],
        content=analysis["markdown_content"],
        original_url=source_url,
        tags=analysis.get("tags"),
    )

    rag.add_note(
        filepath=filepath,
        title=analysis["title"],
        content=analysis["markdown_content"],
        url=source_url,
        tags=analysis.get("tags"),
    )

    return {
        "title": analysis["title"],
        "tags": analysis.get("tags", []),
        "file": os.path.basename(filepath),
    }


def _process_image_and_save(image_bytes: bytes) -> dict:
    """Run Vision AI on an image, save to vault, index in vector DB."""
    analysis = ai_engine.analyze_image(image_bytes)

    filepath = storage.save_note(
        title=analysis["title"],
        content=analysis["markdown_content"],
        tags=analysis.get("tags"),
    )

    rag.add_note(
        filepath=filepath,
        title=analysis["title"],
        content=analysis["markdown_content"],
        tags=analysis.get("tags"),
    )

    return {
        "title": analysis["title"],
        "tags": analysis.get("tags", []),
        "file": os.path.basename(filepath),
    }


def _sync() -> list[dict]:
    """
    Pull all unread Telegram messages, process them, and save to vault.
    Returns a list of results (one per processed message).
    """
    if not TELEGRAM_TOKEN:
        return [{"error": "TELEGRAM_BOT_TOKEN is not set in .env"}]

    offset = _read_offset()
    params = {"timeout": 0}
    if offset:
        params["offset"] = offset

    data = _telegram_api("getUpdates", params)
    updates = data.get("result", [])

    if not updates:
        return []

    results = []
    new_offset = offset

    for update in updates:
        new_offset = max(new_offset, update["update_id"] + 1)
        message = update.get("message")
        if not message:
            continue

        try:
            # ── Photo ───────────────────────────────────────────────
            if "photo" in message:
                # Telegram sends multiple sizes; grab the largest
                photo = message["photo"][-1]
                image_bytes = _download_telegram_file(photo["file_id"])
                result = _process_image_and_save(image_bytes)
                result["type"] = "image"
                results.append(result)
                continue

            # ── Text / Link ─────────────────────────────────────────
            text = message.get("text", "")
            if not text:
                continue

            raw_data = processor.process_message(text)
            result = _process_and_save(raw_data["content"], raw_data.get("url"))
            result["type"] = raw_data["type"]
            results.append(result)

        except Exception as e:
            results.append({"error": str(e), "update_id": update["update_id"]})

    _write_offset(new_offset)
    return results


# ── MCP Server ──────────────────────────────────────────────────────────────

mcp = FastMCP("PersonalKnowledgeBase")


@mcp.tool()
def sync_telegram() -> str:
    """
    Pull all unread messages from the user's Telegram bot, process them
    (GitHub repos, YouTube videos, images, general links, plain text),
    summarize each with AI, and save them as Obsidian notes.

    Call this when the user starts a work session or asks you to check
    for new saves.
    """
    results = _sync()

    if not results:
        return "No new messages from Telegram. Your knowledge base is up to date."

    errors  = [r for r in results if "error" in r]
    saved   = [r for r in results if "error" not in r]

    lines = [f"Synced {len(saved)} new item(s) into the knowledge base:\n"]
    for item in saved:
        tags = ", ".join(item.get("tags", []))
        lines.append(f"  • [{item['type']}] {item['title']}  —  tags: {tags}  —  file: {item['file']}")

    if errors:
        lines.append(f"\n{len(errors)} message(s) failed to process:")
        for err in errors:
            lines.append(f"  ✗ {err['error']}")

    return "\n".join(lines)


@mcp.tool()
def search_knowledge_base(query: str) -> str:
    """
    Search the user's personal knowledge base (Obsidian vault) for previously
    saved tools, repos, videos, images, or ideas.

    Automatically syncs any new Telegram messages before searching so results
    are always fresh.

    Use this whenever the user asks about a tool they saved, when you want to
    recommend a resource from their collection, or when building a project that
    might benefit from something they bookmarked earlier.

    Args:
        query: Natural-language search query (e.g. "AI agent framework",
               "React UI library", "that YouTube video about RAG").
    """
    # Sync first so newly sent links are included in results
    sync_summary = _sync()

    results = rag.search(query, n_results=5)

    if not results:
        header = f"No results found for '{query}'."
        if sync_summary:
            header += f"\n(Synced {len([r for r in sync_summary if 'error' not in r])} new item(s) before searching.)"
        return header

    lines = []
    if sync_summary:
        new_count = len([r for r in sync_summary if "error" not in r])
        if new_count:
            lines.append(f"(Synced {new_count} new item(s) from Telegram before searching.)\n")

    lines.append(f"Found {len(results)} result(s) for '{query}':\n")
    for idx, res in enumerate(results, 1):
        lines.append(f"--- Result {idx} ---")
        lines.append(f"Title: {res['title']}")
        lines.append(f"Snippet: {res['content_snippet']}")
        lines.append(f"File: {res['filepath']}")
        lines.append("")

    return "\n".join(lines)


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
