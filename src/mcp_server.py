"""
Personal Knowledge Base — MCP Server

This is the single entry point for the entire project.
It exposes five tools to your AI coding assistant via MCP:

  1. sync_telegram   — Pulls all unread Telegram messages, processes them
                       (GitHub, YouTube, images, text), summarizes with Gemini,
                       and saves them into the notes database.

  2. search_knowledge_base — Searches past saves by keyword and by meaning.
                             Automatically syncs Telegram first so you never
                             miss anything.

  3. reindex_notes   — Re-embeds notes that have no usable vector, for after an
                       outage or a change of embedding model.

  4. export_markdown — Writes the database back out as Obsidian-style .md
                       files, for browsing or handing to another tool.

  5. import_markdown — Loads an existing markdown vault into the database.

Notes live in a single SQLite file (data/notes.db) holding the text, tags, and
embeddings together. Markdown is an import/export format, not the store.

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
import markdown_io
import transient
from ai import AIEngine
from notes_db import NotesDB
from processor import ContentProcessor

# Telegram caps getUpdates at 100 per call, so a large backlog needs several
# rounds. This bounds a single sync so one call can't run forever.
MAX_SYNC_BATCHES = 20

# ── Shared instances ────────────────────────────────────────────────────────

processor = ContentProcessor()
ai_engine = AIEngine(config.GEMINI_KEY)
notes     = NotesDB(config.DATA_PATH)

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


def _process_and_save(
    content_text: str, source_url: str = None, source_type: str = None
) -> dict:
    """Run AI analysis and store the result."""
    analysis = ai_engine.analyze_content(content_text, source_url)
    return _save(analysis, source_url, source_type)


def _process_image_and_save(image_bytes: bytes) -> dict:
    """Run Vision AI on an image and store the result."""
    analysis = ai_engine.analyze_image(image_bytes)
    return _save(analysis, None, "image")


def _save(analysis: dict, source_url: str = None, source_type: str = None) -> dict:
    """
    Store one analysed item as a note.

    The note and its embedding are written in a single transaction, so an
    embedding outage can't leave a half-saved note behind for the Telegram
    retry to duplicate. NotesDB.add_note owns that decision — see the comments
    there for why a transient failure rolls back and a permanent one doesn't.
    """
    note_id = notes.add_note(
        title=analysis["title"],
        content=analysis["markdown_content"],
        url=source_url,
        tags=analysis.get("tags"),
        source_type=source_type,
    )

    return {
        "title": analysis["title"],
        "tags": analysis.get("tags", []),
        "id": note_id,
        "type": source_type,
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
    result = _process_and_save(
        raw_data["content"], raw_data.get("url"), raw_data["type"]
    )
    result["type"] = raw_data["type"]
    return result


def _sync() -> list[dict]:
    """
    Pull all unread Telegram messages, process them, and store them.
    Returns a list of results (one per processed message).
    """
    if not config.has_telegram():
        return [{"error": config.setup_hint(["TELEGRAM_BOT_TOKEN"])}]

    results: list[dict] = []
    offset = _read_offset()
    stalled = False

    for _ in range(MAX_SYNC_BATCHES):
        params = {"timeout": 0, "limit": 100}
        if offset:
            params["offset"] = offset

        try:
            updates = _telegram_api("getUpdates", params).get("result", [])
        except Exception as e:
            results.append({
                "error": f"Could not reach Telegram: {transient.describe(e)}",
                "retryable": transient.is_transient(e),
            })
            break

        if not updates:
            break

        for update in updates:
            try:
                result = _handle_update(update)
                if result:
                    results.append(result)
            except Exception as e:
                if transient.is_transient(e):
                    # Confirming this update_id would delete the message from
                    # Telegram's queue, so a passing outage would destroy the
                    # note instead of postponing it. Leave the offset where it
                    # is and stop: Telegram redelivers this message, and every
                    # message behind it, on the next sync.
                    results.append({
                        "error": (
                            f"{transient.describe(e)}. Nothing was lost — this "
                            "message and any after it are still queued in "
                            "Telegram, and the next sync will pick them up."
                        ),
                        "update_id": update["update_id"],
                        "retryable": True,
                    })
                    stalled = True
                    break

                # A permanent failure can't succeed on a retry, so confirm it
                # anyway rather than let it block the queue forever.
                results.append({"error": str(e), "update_id": update["update_id"]})

            # Persist after every message so a later crash can't replay the
            # ones we already saved.
            offset = max(offset, update["update_id"] + 1)
            _write_offset(offset)

        if stalled or len(updates) < 100:
            break

    return results


def _format_sync(results: list[dict]) -> str:
    saved  = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    # "Synced 0 new item(s)" over a list of failures reads like a success.
    if saved:
        lines = [f"Synced {len(saved)} new item(s) into the knowledge base:"]
    elif errors:
        lines = ["Nothing was saved to the knowledge base."]
    else:
        lines = ["Synced 0 new item(s) into the knowledge base:"]

    for item in saved:
        tags = ", ".join(item.get("tags", []))
        lines.append(
            f"  • [{item.get('type')}] {item['title']}  —  tags: {tags}  "
            f"—  note #{item.get('id')}"
        )

    if errors:
        lines.append(f"\n{len(errors)} message(s) failed to process:")
        for err in errors:
            lines.append(f"  ✗ {err['error']}")

        if any(err.get("retryable") for err in errors):
            lines.append(
                "\nThis is a temporary outage, not a lost save. Run "
                "sync_telegram again in a minute to finish the queue."
            )

    return "\n".join(lines)


# ── MCP Server ──────────────────────────────────────────────────────────────

mcp = MCPServer("PersonalKnowledgeBase")


@mcp.tool()
def sync_telegram() -> str:
    """
    Pull all unread messages from the user's Telegram bot, process them
    (GitHub repos, YouTube videos, images, general links, plain text),
    summarize each with AI, and save them to the knowledge base.

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
    Search the user's personal knowledge base for previously saved tools,
    repos, videos, images, or ideas.

    Matches on keywords always, and on meaning as well whenever the embedding
    API is reachable — so a result set still comes back during an outage, just
    ranked less well. Automatically syncs any new Telegram messages first so
    results are fresh.

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
            # Report what actually broke rather than a bare count — a Gemini
            # outage and an unreachable Telegram need different responses.
            for err in (r for r in synced if "error" in r):
                notices.append(f"(Sync issue: {err['error']})")
        except Exception as e:
            notices.append(f"(Sync skipped: {transient.describe(e)})")

    try:
        outcome = notes.search(query, n_results=5)
    except Exception as e:
        # Keyword search is local, so reaching here means the database itself
        # is unreadable — not something waiting a minute will fix.
        notices.append(f"Could not search: {transient.describe(e)}.")
        return "\n".join(notices)

    if outcome.degraded:
        tail = (
            " Run the search again shortly for the full ranking."
            if outcome.degraded_retryable
            else ""
        )
        notices.append(
            f"(Semantic search unavailable: {outcome.degraded}. Your notes are "
            f"intact and keyword matches are shown below.{tail})"
        )

    unreachable = notes.unembedded_count()
    if unreachable:
        notices.append(
            f"({unreachable} note(s) have no usable embedding — saved during an "
            "outage, or indexed by an older model — so they are only reachable "
            "by keyword. Run reindex_notes to rebuild them.)"
        )

    if not outcome.results:
        notices.append(f"No results found for '{query}'.")
        if notes.count() == 0:
            notices.append(
                "The knowledge base is empty. If you have a markdown vault from "
                "an earlier version, run import_markdown to load it."
            )
        return "\n".join(notices)

    lines = notices + [f"Found {len(outcome.results)} result(s) for '{query}':\n"]
    for idx, res in enumerate(outcome.results, 1):
        lines.append(f"--- Result {idx} ---")
        lines.append(f"Title: {res['title']}")
        if res.get("url"):
            lines.append(f"Source: {res['url']}")
        if res.get("tags"):
            lines.append(f"Tags: {', '.join(res['tags'])}")
        matched = ", ".join(res["matched_by"])
        if res.get("similarity") is not None:
            # Vector search ranks every note, so a low similarity is the only
            # signal that a result is a near-miss rather than a real match.
            matched += f" (similarity {res['similarity']:.2f})"
        lines.append(f"Matched by: {matched}")
        lines.append(f"Snippet: {res['content_snippet']}")
        lines.append(f"Note: #{res['id']}  (saved {res['created_at']})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def reindex_notes(force: bool = False) -> str:
    """
    Rebuild embeddings for notes that don't have a usable one.

    Use this after an embedding outage, or after changing GEMINI_EMBED_MODEL —
    vectors from two different models aren't comparable, so notes indexed by
    the old one drop out of semantic search until they're rebuilt.

    Args:
        force: Re-embed every note, not just the ones missing a vector.
    """
    if notes.count() == 0:
        return (
            "There are no notes to index yet. Run sync_telegram to pull saves "
            "from Telegram, or import_markdown to load an existing vault."
        )

    report = notes.reindex(force=force)

    if report["pending"] == 0:
        return (
            f"All {notes.count()} note(s) are already indexed with "
            f"{notes.embed_model}. Nothing to do."
        )

    summary = (
        f"Embedded {report['embedded']} of {report['pending']} note(s) with "
        f"{notes.embed_model}."
    )
    if report["failed"]:
        summary += f"\n{len(report['failed'])} note(s) failed:\n" + "\n".join(
            f"  ✗ {failure}" for failure in report["failed"]
        )
    if report["outage"]:
        summary += (
            f"\nStopped early: {report['outage']}. Notes already embedded are "
            "kept — run reindex_notes again shortly to finish the rest."
        )
    return summary


@mcp.tool()
def export_markdown(destination: str = "") -> str:
    """
    Write every note out as an Obsidian-style markdown file with frontmatter.

    The database stays the source of truth; this is for reading the collection
    in Obsidian, backing it up, or handing it to another tool.

    Args:
        destination: Directory to write into. Defaults to OBSIDIAN_VAULT_PATH.
    """
    dest = destination.strip() or config.VAULT_PATH

    all_notes = notes.all_notes()
    if not all_notes:
        return "There are no notes to export yet."

    written = markdown_io.MarkdownExporter(dest).export(all_notes)
    return f"Exported {len(written)} note(s) to {dest}."


@mcp.tool()
def import_markdown(source: str = "") -> str:
    """
    Load an existing markdown vault into the notes database.

    Each file's frontmatter supplies the title, source URL, tags and original
    date where present. Re-importing the same vault updates the existing notes
    rather than duplicating them, so it is safe to run twice.

    Args:
        source: Directory to read .md files from. Defaults to
                OBSIDIAN_VAULT_PATH.
    """
    src = source.strip() or config.VAULT_PATH

    if not os.path.isdir(src):
        return f"Directory not found: {src}"

    parsed = markdown_io.read_vault(src)
    if not parsed:
        return f"No .md files found in {src}."

    imported, failed, outage = 0, [], None
    for note in parsed:
        try:
            notes.add_note(
                title=note["title"],
                content=note["content"],
                url=note["url"],
                tags=note["tags"],
                source_type=note["source_type"] or "markdown-import",
                created_at=note["created_at"],
            )
            imported += 1
        except Exception as e:
            if transient.is_transient(e):
                # Every remaining note would hit the same outage.
                outage = transient.describe(e)
                break
            failed.append(f"{note['source_file']}: {e}")

    summary = f"Imported {imported} of {len(parsed)} note(s) from {src}."
    if failed:
        summary += f"\n{len(failed)} file(s) failed:\n" + "\n".join(
            f"  ✗ {failure}" for failure in failed
        )
    if outage:
        summary += (
            f"\nStopped early: {outage}. Notes already imported are kept — run "
            "import_markdown again shortly to finish the rest."
        )
    return summary


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    missing = config.missing_keys()
    if missing:
        # stderr, not stdout — stdout is the MCP protocol channel.
        print(config.setup_hint(missing), file=sys.stderr)
    mcp.run()
