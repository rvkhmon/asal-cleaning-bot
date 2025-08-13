import os
import io
import csv
import re
import sqlite3
from datetime import datetime, timedelta, time as dtime
from typing import List, Tuple, Optional

import pytz
from dotenv import load_dotenv
from openpyxl import Workbook

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ───────────────────────────────────────────────────────────────────────────────
# Конфиг из окружения
# ───────────────────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent"
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID", "0") or 0)
REPORT_TIME = os.getenv("REPORT_TIME", "18:00").strip() or "18:00"
AUTOCARRYOVER = os.getenv("AUTOCARRYOVER", "true").lower() == "true"
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

def tz() -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(TIMEZONE)
    except Exception:
        return pytz.timezone("Asia/Tashkent")

def now_local() -> datetime:
    return datetime.now(tz())

def day_str(dt: Optional[datetime] = None) -> str:
    return (dt or now_local()).strftime("%Y-%m-%d")

# ───────────────────────────────────────────────────────────────────────────────
# БД (SQLite)
# ───────────────────────────────────────────────────────────────────────────────
DB_PATH = "asal.db"

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                room_no INTEGER NOT NULL,
                maid TEXT NOT NULL,
                ctype TEXT NOT NULL,     -- Полная / Текущая
                status TEXT NOT NULL DEFAULT 'Назначено', -- Назначено/В процессе/Готово/Не убрано
                comment TEXT
            )
        """)
    conn.close()

init_db()

# ───────────────────────────────────────────────────────────────────────────────
# Утилиты БД
# ───────────────────────────────────────────────────────────────────────────────
def clear_day(day: str):
    conn = db()
    with conn:
        conn.execute("DELETE FROM plan WHERE day=?", (day,))
    conn.close()

def insert_rows(day: str, rows: List[Tuple[int, str, str]]):
    conn = db()
    with conn:
        conn.executemany(
            "INSERT INTO plan(day, room_no, maid, ctype) VALUES (?,?,?,?)",
            [(day, int(r), m, c) for r, m, c in rows],
        )
    conn.close()

def get_rooms(day: str):
    conn = db()
    cur = conn.execute(
        "SELECT room_no, maid, ctype, status, COALESCE(comment,'') comment "
        "FROM plan WHERE day=? ORDER BY room_no", (day,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def get_stats(day: str):
    conn = db()
    cur = conn.execute(
        "SELECT "
        "COUNT(*) as total, "
        "SUM(CASE WHEN status='Готово' THEN 1 ELSE 0 END) as done, "
        "SUM(CASE WHEN status!='Готово' THEN 1 ELSE 0 END) as left "
        "FROM plan WHERE day=?", (day,)
    )
    row = cur.fetchone()
    conn.close()
    total = row["total"] or 0
    done = row["done"] or 0
    left = row["left"] or 0
    return total, done, left

# ───────────────────────────────────────────────────────────────────────────────
# Парсер CSV — «железобетонный»
# ───────────────────────────────────────────────────────────────────────────────
def _decode_bytes(b: bytes) -> str:
    # 1) UTF-8 (включая BOM)
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # 2) Windows-1251
    try:
        return b.decode("cp1251")
    except UnicodeDecodeError:
        # 3) На край — игнор битых символов
        return b.decode("utf-8", errors="ignore")

def parse_plan_csv_bytes(b: bytes) -> List[Tuple[int, str, str]]:
    """
    Возвращает список кортежей: (room_no, maid, ctype)
    Поддерживает:
    - кодировки: utf-8/utf-8-sig/cp1251
    - разделители: запятая или точка с запятой
    - наличие/отсутствие заголовка
    - пробелы вокруг значений
    """
    txt = _decode_bytes(b)
    rows: List[Tuple[int, str, str]] = []

    for raw in txt.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = re.split(r"[;,]", line)
        parts = [p.strip() for p in parts if p.strip() != ""]
        if len(parts) < 3:
            # возможно это заголовок или мусор
            continue
        room, maid, ctype = parts[0], parts[1], parts[2]
        # пропустим строку, если это заголовок
        if not room.isdigit():
            # допускаем, что это строка заголовка — просто скипаем
            continue
        try:
            rno = int(room)
        except ValueError:
            continue
        rows.append((rno, maid, ctype))
    return rows

# ───────────────────────────────────────────────────────────────────────────────
# Команды
# ───────────────────────────────────────────────────────────────────────────────
HELP_TEXT = (
    "Привет! Я бот учёта уборок.\n\n"
    "Доступные команды:\n"
    "/upload_plan — загрузить план (CSV)\n"
    "/report — отчёт за сегодня\n"
    "/export_csv — выгрузить план в CSV\n"
    "/export_xlsx — выгрузить план в XLSX\n"
    "/clear_today — очистить сегодняшний план (админ)\n\n"
    "Формат CSV (без заголовков):\n"
    "`101,Севара,Полная`\n"
    "`102,Гульноз,Текущая`\n"
    "Допускаются: UTF-8/UTF-8-BOM/cp1251, запятая или `;`.\n"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or (REPORT_CHAT_ID != 0 and user_id == REPORT_CHAT_ID)

async def clear_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Только админы могут очищать план.")
        return
    d = day_str()
    clear_day(d)
    await update.message.reply_text(f"План на {d} очищен.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    total, done, left = get_stats(d)
    rows = get_rooms(d)
    lines = [f"🧹 Отчёт за {d}\nВсего: {total} | Готово: {done} | Осталось: {left}", ""]
    by_maid = {}
    for r in rows:
        by_maid.setdefault(r["maid"], []).append(r)
    for maid, lst in sorted(by_maid.items()):
        lines.append(f"— {maid}: {len(lst)} номеров")
    await update.message.reply_text("\n".join(lines))

async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    rows = get_rooms(d)
    buff = io.StringIO()
    w = csv.writer(buff)
    # заголовок — можно убрать, если не нужно
    w.writerow(["Дата", "№ Номера", "Горничная", "Тип", "Статус", "Комментарий"])
    for r in rows:
        w.writerow([d, r["room_no"], r["maid"], r["ctype"], r["status"], r["comment"]])
    buff.seek(0)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(buff.getvalue().encode("utf-8")), filename=f"cleaning_{d}.csv")
    )

async def export_xlsx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = day_str()
    rows = get_rooms(d)
    wb = Workbook()
    ws = wb.active
    ws.title = "Уборка"
    ws.append(["Дата", "№ Номера", "Горничная", "Тип", "Статус", "Комментарий"])
    for r in rows:
        ws.append([d, r["room_no"], r["maid"], r["ctype"], r["status"], r["comment"]])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    await update.message.reply_document(
        document=InputFile(bio, filename=f"cleaning_{d}.xlsx")
    )

# ───────────────────────────────────────────────────────────────────────────────
# Загрузка плана
# ───────────────────────────────────────────────────────────────────────────────
UPLOAD_PROMPT = (
    "Пришлите CSV-файл плана.\n"
    "Формат строк: `room_no,maid,cleaning_type` (например: `101,Севара,Полная`).\n"
    "Допускаются кодировки UTF-8 / UTF-8-BOM / cp1251, разделители `,` или `;`.\n"
)

async def upload_plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["await_csv"] = True
    await update.message.reply_text(UPLOAD_PROMPT, parse_mode="Markdown")

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # принимаем файл либо после /upload_plan, либо если это CSV
    doc = update.message.document
    if not doc:
        return

    filename = (doc.file_name or "").lower()
    expecting = context.user_data.get("await_csv", False)
    if not expecting and not filename.endswith(".csv"):
        return

    f = await doc.get_file()
    b = await f.download_as_bytearray()
    rows = parse_plan_csv_bytes(bytes(b))

    if not rows:
        preview = _decode_bytes(bytes(b))[:160].replace("\n", "\\n")
        await update.message.reply_text(
            "В CSV не нашёл строк.\n"
            "Проверьте: разделители (`,` или `;`), кодировку (UTF-8/UTF-8-BOM/cp1251), "
            "и отсутствие пустых строк/заголовков.\n\n"
            f"*Первые символы файла:* `{preview}`",
            parse_mode="Markdown",
        )
        return

    d = day_str()
    # Если включён автоперенос — очищаем сегодняшний план и заливаем заново
    clear_day(d)
    insert_rows(d, rows)
    total, done, left = get_stats(d)
    context.user_data["await_csv"] = False
    await update.message.reply_text(
        f"Загружено строк: {len(rows)} ✅\n"
        f"Всего: {total} | Готово: {done} | Осталось: {left}"
    )

# ───────────────────────────────────────────────────────────────────────────────
# Автоотчёт по расписанию (без параметра timezone)
# ───────────────────────────────────────────────────────────────────────────────
def next_run_utc() -> datetime:
    """Вычисляем следующий запуск в UTC, исходя из локальной таймзоны и REPORT_TIME."""
    hh, mm = 18, 0
    try:
        hh, mm = [int(x) for x in REPORT_TIME.split(":")]
    except Exception:
        pass

    local_today = now_local().replace(hour=hh, minute=mm, second=0, microsecond=0)
    if local_today <= now_local():
        local_today += timedelta(days=1)
    return local_today.astimezone(pytz.UTC)

async def send_report(context: ContextTypes.DEFAULT_TYPE):
    if not REPORT_CHAT_ID:
        return
    d = day_str()
    total, done, left = get_stats(d)
    msg = f"🧹 Ежедневный отчёт {d}\nВсего: {total} | Готово: {done} | Осталось: {left}"
    try:
        await context.bot.send_message(chat_id=REPORT_CHAT_ID, text=msg)
    except Exception:
        pass

def schedule_daily_job(app):
    # Первое срабатывание — в рассчитанное время, дальше — каждые 24 часа
    first_run = next_run_utc()
    app.job_queue.run_repeating(send_report, interval=24*60*60, first=first_run)

# ───────────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────────
def validate_env():
    print(f"[ENV] BOT_TOKEN set? {'yes' if BOT_TOKEN else 'no'}; length={len(BOT_TOKEN)}")
    print(f"[ENV] TIMEZONE='{TIMEZONE}' REPORT_TIME='{REPORT_TIME}' AUTOCARRYOVER={AUTOCARRYOVER}")
    print(f"[ENV] REPORT_CHAT_ID={REPORT_CHAT_ID} ADMIN_IDS={ADMIN_IDS}")
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не найден. Задайте его в Render → Environment → BOT_TOKEN=<токен от @BotFather>.")

def main():
    validate_env()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("upload_plan", upload_plan_cmd))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("export_csv", export_csv))
    app.add_handler(CommandHandler("export_xlsx", export_xlsx))
    app.add_handler(CommandHandler("clear_today", clear_today))

    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))

    # Автоотчёт
    schedule_daily_job(app)

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
