# Personal AI Knowledge Agent

A personal knowledge assistant that captures interesting resources from your phone via Telegram and makes them searchable from your AI coding tools via MCP.

## How It Works

```
Phone (Telegram) ──▶ Telegram Cloud (queues messages)
                                │
                                ▼
              IDE opens ──▶ MCP Server wakes up
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
               GitHub      YouTube      Images
              (README)   (metadata)   (Vision AI)
                    │           │           │
                    └───────────┼───────────┘
                                ▼
                         Gemini AI Summary
                                │
                                ▼
                        data/notes.db (SQLite)
                   text + tags + embeddings, one file
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
            FTS5 keyword search     embedding search
              (always works)       (when Gemini is up)
                    └───────────┬───────────┘
                                ▼
                        fused result ranking
```

**No background processes.** The MCP server only runs while your IDE is open. Telegram holds your messages until then.

## Features

- **Telegram Ingestion** — Share links / images / text from your phone
- **GitHub** — Fetches and summarizes the README
- **YouTube** — Extracts video metadata and description
- **Images** — Gemini Vision analyzes screenshots and photos
- **AI Summarization** — Every save is analyzed for *why it's useful to you in the future*
- **One SQLite File** — Note text, tags and embeddings live together in `data/notes.db`
- **Hybrid Search** — SQLite FTS5 keyword matching always runs; Gemini embeddings add
  semantic ranking when reachable, so an API outage degrades search instead of breaking it
- **Markdown In / Out** — Import an existing Obsidian vault, or export the database
  back to `.md` files whenever you want to browse them
- **MCP Integration** — Your AI coding assistant searches your notes while you code

## Dependencies

Only 6 lightweight packages. No bloated vector databases.

| Package | Purpose |
|---|---|
| `google-genai` | Gemini AI (summaries, vision, embeddings) |
| `beautifulsoup4` | Scrape text from web pages |
| `requests` | HTTP calls (Telegram API, GitHub, URLs) |
| `yt-dlp` | YouTube video metadata |
| `python-dotenv` | Load `.env` config |
| `mcp[cli]` | MCP server for IDE integration |

---

## Installation

### Step 1 — Get Your API Keys

You need two keys before installing. Both are free:

| Key | How to get it |
|---|---|
| **Telegram Bot Token** | Open Telegram → search for [@BotFather](https://t.me/botfather) → send `/newbot` → follow the steps → copy the token |
| **Gemini API Key** | Go to [Google AI Studio](https://aistudio.google.com/) → click "Get API Key" → create one → copy it |

### Step 2 — Clone & Install

```bash
git clone https://github.com/ayladikumen/personal-knowledge-agent.git
cd personal-knowledge-agent
```

Create a virtual environment and install:

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Configure

Copy the example env file and paste in your keys:

```bash
cp .env.example .env
```

Then open `.env` in any text editor and fill in:

```
TELEGRAM_BOT_TOKEN=paste_your_telegram_token_here
GEMINI_API_KEY=paste_your_gemini_key_here
```

Only the two keys are required. The rest have sensible defaults, and relative
paths resolve from the project root:

| Key | Required | Default | What it does |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | — | Your bot, from @BotFather |
| `GEMINI_API_KEY` | yes | — | Your key, from Google AI Studio |
| `DATA_PATH` | no | `./data` | Where `notes.db` lives |
| `OBSIDIAN_VAULT_PATH` | no | `./vault` | Markdown import/export directory only |
| `GEMINI_MODEL` | no | `gemini-2.0-flash` | Any model your key can access |
| `GEMINI_EMBED_MODEL` | no | `gemini-embedding-2` | Embedding model for semantic search |

Changing `GEMINI_EMBED_MODEL` makes every stored vector unusable — embeddings
from two models aren't comparable. The model name is recorded with each vector,
so affected notes are reported and stay keyword-searchable; run `reindex_notes`
to rebuild them.

### Step 4 — Connect to Your IDE

Add the MCP server to your IDE config. The exact location depends on your tool:

- **Cursor**: `~/.cursor/mcp.json`
- **Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Antigravity**: Settings → MCP

Add this entry (update the path to match where you cloned the repo):

```json
{
  "mcpServers": {
    "personal-knowledge-base": {
      "command": "python",
      "args": ["C:/path/to/personal-knowledge-agent/src/mcp_server.py"]
    }
  }
}
```

Use forward slashes on Windows too. If you installed into a virtualenv, point
`command` at that env's Python (`.../venv/Scripts/python.exe` on Windows,
`.../venv/bin/python` elsewhere) rather than a bare `python`.

### Step 5 — Start Using It

1. **Save something**: Open Telegram on your phone → send a GitHub link, YouTube URL, image, or text to your bot.
2. **Find it later**: In your IDE, ask your AI assistant something like *"search my knowledge base for AI agent frameworks"*.

That's it. No background services to manage.

---

## MCP Tools Reference

| Tool | What it does |
|---|---|
| `sync_telegram` | Pulls all unread Telegram messages, processes them, saves to `notes.db` |
| `search_knowledge_base` | Auto-syncs first, then searches by keyword and by meaning |
| `reindex_notes` | Re-embeds notes with no usable vector (after an outage or model change) |
| `export_markdown` | Writes every note out as an Obsidian-style `.md` file |
| `import_markdown` | Loads an existing markdown vault into the database |

If Telegram is unreachable, `search_knowledge_base` still searches what you
already have rather than failing.

### Search degrades instead of breaking

Keyword search runs entirely inside SQLite, so it works with no network, no API
key and no quota. Semantic ranking is layered on top when Gemini answers, and
the two rankings are fused by position rather than score — bm25 relevance and
cosine similarity aren't on comparable scales.

That matters because the previous design had a single point of failure: when
Google retired `text-embedding-004`, every embed call started returning 404 and
search silently returned nothing, with no error to notice. Now the same failure
costs you ranking quality and prints why, and each result reports its cosine
similarity so a weak match is visible as one — vector search ranks *every* note,
so it always has something to return.

### Outages never lose a save

Both syncing and searching go through Gemini, whose free tier answers
`503 UNAVAILABLE` whenever the model is briefly overloaded. Short blips are
retried automatically. If one outlasts the retries, the sync stops and leaves
the message **in the Telegram queue** — a message is only confirmed once its
note is written and indexed, so running `sync_telegram` again after the outage
picks up exactly where it left off.

The note and its embedding are written in a single transaction, and notes are
keyed by a hash of their title and body. So a retry after an outage updates the
one row rather than leaving a second near-identical copy behind.

---

## Project layout

| File | Role |
|---|---|
| `src/mcp_server.py` | Entry point — MCP tools and the Telegram sync loop |
| `src/config.py` | Paths, keys and setup validation |
| `src/processor.py` | Fetches and extracts content from links |
| `src/ai.py` | Gemini summarization and vision, response parsing |
| `src/notes_db.py` | SQLite store: notes, tags, embeddings, hybrid search |
| `src/markdown_io.py` | Markdown export and import (frontmatter, filename rules) |

### Database schema

`data/notes.db` holds four tables:

| Table | Contents |
|---|---|
| `notes` | `id`, `title`, `content`, `url`, `source_type`, `created_at`, `content_hash` |
| `tags` | one row per (note, tag), cascading on delete |
| `embeddings` | one row per note: the packed float32 vector plus the model that made it |
| `notes_fts` | FTS5 index over title and content |

It is an ordinary SQLite file, so `sqlite3 data/notes.db` works for ad-hoc
queries.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests stub out the network, Gemini and the embeddings, so they run offline
and need no API keys.

## Troubleshooting

**Search results look unrelated.** Semantic search ranks every note, so it
always returns its closest guesses. Check the `similarity` shown on each result:
genuine matches sit well above unrelated ones, which cluster low.

**Some notes only match by keyword.** They have no embedding — either they were
saved during an outage, or their vector came from a different embedding model.
`search_knowledge_base` reports the count; run `reindex_notes` to rebuild them.

**Migrating from an older version.** Notes used to live as markdown in the
vault, with embeddings in `data/embeddings.json`. Run `import_markdown` once to
pull the vault into `notes.db`; the old `embeddings.json` is no longer read and
can be deleted.

**"Missing configuration" from every tool.** The `.env` file must sit in the
project root, next to `requirements.txt`. Placeholder values copied from
`.env.example` count as unset.

**Nothing syncs.** Only one process can poll a Telegram bot at a time. Make
sure no other copy of the server is running against the same token.

**"503 UNAVAILABLE" during a sync.** That is Gemini being briefly overloaded,
not Telegram. Nothing was lost — the messages are still queued. Wait a minute
and run `sync_telegram` again.

**Sync says "up to date" but there are no notes.** Telegram deletes a message
from its queue once the server confirms it, and versions before the outage
handling above confirmed messages even when saving them failed. Those messages
are gone from Telegram and need to be sent to the bot again; from then on a
failed save keeps its message queued.
