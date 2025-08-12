import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    title = getattr(chat, "title", "") or "(нет названия)"
    await update.message.reply_text(f"Chat ID: {chat.id}\nНазвание: {title}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", chatid))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(MessageHandler(filters.ALL, chatid))
    print("ChatID helper is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
