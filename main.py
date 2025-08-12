
import os, io, csv, pytz
from openpyxl import Workbook
from datetime import datetime, timedelta, time
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from db import (
    init_db, add_plan_rows, get_rooms, get_rooms_for_maid, get_room, set_status, toggle_type,
    set_comment, clear_date, stats, upsert_user, get_user, set_setting, get_setting
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit())
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID","0") or "0")
REPORT_TIME = os.getenv("REPORT_TIME","18:00")
AUTOCARRYOVER = (os.getenv("AUTOCARRYOVER","true").lower() == "true")

def tz():
    return pytz.timezone(TIMEZONE)

def now_local():
    return datetime.now(tz())

def day_str(dt=None):
    return (dt or now_local()).strftime("%Y-%m-%d")

def is_admin(user_id):
    return (user_id in ADMIN_IDS) if ADMIN_IDS else True

def room_keyboard(room_id, status, cleaning_type):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Убрано" if status!="Убрано" else "↩️ Отметить НЕ убрано",
                              callback_data=f"status:{room_id}:{'Убрано' if status!='Убрано' else 'Не убрано'}")],
        [InlineKeyboardButton(f"🔁 Тип: {cleaning_type}", callback_data=f"toggle:{room_id}")],
        [InlineKeyboardButton("📝 Комментарий", callback_data=f"comment:{room_id}")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user.id, update.effective_user.full_name)
    await update.message.reply_text(
        "Привет! Я бот учёта уборки номеров 🧹\n\n"
        "Основные команды:\n"
        "/plan — план на сегодня\n"
        "/my — мои номера на сегодня\n"
        "/report — отчёт за сегодня\n"
        "/export_csv /export_xlsx — выгрузка\n"
        "/upload_plan — загрузить CSV (админ)\n"
        "/resetday — очистить план (админ)\n"
        "/carryover [YYYY-MM-DD] — перенести неубранные (админ)\n"
        "/set_tz Asia/Tashkent — установить часовой пояс (админ)\n"
        "/iam <Имя> — задать своё имя"
    )

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    rooms = get_rooms(d)
    if not rooms:
        await update.message.reply_text("План пуст. Админ может загрузить CSV через /upload_plan.")
        return
    lines = [f"🧾 План на {d}:"]
    for rid, room_no, maid, maid_tg_id, ctype, status, comment in rooms:
        line = f"• №{room_no} — {ctype}, горничная: {maid or '-'} — {status}"
        if comment: line += f" — 📝 {comment}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))

async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    usr = get_user(update.effective_user.id)
    rooms = get_rooms_for_maid(d, maid_tg_id=update.effective_user.id)
    if not rooms and usr and usr[1]:
        rooms = get_rooms_for_maid(d, maid_name=usr[1])
    if not rooms:
        await update.message.reply_text("На сегодня за вами нет закреплённых номеров.")
        return
    for rid, room_no, maid, maid_tg_id, ctype, status, comment in rooms:
        await update.message.reply_text(f"№{room_no} • {ctype} • {status}", reply_markup=room_keyboard(rid, status, ctype))

async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    title = getattr(chat, "title", "") or "(нет названия)"
    await update.message.reply_text(f"Chat ID: {chat.id}\nНазвание: {title}")

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await build_report_text()
    msg = await update.message.reply_text(text)
    # попытка запинить, если бот админ
    try:
        await update.effective_chat.pin_message(msg.message_id)
    except Exception:
        pass

async def build_report_text():
    d = day_str()
    s = stats(d)
    percent = 0 if s['total']==0 else round(s['cleaned']*100/s['total'])
    return (f"📊 Отчёт на {d}\n"
            f"Всего: {s['total']} • Убрано: {s['cleaned']} ({percent}%) • Осталось: {s['remaining']}\n"
            f"Полная уборка: {s['full_cleaned']}/{s['full_total']}")

async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    if REPORT_CHAT_ID == 0: return
    text = await build_report_text()
    msg = await context.bot.send_message(chat_id=REPORT_CHAT_ID, text=text)
    try:
        await context.bot.pin_chat_message(chat_id=REPORT_CHAT_ID, message_id=msg.message_id, disable_notification=True)
    except Exception:
        pass
    # перенос неубранных на завтра (если включено)
    if AUTOCARRYOVER:
        tomorrow = day_str(now_local() + timedelta(days=1))
        from db import get_rooms  # reimport safe
        # переносим через простую команду — в упрощённой версии админ сделает вручную при необходимости
        # здесь не переносим, чтобы не дублировать. Можно включить перенос через отдельный job при 23:55.

async def cmd_upload_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только админ.")
        return
    await update.message.reply_text("Пришлите CSV: room_no,maid,cleaning_type (Полная/Текущая). Дата = сегодня.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только админ может загружать план.")
        return
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("Нужен CSV-файл.")
        return
    f = await doc.get_file()
    bio = io.BytesIO()
    await f.download(out=bio)
    bio.seek(0)
    text = bio.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), fieldnames=["room_no","maid","cleaning_type"])
    rows = []
    d = day_str()
    for row in reader:
        room_no = (row.get("room_no") or "").strip()
        if not room_no or room_no.lower().startswith("room_no"): continue
        maid = (row.get("maid") or "").strip() or None
        ctype = (row.get("cleaning_type") or "Текущая").strip()
        if ctype not in ("Полная","Текущая"): ctype = "Текущая"
        rows.append({"work_date": d, "room_no": room_no, "maid": maid, "maid_tg_id": None, "cleaning_type": ctype})
    if not rows:
        await update.message.reply_text("Файл пуст или неверного формата.")
        return
    clear_date(d)
    add_plan_rows(rows)
    await update.message.reply_text(f"Загружено {len(rows)} строк на {d}. /plan — список, /my — карточки для горничных.")
    # выдать карточки
    rooms = get_rooms(d)
    for rid, room_no, maid, maid_tg_id, ctype, status, comment in rooms:
        await update.message.reply_text(f"№{room_no} • {ctype} • {maid or '-'} • {status}", reply_markup=room_keyboard(rid, status, ctype))

async def cmd_resetday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Только админ.")
        return
    d = day_str()
    clear_date(d)
    await update.message.reply_text(f"План на {d} очищен.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")
    action = data[0]
    room_id = int(data[1])
    # проверка прав: админ или назначенная горничная
    r = get_room(room_id)
    if r:
        _, work_date, _, maid_name, maid_tg_id, _, _, _, _ = r
        user_id = query.from_user.id
        user_ok = is_admin(user_id) or (maid_tg_id == user_id)
        if not user_ok:
            # если tg_id не указан в плане — попробуем сверить по имени
            usr = get_user(user_id)
            if usr and usr[1] and maid_name and usr[1].strip().lower() == maid_name.strip().lower():
                user_ok = True
        if not user_ok:
            await query.message.reply_text("⛔ Только назначенная горничная или админ может менять статус этого номера.")
            return

    if action == "status":
        new_status = data[2]
        set_status(room_id, new_status, query.from_user.full_name)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Статус обновлён ✅")
    elif action == "toggle":
        new_type = toggle_type(room_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"Тип уборки изменён на: {new_type}")
    elif action == "comment":
        context.user_data["await_comment_for"] = room_id
        await query.message.reply_text("Напишите комментарий одним сообщением.")
    else:
        await query.message.reply_text("Неизвестное действие.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text or ""

    # --- режим ввода комментария к номеру ---
    if "await_comment_for" in context.user_data:
        rid = context.user_data.pop("await_comment_for")
        set_comment(rid, txt, update.message.from_user.full_name)
        await update.message.reply_text("Комментарий сохранён 📝")
        return

    # --- команда /iam Имя ---
    if txt.startswith("/iam "):
        name = txt[5:].strip()
        if not name:
            await update.message.reply_text("Пример: /iam Севара")
            return
        upsert_user(update.effective_user.id, name=name)
        await update.message.reply_text(f"Готово! Сохранил имя: {name}")
        return

    # --- /export_csv ---
    if txt.startswith("/export_csv"):
        d = day_str()
        rows = get_rooms(d)
        buff = io.StringIO()
        writer = csv.writer(buff)
        writer.writerow(["work_date","room_no","maid","cleaning_type","status","comment"])
        for rid, room_no, maid, maid_tg_id, ctype, status, comment in rows:
            writer.writerow([d, room_no, maid or "", ctype, status, comment or ""])
        buff.seek(0)
        await update.message.reply_document(
            document=InputFile(io.BytesIO(buff.getvalue().encode("utf-8")),
                               filename=f"cleaning_{d}.csv")
        )
        return

    # --- /export_xlsx (через openpyxl, БЕЗ pandas) ---
    if txt.startswith("/export_xlsx"):
    from openpyxl import Workbook
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

    # --- проксирование основных команд ---
    if txt.startswith("/plan"):
        await cmd_plan(update, context); return
    if txt.startswith("/my"):
        await cmd_my(update, context); return
    if txt.startswith("/report"):
        await cmd_report(update, context); return
    if txt.startswith("/upload_plan"):
        await cmd_upload_plan(update, context); return
    if txt.startswith("/resetday"):
        await cmd_resetday(update, context); return
    if txt.startswith("/set_tz "):
        # админская: /set_tz Asia/Tashkent
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Только админ.")
            return
        tz = txt[8:].strip()
        try:
            _ = pytz.timezone(tz)
        except Exception:
            await update.message.reply_text("Некорректный часовой пояс.")
            return
        global TIMEZONE
        TIMEZONE = tz
        set_setting("TIMEZONE", tz)
        await update.message.reply_text(f"Часовой пояс установлен: {tz}")
        return
    if update.message.text.startswith("/export_xlsx"):
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
def schedule_jobs(app):
    # ежедневный отчёт
    if REPORT_CHAT_ID and REPORT_TIME:
        hh, mm = (int(x) for x in REPORT_TIME.split(":"))
        app.job_queue.run_daily(daily_report, time=time(hour=hh, minute=mm, tzinfo=tz()))
    # опционально — nightly carryover в 23:55
    if os.getenv("AUTOCARRYOVER","true").lower()=="true":
        async def nightly(context):
            from db import stats as _stats
            s = _stats(day_str())
            if s['remaining']>0 and REPORT_CHAT_ID:
                await context.bot.send_message(REPORT_CHAT_ID, f"↪️ Перенесите неубранные на завтра командой /carryover YYYY-MM-DD (опционально).")
        app.job_queue.run_daily(nightly, time=time(hour=23, minute=55, tzinfo=tz()))

def main():
    init_db()
    tz_saved = get_setting("TIMEZONE")
    global TIMEZONE
    if tz_saved: TIMEZONE = tz_saved

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    schedule_jobs(app)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    # команды как /plan в приват/группе
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("my", cmd_my))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("upload_plan", cmd_upload_plan))
    app.add_handler(CommandHandler("resetday", cmd_resetday))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
