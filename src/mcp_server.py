"""
Personal Knowledge Base — MCP Server

This is the single entry point for the entire project.
It exposes three tools to your AI coding assistant via MCP:

  1. sync_telegram   — Pulls all unread Telegram messages, processes them
                       (GitHub, YouTube, images, text), summarizes with Gemini,
                       and saves them as Obsidian markdown notes.

  2. search_knowledge_base — Semantically searches your vault for past saves.
                             Automatically syncs Telegram first so you never
                             miss anything.

  3. reindex_vault   — Rebuilds the search index from the markdown notes on
                       disk, for when the vector database is lost or moved.

No background bot needed. The MCP server only runs while your IDE is open.
"""

import os
import sys

# Ensure sibling modules are importable when launched by path from an IDE.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests as http_requests

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

import config
from ai import AIEngine
from processor import ContentProcessor
from rag import RAGSearch
from storage import StorageManager

# Telegram caps getUpdates at 100 per call, so a large backlog needs several
# rounds. This bounds a single sync so one call can't run forever.
MAX_SYNC_BATCHES = 20

# ── Shared instances ────────────────────────────────────────────────────────

processor = ContentProcessor()
ai_engine = AIEngine(config.GEMINI_KEY)
storage   = StorageManager(config.VAULT_PATH)
rag       = RAGSearch(config.DATA_PATH)

# ── Telegram helpers ────────────────────────────────────────────────────────


def _read_offset() -> int:
    """Read the last processed Telegram update_id so we never reprocess."""
    if not os.path.exists(config.OFFSET_FILE):
        return 0
    try:
        with open(config.OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return 0


def _write_offset(offset: int):
    with open(config.OFFSET_FILE, "w") as f:
        f.write(str(offset))


def _telegram_api(method: str, params: dict = None) -> dict:
    """Call the Telegram Bot API directly via HTTP."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{method}"
    r = http_requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok", False):
        raise RuntimeError(
            f"Telegram API error on {method}: "
            f"{payload.get('description', 'unknown error')}"
        )
    return payload


def _download_telegram_file(file_id: str) -> bytes:
    """Download a file (photo) from Telegram by file_id."""
    info = _telegram_api("getFile", {"file_id": file_id})
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}/{file_path}"
    r = http_requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def _process_and_save(content_text: str, source_url: str = None) -> dict:
    """Run AI analysis, save to vault, index in vector DB."""
    analysis = ai_engine.analyze_content(content_text, source_url)
    return _save(analysis, source_url)


def _process_image_and_save(image_bytes: bytes) -> dict:
    """Run Vision AI on an image, save to vault, index in vector DB."""
    analysis = ai_engine.analyze_image(image_bytes)
    return _save(analysis, None)


def _save(analysis: dict, source_url: str = None) -> dict:
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


def _handle_update(update: dict) -> dict | None:
    """Process a single Telegram update into a saved note, or None to skip."""
    message = update.get("message") or update.get("channel_post")
    if not message:
        return None

    if "photo" in message:
        # Telegram sends multiple sizes; grab the largest.
        image_bytes = _download_telegram_file(message["photo"][-1]["file_id"])
        result = _process_image_and_save(image_bytes)
        result["type"] = "image"
        return result

    # Captions carry the text when a link is shared alongside media.
    text = message.get("text") or message.get("caption") or ""
    if not text.strip():
        return None

    raw_data = processor.process_message(text)
    result = _process_and_save(raw_data["content"], raw_data.get("url"))
    result["type"] = raw_data["type"]
    return result


def _sync() -> list[dict]:
    """
    Pull all unread Telegram messages, process them, and save to vault.
    Returns a list of results (one per processed message).
    """
    if not config.has_telegram():
        return [{"error": config.setup_hint(["TELEGRAM_BOT_TOKEN"])}]

    results: list[dict] = []
    offset = _read_offset()

    for _ in range(MAX_SYNC_BATCHES):
        params = {"timeout": 0, "limit": 100}
        if offset:
            params["offset"] = offset

        try:
            updates = _telegram_api("getUpdates", params).get("result", [])
        except Exception as e:
            results.append({"error": f"Could not reach Telegram: {e}"})
            break

        if not updates:
            break

        for update in updates:
            try:
                result = _handle_update(update)
                if result:
                    results.append(result)
            except Exception as e:
                results.append({"error": str(e), "update_id": update["update_id"]})

            # Persist after every message so a later crash can't replay the
            # ones we already saved.
            offset = max(offset, update["update_id"] + 1)
            _write_offset(offset)

        if len(updates) < 100:
            break

    return results


def _format_sync(results: list[dict]) -> str:
    saved  = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    lines = [f"Synced {len(saved)} new item(s) into the knowledge base:"]
    for item in saved:
        tags = ", ".join(item.get("tags", []))
        lines.append(
            f"  • [{item['type']}] {item['title']}  —  tags: {tags}  "
            f"—  file: {item['file']}"
        )

    if errors:
        lines.append(f"\n{len(errors)} message(s) failed to process:")
        for err in errors:
            lines.append(f"  ✗ {err['error']}")

    return "\n".join(lines)


# ── MCP Server ──────────────────────────────────────────────────────────────

mcp = MCPServer("PersonalKnowledgeBase")


@mcp.tool()
def sync_telegram() -> str:
    """
    Pull all unread messages from the user's Telegram bot, process them
    (GitHub repos, YouTube videos, images, general links, plain text),
    summarize each with AI, and save them as Obsidian notes.

    Call this when the user starts a work session or asks you to check
    for new saves.
    """
    missing = config.missing_keys()
    if missing:
        return config.setup_hint(missing)

    results = _sync()
    if not results:
        return "No new messages from Telegram. Your knowledge base is up to date."

    return _format_sync(results)


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
    # Sync first so newly sent links are included — but never let a Telegram
    # outage or a bad key stop the user searching what they already have.
    notices = []
    if config.has_telegram() and config.has_gemini():
        try:
            synced = _sync()
            new_count = len([r for r in synced if "error" not in r])
            if new_count:
                notices.append(
                    f"(Synced {new_count} new item(s) from Telegram before searching.)"
                )
            failed = len(synced) - new_count
            if failed:
                notices.append(f"({failed} incoming message(s) could not be processed.)")
        except Exception as e:
            notices.append(f"(Telegram sync skipped: {e})")

    results = rag.search(query, n_results=5)

    if not results:
        notices.append(f"No results found for '{query}'.")
        if rag.count() == 0:
            notices.append(
                "The search index is empty. If your vault already has notes, "
                "run reindex_vault to rebuild the index."
            )
        return "\n".join(notices)

    lines = notices + [f"Found {len(results)} result(s) for '{query}':\n"]
    for idx, res in enumerate(results, 1):
        lines.append(f"--- Result {idx} ---")
        lines.append(f"Title: {res['title']}")
        if res.get("url"):
            lines.append(f"Source: {res['url']}")
        if res.get("tags"):
            lines.append(f"Tags: {', '.join(res['tags'])}")
        lines.append(f"Snippet: {res['content_snippet']}")
        lines.append(f"File: {res['filepath']}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def reindex_vault() -> str:
    """
    Rebuild the semantic search index from the markdown notes in the Obsidian
    vault.

    Use this when search returns nothing even though notes exist — for example
    after the vector database was deleted, or the vault was moved or synced
    from another machine.
    """
    if not os.path.isdir(config.VAULT_PATH):
        return f"Vault directory not found: {config.VAULT_PATH}"

    indexed, failed = 0, []
    for entry in sorted(os.listdir(config.VAULT_PATH)):
        if not entry.endswith(".md"):
            continue
        filepath = os.path.join(config.VAULT_PATH, entry)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            rag.add_note(
                filepath=filepath,
                title=os.path.splitext(entry)[0],
                content=content,
            )
            indexed += 1
        except Exception as e:
            failed.append(f"{entry}: {e}")

    summary = f"Reindexed {indexed} note(s) from {config.VAULT_PATH}."
    if failed:
        summary += f"\n{len(failed)} file(s) failed:\n" + "\n".join(
            f"  ✗ {f}" for f in failed
        )
    return summary


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    missing = config.missing_keys()
    if missing:
        # stderr, not stdout — stdout is the MCP protocol channel.
        print(config.setup_hint(missing), file=sys.stderr)
    mcp.run()
