# Setup notes

State of this install, and what was changed to get it running.

## Status

| Component | Status |
| --- | --- |
| Source code | Updated to upstream `master` |
| Dependencies | Installed in `venv/` — `google-genai`, `mcp`, `yt-dlp`, `bs4`, `requests`, `python-dotenv` |
| Test suite | 140/140 passing |
| Claude Desktop | `personal-knowledge-base` registered in `claude_desktop_config.json` |
| Storage | `data/notes.db` (SQLite) — note text, tags and embeddings in one file |
| Gemini — chat | Working (`gemini-flash-latest`) |
| Gemini — embeddings | Working (`gemini-embedding-2`, dim 3072) |
| Gemini — full note pipeline | Working (title + tags generated from a test URL) |
| Telegram bot | Token valid, but the network keeps resetting the connection (see below) |

## What was changed

**Code brought up to date.** The clone was sitting at `d9b71dc`, which still used
ChromaDB and `google-generativeai` (end-of-life). Current `master` replaced both
with `google-genai`, and added `src/config.py` plus a test suite.

**Model defaults changed** — `src/config.py` and `.env`:

- `GEMINI_MODEL`: `gemini-2.0-flash` → `gemini-flash-latest`. The `gemini-2.0-*`
  models return `429 RESOURCE_EXHAUSTED` on this key (no quota allocated); the
  `-latest` aliases have quota.
- `GEMINI_EMBED_MODEL`: `gemini-embedding-2`. The old code had
  `text-embedding-004` hardcoded, which now 404s — that model was retired, so
  semantic search failed on every query, silently.

Both are overridable from `.env` rather than hardcoded.

**Storage replaced.** Notes used to be markdown files in `vault/` with a
separate `data/embeddings.json` vector index. Both are now one SQLite file,
`data/notes.db`, holding the text, tags and embeddings together. Search is
hybrid: SQLite FTS5 keyword matching always runs locally, and Gemini embeddings
add semantic ranking when reachable — so an API outage or a retired embedding
model degrades result quality instead of returning nothing. Markdown is now an
import/export format (`import_markdown` / `export_markdown`), not the store.

**Old packages removed** from the venv: `chromadb`, `chroma-hnswlib`,
`google-generativeai`, `python-telegram-bot`.

## Claude Desktop is the Microsoft Store build — this matters

This machine runs Claude Desktop from the Store (MSIX) package, not the standard
installer. MSIX apps get their `%APPDATA%` writes redirected into a private
package folder, so the documented config location is **not** the one this build
reads. The real file is:

```
%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

A config written to `%APPDATA%\Claude` is ignored silently — no error, no log,
the server simply never appears. The giveaway was that `%APPDATA%\Claude` had no
`logs` directory, meaning Claude had never even tried to launch the server.

`tools/configure_claude.py` now writes to every Claude config directory that
actually exists, so both builds are covered. It merges into the existing file —
`coworkUserFilesPath` and `preferences` are preserved.

Re-run it any time with `register-claude.bat`.

## Known issues

**Telegram connectivity.** `getMe` succeeded once, then every later attempt
failed with `ConnectionResetError 10054` — the connection is being cut mid-TLS
rather than refused. That is a network-level block on `api.telegram.org`, not a
bad token. `sync_telegram` will fail the same way until it's routed around; a VPN
or alternate DNS on this machine should clear it. The rest of the pipeline
(Gemini, note writes, search) does not depend on it.

**The bot accepts messages from anyone.** `sync_telegram` pulls every update
from `getUpdates` without checking who sent it, so any stranger who finds the
bot can add notes to this knowledge base. The bot handle is deliberately kept
out of this file for that reason. Filtering on a known chat id would fix it.

## Helper scripts

| Script | What it does |
| --- | --- |
| `setup.bat` | Full install — venv, dependencies, Claude Desktop registration, checks |
| `verify.bat` | Re-runs all checks and writes `setup-log.txt` |
| `register-claude.bat` | Re-registers the server in every Claude Desktop config location |
| `tools/check_setup.py` | Imports, config, and live API checks |
| `tools/probe_models.py` | Lists which Gemini models the key can actually call |
| `tools/diagnose.py` | Dumps the resolved config and Claude Desktop paths |

## Using it

1. Fully quit Claude Desktop (system tray → Quit) and reopen it.
2. Message your bot on Telegram with a link, image, or note.
3. Ask Claude to search your knowledge base, or call `sync_telegram` directly.

Notes land in `data/notes.db` (SQLite): text, tags and embeddings in one file.
Keyword search always works; semantic ranking needs Gemini. If some notes only
match by keyword, run `reindex_notes` to rebuild their embeddings. Use
`export_markdown` to get browsable `.md` files out of the database.
