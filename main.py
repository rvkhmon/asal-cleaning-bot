# main.py
# ASAL Cleaning Bot — production-ready
# Работает с python-telegram-bot v21.x

import os, io, csv, pytz
from datetime import datetime, timedelta, time

from dotenv import load_dotenv
from openpyxl import Workbook

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ==== DB helpers (из db.py) ====
# Предполагаются функции с такими именами/сигнатурами:
# init_db()
# add_plan_rows(date_str, rows: list[dict])
# get_rooms(date_str) -> list[(id, room_no, maid, maid_tg_id, ctype, status, comment)]
# get_rooms_for_maid(date_str, tg_id) -> list[...]
# get_room(room_id) -> tuple(...)
# set_status(room_id, status_str)
# toggle_type(room_id) -> new_type
# set_comment(room_id, text, author)
# clear_date(date_str)
# stats(date_str) -> (total, done, left, percent)
# upsert_user(tg_id, name)
# get_user(tg_id) -> dict|None { 'id', 'tg_id', 'name' }
# set_setting(key, value), get_setting(key)
from db import (
    init_db, add_plan_rows, get_rooms, get_rooms_for_maid, get_room,
    set_status, toggle_type, set_comment, clear_date, stats,
    upsert_user, get_user, set_setting, get_setting
)

# ==== ENV ====
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID", "0") or "0")
REPORT_TIME = os.getenv("REPORT_TIME", "18:00")
AUTOCARRYOVER = (os.getenv("AUTOCARRYOVER", "true").lower() == "true")

# список админов по tg id (через запятую)
ADMIN_IDS = set(
    int(x.strip()) for x in (os.getenv("ADMIN_IDS", "") or "").split(",")
    if x.strip().isdigit()
)

# Диагностика env (без вывода токена)
bt = (BOT_TOKEN or "").strip()
print(f"[ENV] BOT_TOKEN set? {'yes' if bt else 'no'}; length={len(bt)}")
print(f"[ENV] TIMEZONE={TIMEZONE!r} REPORT_TIME={REPORT_TIME!r} AUTOCARRYOVER={AUTOCARRYOVER}")
print(f"[ENV] REPORT_CHAT_ID={REPORT_CHAT_ID} ADMIN_IDS={sorted(ADMIN_IDS) if ADMIN_IDS else '[]'}")

if not bt or ":" not in bt:
    raise ValueError(
        "❌ BOT_TOKEN не найден или некорректен. "
        "Задай его в Render → Environment → BOT_TOKEN=<токен от @BotFather>."
    )

# ==== time helpers ====
def tz():
    return pytz.timezone(TIMEZONE)

def now_local():
    return datetime.now(tz())

def day_str(dt: datetime | None = None):
    return (dt or now_local()).strftime("%Y-%m-%d")

# ==== permissions ====
def is_admin(tg_id: int) -> bool:
    return (len(ADMIN_IDS) == 0) or (tg_id in ADMIN_IDS)

# ==== keyboards & formatting ====
def room_row_to_text(r):
    _, room_no, maid, _, ctype, status, comment = r
    s = "✅ Убрано" if status == "done" else "⏳ Не убрано"
    cm = f"\n📝 {comment}" if (comment or "").strip() else ""
    return f"№{room_no} • {ctype} • {s}\nГорничная: {maid or '-'}{cm}"

def room_row_kb(r):
    rid = r[0]
    _, room_no, maid, _, ctype, status, comment = r
    togglestatus = "↩️ Отметить НЕ убрано" if status == "done" else "✅ Отметить убрано"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(togglestatus, callback_data=f"st:{rid}")],
        [InlineKeyboardButton("🔁 Тип (Полная/Текущая)", callback_data=f"tp:{rid}")],
        [InlineKeyboardButton("📝 Комментарий", callback_data=f"cm:{rid}")]
    ])

# ==== commands ====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это бот учёта уборки.\n\n"
        "Основные команды:\n"
        "• /iam Имя — указать своё имя (для горничной)\n"
        "• /my — мои номера на сегодня\n"
        "• /report — отчёт по дню\n"
        "• /upload_plan — загрузить план CSV (админ)\n"
        "• /export_csv, /export_xlsx — выгрузка текущего дня\n"
        "• /chatid — показать Chat ID\n"
        "• /resetday — очистить план на сегодня (админ)\n"
    )

async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    title = getattr(chat, "title", "") or "(нет названия)"
    await update.message.reply_text(f"Chat ID: {chat.id}\nНазвание: {title}")

async def cmd_iam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text("Пример: /iam Севара")
        return
    name = args[1].strip()
    if not name:
        await update.message.reply_text("Пример: /iam Севара")
        return
    upsert_user(update.effective_user.id, name=name)
    await update.message.reply_text(f"Готово! Сохранил имя: {name}")

async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    rows = get_rooms_for_maid(d, update.effective_user.id)
    if not rows:
        # fallback: если нет привязки — подсказка
        u = get_user(update.effective_user.id)
        if not u:
            await update.message.reply_text("Сначала укажи имя: /iam Имя")
            return
        await update.message.reply_text("На сегодня номеров не назначено.")
        return
    for r in rows:
        await update.message.reply_text(room_row_to_text(r), reply_markup=room_row_kb(r))

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только админ.")
        return
    d = day_str()
    rows = get_rooms(d)
    if not rows:
        await update.message.reply_text("План на сегодня пуст.")
        return
    text = "📋 План на сегодня:\n\n"
    for r in rows:
        _, room_no, maid, _, ctype, status, comment = r
        text += f"№{room_no} • {ctype} • {('✅' if status=='done' else '—')} • {maid or '-'}\n"
    await update.message.reply_text(text)

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    total, done, left, percent = stats(d)
    msg = f"🧹 Отчёт за {d}\n\nВсего: {total}\nУбрано: {done}\nОсталось: {left}\nГотово: {percent}%"
    sent = await update.message.reply_text(msg)
    # пробуем закрепить, если это групповой чат
    try:
        if update.effective_chat.type in ("group", "supergroup"):
            await context.bot.pin_chat_message(update.effective_chat.id, sent.message_id, disable_notification=True)
    except Exception:
        pass

async def cmd_resetday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только админ.")
        return
    d = day_str()
    clear_date(d)
    await update.message.reply_text(f"Удалил план на {d}.")

async def cmd_upload_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только админ.")
        return
    context.user_data["await_plan"] = True
    await update.message.reply_text(
        "Пришлите CSV-файл плана. Формат:\n"
        "`room_no,maid,cleaning_type`\n\n"
        "Пример:\n"
        "101,Севара,Полная\n"
        "102,Гульноз,Текущая",
        parse_mode="Markdown"
    )

# ==== document (CSV) ====
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_plan"):
        return
    doc = update.message.document
    if not doc:
        return
    if not (doc.mime_type and "csv" in doc.mime_type.lower()) and not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("Нужен именно CSV-файл.")
        return

    # скачиваем
    file = await doc.get_file()
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)
    text = bio.read().decode("utf-8-sig").strip()

    # парсим
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        room_no = str(row.get("room_no", "")).strip()
        maid = (row.get("maid", "") or "").strip()
        ctype = (row.get("cleaning_type", "") or "").strip() or "Текущая"
        if not room_no:
            continue
        rows.append({"room_no": room_no, "maid": maid, "cleaning_type": ctype})

    if not rows:
        await update.message.reply_text("В CSV не нашёл строк.")
        return

    d = day_str()
    add_plan_rows(d, rows)
    context.user_data.pop("await_plan", None)

    await update.message.reply_text(f"Загрузил план на {d}: {len(rows)} строк.")
    # покажем сводку
    await cmd_report(update, context)

# ==== text (/export_csv, /export_xlsx, /iam echo, etc) ====
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text or ""

    # режим ввода комментария
    if "await_comment_for" in context.user_data:
        rid = context.user_data.pop("await_comment_for")
        set_comment(rid, txt, update.message.from_user.full_name)
        await update.message.reply_text("Комментарий сохранён 📝")
        return

    if txt.startswith("/iam "):
        await cmd_iam(update, context)
        return

    if txt.startswith("/export_csv"):
        d = day_str()
        rows = get_rooms(d)
        buff = io.StringIO()
        writer = csv.writer(buff)
        writer.writerow(["work_date", "room_no", "maid", "cleaning_type", "status", "comment"])
        for rid, room_no, maid, maid_tg_id, ctype, status, comment in rows:
            writer.writerow([d, room_no, maid or "", ctype, status, comment or ""])
        buff.seek(0)
        await update.message.reply_document(
            document=InputFile(io.BytesIO(buff.getvalue().encode("utf-8")),
                               filename=f"cleaning_{d}.csv")
        )
        return

    if txt.startswith("/export_xlsx"):
        d = day_str()
        rows = get_rooms(d)

        wb = Workbook()
        ws = wb.active
        ws.title = "Уборка"
        ws.append(["Дата", "№ Номера", "Горничная", "Тип", "Статус", "Комментарий"])
        for rid, room_no, maid, maid_tg_id, ctype, status, comment in rows:
            ws.append([d, room_no, maid or "", ctype, status, comment or ""])

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        await update.message.reply_document(
            document=InputFile(bio, filename=f"cleaning_{d}.xlsx")
        )
        return

# ==== callbacks (кнопки) ====
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if ":" not in data:
        return
    action, rid = data.split(":", 1)
    rid = int(rid)

    if action == "st":  # toggle status
        r = get_room(rid)
        if not r:
            return
        _, _, maid, maid_tg_id, _, status, _ = r
        user_id = update.effective_user.id
        # менять может назначенная горничная или админ
        if (maid_tg_id and user_id == maid_tg_id) or is_admin(user_id):
            new_status = "todo" if status == "done" else "done"
            set_status(rid, new_status)
            r2 = get_room(rid)
            await q.edit_message_text(room_row_to_text(r2), reply_markup=room_row_kb(r2))
        else:
            await q.answer("Нет прав менять статус", show_alert=True)

    elif action == "tp":  # toggle type
        new_type = toggle_type(rid)
        r2 = get_room(rid)
        await q.edit_message_text(room_row_to_text(r2), reply_markup=room_row_kb(r2))

    elif action == "cm":  # comment
        context.user_data["await_comment_for"] = rid
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("Напишите комментарий к этому номеру:")

# ==== scheduled jobs ====
async def send_report(context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    total, done, left, percent = stats(d)
    msg = f"🧹 Автоотчёт за {d}\n\nВсего: {total}\nУбрано: {done}\nОсталось: {left}\nГотово: {percent}%"
    if REPORT_CHAT_ID:
        m = await context.bot.send_message(REPORT_CHAT_ID, msg)
        try:
            await context.bot.pin_chat_message(REPORT_CHAT_ID, m.message_id, disable_notification=True)
        except Exception:
            pass

async def carryover_left(context: ContextTypes.DEFAULT_TYPE):
    if not AUTOCARRYOVER:
        return
    today = now_local().date()
    tomorrow = datetime.combine(today + timedelta(days=1), time(9,0)).astimezone(tz())
    # перенос реализован на стороне БД (в твоём db.py — clear_date/add_plan_rows можно использовать)
    # Здесь простой вариант: выбираем “не убрано” и переносим с теми же параметрами.
    rows = [r for r in get_rooms(day_str()) if r[5] != "done"]
    if not rows:
        return
    carry = []
    for _, room_no, maid, _, ctype, status, comment in rows:
        carry.append({"room_no": room_no, "maid": maid or "", "cleaning_type": ctype})
    add_plan_rows(day_str(tomorrow), carry)
    if REPORT_CHAT_ID:
        await context.bot.send_message(REPORT_CHAT_ID, f"🔁 Перенёс на завтра: {len(carry)} номеров.")

# ==== app ====
def schedule_daily_jobs(app):
    # автоотчёт в REPORT_TIME local
    try:
        hh, mm = [int(x) for x in REPORT_TIME.split(":")]
    except Exception:
        hh, mm = 18, 0
    app.job_queue.run_daily(send_report, time=time(hh, mm), name="daily_report", timezone=tz())
    # перенос в 23:55 local
    app.job_queue.run_daily(carryover_left, time=time(23,55), name="carryover", timezone=tz())

def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("iam", cmd_iam))
    app.add_handler(CommandHandler("my", cmd_my))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("resetday", cmd_resetday))
    app.add_handler(CommandHandler("upload_plan", cmd_upload_plan))

    # документы CSV (после /upload_plan)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # текстовые хендлеры: /export_csv, /export_xlsx, комментарии
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    # callback-кнопки
    app.add_handler(CallbackQueryHandler(on_cb))

    schedule_daily_jobs(app)

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
