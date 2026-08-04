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
| `google-generativeai` | Gemini AI (summaries, vision, embeddings) |
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
