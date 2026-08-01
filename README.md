# Personal AI Knowledge Agent

This is a Telegram Bot that acts as your personal knowledge assistant. Send it links (GitHub, YouTube, articles) or images from your phone, and it will process them using an LLM to extract the core value, summarize them, and save them as structured Markdown notes in an Obsidian vault.

It also supports semantic search, meaning you can ask the bot "Did I save any repos related to AI agents?" and it will search your past notes.

## Features
- **Telegram Interface**: Easy to use from your phone by just sharing links/images.
- **GitHub Parsing**: Extracts READMEs and repository descriptions.
- **YouTube Parsing**: Extracts transcripts.
- **LLM Summarization**: Uses Google Gemini to understand *why* the link is useful to you.
- **Obsidian Integration**: Saves notes locally in a Markdown vault with tags.
- **Semantic Search**: Built-in RAG (Retrieval-Augmented Generation) using ChromaDB to answer your questions based on your saved notes.

## Setup

1. Clone the repository.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your API keys:
   - Get a Telegram Bot token from [@BotFather](https://t.me/botfather).
   - Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/).
4. Run the bot:
   ```bash
   python src/bot.py
   ```
