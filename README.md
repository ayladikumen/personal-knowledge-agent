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
              (README)   (transcript)  (Vision AI)
                    │           │           │
                    └───────────┼───────────┘
                                ▼
                         Gemini AI Summary
                                │
                    ┌───────────┼───────────┐
                    ▼                       ▼
             Obsidian Vault          ChromaDB (RAG)
             (.md files)           (semantic search)
```

**No background processes.** The MCP server only runs while your IDE is open. Telegram holds your messages until then.

## Features

- **Telegram Ingestion** — Share links / images / text from your phone.
- **GitHub** — Fetches and summarizes the README.
- **YouTube** — Extracts video metadata and description.
- **Images** — Gemini Vision analyzes screenshots and photos.
- **AI Summarization** — Every save is analyzed for *why it's useful to you in the future*.
- **Obsidian Vault** — Notes are saved as clean Markdown with YAML frontmatter and tags.
- **Semantic Search** — Ask your AI assistant to search your vault and it will find relevant past saves.
- **MCP Integration** — Your AI coding assistant can directly search your knowledge base while you code.

## Setup

### 1. Clone & install

```bash
git clone https://github.com/ayladikumen/personal-knowledge-agent.git
cd personal-knowledge-agent

python -m venv venv
.\venv\Scripts\activate        # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

### 2. Configure API keys

Copy `.env.example` to `.env` and fill in:

| Key | Required | Where to get it |
|-----|----------|-----------------|
| `TELEGRAM_BOT_TOKEN` | yes | [@BotFather](https://t.me/botfather) on Telegram |
| `GEMINI_API_KEY` | yes | [Google AI Studio](https://aistudio.google.com/) |
| `OBSIDIAN_VAULT_PATH` | no | Where notes are written (default `./vault`) |
| `CHROMA_DB_PATH` | no | Where the search index lives (default `./chroma_db`) |
| `GEMINI_MODEL` | no | Any model your key can access (default `gemini-2.0-flash`) |

### 3. Connect MCP to your IDE

Add the following to your IDE's MCP config (e.g. Cursor, Claude Desktop, Antigravity),
replacing the path with wherever you cloned this repo:

```json
{
  "mcpServers": {
    "personal-knowledge-base": {
      "command": "python",
      "args": ["/absolute/path/to/personal-knowledge-agent/src/mcp_server.py"]
    }
  }
}
```

On Windows use forward slashes, e.g. `C:/Users/you/personal-knowledge-agent/src/mcp_server.py`.
If you installed into a virtualenv, point `command` at that env's Python
(`.../venv/Scripts/python.exe` on Windows, `.../venv/bin/python` elsewhere).

That's it. Your AI assistant now has three new tools:

| Tool | What it does |
|------|-------------|
| `sync_telegram` | Pulls all unread messages from Telegram, processes them, saves to vault |
| `search_knowledge_base` | Syncs first, then semantically searches your vault (auto-finds relevant past saves) |
| `reindex_vault` | Rebuilds the search index from the markdown notes on disk |

## Usage

1. **On your phone**: See something cool → share it to your Telegram bot.
2. **At your desk**: Open your IDE. Your AI assistant calls `search_knowledge_base("AI agents")` and instantly finds that repo you saved last week.

The `search_knowledge_base` tool automatically syncs Telegram before every search, so you never miss anything. If Telegram is unreachable the search still runs against what you already have.

## Project layout

| File | Role |
|------|------|
| `src/mcp_server.py` | Entry point — MCP tools and the Telegram sync loop |
| `src/config.py` | Paths, keys and setup validation |
| `src/processor.py` | Fetches and extracts content from links |
| `src/ai.py` | Gemini summarization and vision, response parsing |
| `src/storage.py` | Writes Obsidian markdown notes |
| `src/rag.py` | ChromaDB indexing and semantic search |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests stub out the network, Gemini and the vector store, so they run
offline and need no API keys.

## Troubleshooting

**Search returns nothing but the vault has notes.** The vector index lives in
`chroma_db/`, separate from the vault. If it was deleted or the vault came from
another machine, run `reindex_vault` to rebuild it.

**"Missing configuration" from every tool.** The `.env` file must sit in the
project root, next to `requirements.txt`. Placeholder values from
`.env.example` count as unset.

**Nothing syncs.** Only one process can poll a Telegram bot at a time. Make
sure no other copy of the server (or another bot framework) is running against
the same token.
