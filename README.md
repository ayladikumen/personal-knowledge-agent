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
                    ┌───────────┼───────────┐
                    ▼                       ▼
             Obsidian Vault         Embeddings JSON
             (.md files)          (semantic search)
```

**No background processes.** The MCP server only runs while your IDE is open. Telegram holds your messages until then.

## Features

- **Telegram Ingestion** — Share links / images / text from your phone
- **GitHub** — Fetches and summarizes the README
- **YouTube** — Extracts video metadata and description
- **Images** — Gemini Vision analyzes screenshots and photos
- **AI Summarization** — Every save is analyzed for *why it's useful to you in the future*
- **Obsidian Vault** — Notes saved as clean Markdown with YAML frontmatter and tags
- **Semantic Search** — Gemini embeddings + cosine similarity (no heavy vector DB)
- **MCP Integration** — Your AI coding assistant searches your vault while you code

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
OBSIDIAN_VAULT_PATH=./vault
```

Only the two keys are required. The rest have sensible defaults, and relative
paths resolve from the project root:

| Key | Required | Default | What it does |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | — | Your bot, from @BotFather |
| `GEMINI_API_KEY` | yes | — | Your key, from Google AI Studio |
| `OBSIDIAN_VAULT_PATH` | no | `./vault` | Where notes are written |
| `DATA_PATH` | no | `./data` | Where the embeddings index lives |
| `GEMINI_MODEL` | no | `gemini-2.0-flash` | Any model your key can access |

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
| `sync_telegram` | Pulls all unread Telegram messages, processes them, saves to vault |
| `search_knowledge_base` | Auto-syncs first, then searches your vault semantically |
| `reindex_vault` | Rebuilds the embeddings index from the markdown notes on disk |

If Telegram is unreachable, `search_knowledge_base` still searches what you
already have rather than failing.

### Outages never lose a save

Both syncing and searching go through Gemini, whose free tier answers
`503 UNAVAILABLE` whenever the model is briefly overloaded. Short blips are
retried automatically. If one outlasts the retries, the sync stops and leaves
the message **in the Telegram queue** — a message is only confirmed once its
note is written and indexed, so running `sync_telegram` again after the outage
picks up exactly where it left off.

---

## Project layout

| File | Role |
|---|---|
| `src/mcp_server.py` | Entry point — MCP tools and the Telegram sync loop |
| `src/config.py` | Paths, keys and setup validation |
| `src/processor.py` | Fetches and extracts content from links |
| `src/ai.py` | Gemini summarization and vision, response parsing |
| `src/storage.py` | Writes Obsidian markdown notes |
| `src/rag.py` | Gemini embeddings, cosine similarity, JSON index |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests stub out the network, Gemini and the embeddings store, so they run
offline and need no API keys.

## Troubleshooting

**Search returns nothing but the vault has notes.** The index lives in
`data/embeddings.json`, separate from the vault. If it was deleted, or the
vault came from another machine, run `reindex_vault` to rebuild it.

**"Missing configuration" from every tool.** The `.env` file must sit in the
project root, next to `requirements.txt`. Placeholder values copied from
`.env.example` count as unset.

**Nothing syncs.** Only one process can poll a Telegram bot at a time. Make
sure no other copy of the server is running against the same token.

**"503 UNAVAILABLE" during a sync.** That is Gemini being briefly overloaded,
not Telegram. Nothing was lost — the messages are still queued. Wait a minute
and run `sync_telegram` again.

**Sync says "up to date" but the vault is empty.** Telegram deletes a message
from its queue once the server confirms it, and versions before the outage
handling above confirmed messages even when saving them failed. Those messages
are gone from Telegram and need to be sent to the bot again; from then on a
failed save keeps its message queued.
