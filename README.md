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

| Key | Where to get it |
|-----|----------------|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/botfather) on Telegram |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) |

### 3. Connect MCP to your IDE

Add the following to your IDE's MCP config (e.g. Cursor, Claude Desktop, Antigravity):

```json
{
  "mcpServers": {
    "personal-knowledge-base": {
      "command": "python",
      "args": ["C:/Users/doruk/.gemini/antigravity/scratch/personal-knowledge-agent/src/mcp_server.py"]
    }
  }
}
```

That's it. Your AI assistant now has two new tools:

| Tool | What it does |
|------|-------------|
| `sync_telegram` | Pulls all unread messages from Telegram, processes them, saves to vault |
| `search_knowledge_base` | Syncs first, then semantically searches your vault (auto-finds relevant past saves) |

## Usage

1. **On your phone**: See something cool → share it to your Telegram bot.
2. **At your desk**: Open your IDE. Your AI assistant calls `search_knowledge_base("AI agents")` and instantly finds that repo you saved last week.

The `search_knowledge_base` tool automatically syncs Telegram before every search, so you never miss anything.
