import os
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from processor import ContentProcessor
from ai import AIEngine
from storage import StorageManager
from rag import RAGSearch

# Load environment variables
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", "./vault")

# Initialize modules
processor = ContentProcessor()
ai_engine = AIEngine(GEMINI_KEY)
storage = StorageManager(VAULT_PATH)
rag = RAGSearch()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Hello! I am your Personal AI Knowledge Assistant.\n\n"
        "Send me any link, text, or image, and I will summarize it and save it to your Obsidian vault.\n"
        "You can also ask me questions like: '/search AI agents' to search your past notes."
    )
    await update.message.reply_text(welcome_text)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Please provide a search query. Example: /search python tutorials")
        return
        
    await update.message.reply_text(f"🔍 Searching your knowledge base for: '{query}'...")
    
    results = rag.search(query)
    
    if not results:
        await update.message.reply_text("No matching notes found in your vault.")
        return
        
    response = f"📚 Found {len(results)} results:\n\n"
    for idx, res in enumerate(results, 1):
        response += f"{idx}. **{res['title']}**\n"
        response += f"{res['content_snippet']}\n"
        response += f"File: {os.path.basename(res['filepath'])}\n\n"
        
    await update.message.reply_text(response, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
        if update.message.photo:
            await update.message.reply_text("Received a photo. Vision analysis is coming soon!")
        return

    status_msg = await update.message.reply_text("⏳ Processing your message...")
    
    try:
        # 1. Process content (scrape, parse)
        raw_data = processor.process_message(text)
        
        # 2. AI Analysis
        analysis = ai_engine.analyze_content(raw_data['content'], raw_data.get('url'))
        
        # 3. Save to Obsidian
        filepath = storage.save_note(
            title=analysis['title'],
            content=analysis['markdown_content'],
            original_url=raw_data.get('url'),
            tags=analysis.get('tags')
        )
        
        # 4. Add to Vector DB
        rag.add_note(
            filepath=filepath,
            title=analysis['title'],
            content=analysis['markdown_content'],
            url=raw_data.get('url'),
            tags=analysis.get('tags')
        )
        
        # 5. Reply success
        success_msg = (
            f"✅ Saved successfully!\n"
            f"Title: {analysis['title']}\n"
            f"Tags: {', '.join(analysis.get('tags', []))}\n"
            f"File: {os.path.basename(filepath)}"
        )
        await status_msg.edit_text(success_msg)
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing message: {str(e)}")

def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "your_telegram_bot_token_here":
        print("ERROR: Please set TELEGRAM_BOT_TOKEN in .env")
        return
    if not GEMINI_KEY or GEMINI_KEY == "your_gemini_api_key_here":
        print("ERROR: Please set GEMINI_API_KEY in .env")
        return
        
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
