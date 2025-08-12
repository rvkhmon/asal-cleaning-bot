
import sqlite3
from contextlib import closing
from datetime import datetime

DB_PATH = "data.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS rooms ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "work_date TEXT NOT NULL,"
            "room_no TEXT NOT NULL,"
            "maid TEXT,"
            "maid_tg_id INTEGER,"
            "cleaning_type TEXT CHECK(cleaning_type IN ('Полная','Текущая')) DEFAULT 'Текущая',"
            "status TEXT CHECK(status IN ('Убрано','Не убрано')) DEFAULT 'Не убрано',"
            "comment TEXT,"
            "updated_by TEXT,"
            "updated_at TEXT"
            ");"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rooms_date ON rooms(work_date);")

        cur.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "tg_id INTEGER UNIQUE,"
            "name TEXT,"
            "role TEXT CHECK(role IN ('admin','maid','user')) DEFAULT 'user',"
            "created_at TEXT"
            ");"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "key TEXT PRIMARY KEY,"
            "value TEXT"
            ");"
        )
        con.commit()

def set_setting(key, value):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        con.commit()

def get_setting(key, default=None):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

def upsert_user(tg_id, name=None, role=None):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT tg_id FROM users WHERE tg_id=?", (tg_id,))
        if cur.fetchone():
            if name:
                cur.execute("UPDATE users SET name=? WHERE tg_id=?", (name, tg_id))
            if role:
                cur.execute("UPDATE users SET role=? WHERE tg_id=?", (role, tg_id))
        else:
            cur.execute("INSERT INTO users(tg_id,name,role,created_at) VALUES(?,?,?,?)",
                        (tg_id, name, role or 'user', datetime.utcnow().isoformat()))
        con.commit()

def get_user(tg_id):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT tg_id,name,role FROM users WHERE tg_id=?", (tg_id,))
        return cur.fetchone()

def add_plan_rows(rows):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        for r in rows:
            cur.execute(
                "INSERT INTO rooms (work_date, room_no, maid, maid_tg_id, cleaning_type, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'Не убрано', ?)",
                (r['work_date'], r['room_no'], r.get('maid'), r.get('maid_tg_id'),
                 r.get('cleaning_type', 'Текущая'), datetime.utcnow().isoformat())
            )
        con.commit()

def get_rooms(date_str):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT id, room_no, maid, maid_tg_id, cleaning_type, status, COALESCE(comment,'') "
                    "FROM rooms WHERE work_date=? ORDER BY room_no;", (date_str,))
        return cur.fetchall()

def get_room(room_id):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT id, work_date, room_no, maid, maid_tg_id, cleaning_type, status, COALESCE(comment,''), updated_by "
                    "FROM rooms WHERE id=?", (room_id,))
        return cur.fetchone()

def get_rooms_for_maid(date_str, maid_name=None, maid_tg_id=None):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        if maid_tg_id:
            cur.execute("SELECT id, room_no, maid, maid_tg_id, cleaning_type, status, COALESCE(comment,'') "
                        "FROM rooms WHERE work_date=? AND maid_tg_id=? ORDER BY room_no;",
                        (date_str, maid_tg_id))
        else:
            cur.execute("SELECT id, room_no, maid, maid_tg_id, cleaning_type, status, COALESCE(comment,'') "
                        "FROM rooms WHERE work_date=? AND maid=? ORDER BY room_no;",
                        (date_str, maid_name))
        return cur.fetchall()

def set_status(room_id, status, user):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("UPDATE rooms SET status=?, updated_by=?, updated_at=? WHERE id=?",
                    (status, user, datetime.utcnow().isoformat(), room_id))
        con.commit()

def toggle_type(room_id):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT cleaning_type FROM rooms WHERE id=?", (room_id,))
        row = cur.fetchone()
        if not row:
            return
        new_type = 'Полная' if row[0] == 'Текущая' else 'Текущая'
        cur.execute("UPDATE rooms SET cleaning_type=?, updated_at=? WHERE id=?",
                    (new_type, datetime.utcnow().isoformat(), room_id))
        con.commit()
        return new_type

def set_comment(room_id, comment, user):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("UPDATE rooms SET comment=?, updated_by=?, updated_at=? WHERE id=?",
                    (comment, user, datetime.utcnow().isoformat(), room_id))
        con.commit()

def clear_date(date_str):
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("DELETE FROM rooms WHERE work_date=?", (date_str,))
        con.commit()

def stats(date_str):
    rooms = get_rooms(date_str)
    total = len(rooms)
    cleaned = sum(1 for r in rooms if r[5] == 'Убрано')
    remaining = total - cleaned
    full_total = sum(1 for r in rooms if r[4] == 'Полная')
    full_cleaned = sum(1 for r in rooms if r[4] == 'Полная' and r[5] == 'Убрано')
    return {'total': total, 'cleaned': cleaned, 'remaining': remaining,
            'full_total': full_total, 'full_cleaned': full_cleaned}
