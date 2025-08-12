
from telegram.ext import ApplicationBuilder, MessageHandler, filters
import os

BOT_TOKEN = os.getenv("BOT_TOKEN") or "PASTE-YOUR-BOT-TOKEN-HERE"

async def print_chat_id(update, context):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat ID этой группы: {chat.id}")
    print(f"Chat ID = {chat.id} (название: {chat.title})")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, print_chat_id))
    print("Бот запущен. Добавьте его в группу и напишите сообщение.")
    app.run_polling()

if __name__ == "__main__":
    main()
