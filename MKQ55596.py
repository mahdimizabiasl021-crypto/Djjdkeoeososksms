import os
import sqlite3
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.error import NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ======================
# TOKEN LOADER
# ======================
def load_token():
    if os.getenv("BOT_TOKEN"):
        return os.getenv("BOT_TOKEN")

    for f in ("Token.txt", "token.txt"):
        if os.path.exists(f):
            return open(f, "r", encoding="utf-8").read().strip()

    raise RuntimeError("BOT_TOKEN not found")

TOKEN = load_token()

# ======================
# DATABASE URL LOADER
# ======================
def load_database_url():
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")

    for f in ("Database.txt", "database.txt"):
        if os.path.exists(f):
            return open(f, "r", encoding="utf-8").read().strip()

    return None  # fallback to sqlite

DATABASE_URL = load_database_url()

# ======================
# ADMIN
# ======================
ADMIN_IDS = {6474515118}

# ======================
# DATABASE INIT
# ======================
using_pg = DATABASE_URL is not None

if using_pg:
    import psycopg2
    db = psycopg2.connect(DATABASE_URL)
    db.autocommit = True
    cur = db.cursor()

    def q(sql, p=None):
        cur.execute(sql, p or ())
else:
    db = sqlite3.connect("bot.db", check_same_thread=False)
    cur = db.cursor()

    def q(sql, p=None):
        cur.execute(sql, p or ())
        db.commit()

# ======================
# TABLES
# ======================
q("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_admin INTEGER,
    last_seen BIGINT
)
""")

if using_pg:
    q("""
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        sender_id BIGINT,
        receiver_id BIGINT,
        msg_type TEXT,
        content TEXT,
        ts BIGINT
    )
    """)
else:
    q("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        msg_type TEXT,
        content TEXT,
        ts INTEGER
    )
    """)

q("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# ======================
# SETTINGS HELPERS
# ======================
def set_setting(k, v):
    if using_pg:
        q("INSERT INTO settings VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    else:
        q("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))

def get_setting(k, d=""):
    if using_pg:
        q("SELECT value FROM settings WHERE key=%s", (k,))
    else:
        q("SELECT value FROM settings WHERE key=?", (k,))
    r = cur.fetchone()
    return r[0] if r else d

def get_bool(k):
    return get_setting(k, "0") == "1"

def set_bool(k, v):
    set_setting(k, "1" if v else "0")

# defaults
set_setting("force_join_channel", get_setting("force_join_channel", "@YOUR_CHANNEL"))
set_setting("force_join_link", get_setting("force_join_link", "https://t.me/YOUR_CHANNEL"))
set_bool("force_join_enabled", get_bool("force_join_enabled"))

# ======================
# HELPERS
# ======================
def now():
    return int(time.time())

def save_user(u):
    q("""
    INSERT INTO users VALUES(%s,%s,%s,%s,%s)
    ON CONFLICT(user_id) DO UPDATE SET
    username=EXCLUDED.username,
    full_name=EXCLUDED.full_name,
    is_admin=EXCLUDED.is_admin,
    last_seen=EXCLUDED.last_seen
    """ if using_pg else """
    INSERT INTO users VALUES(?,?,?,?,?)
    ON CONFLICT(user_id) DO UPDATE SET
    username=excluded.username,
    full_name=excluded.full_name,
    is_admin=excluded.is_admin,
    last_seen=excluded.last_seen
    """,
    (u.id, u.username, u.full_name, int(u.id in ADMIN_IDS), now()))

def save_msg(s, r, t, c):
    q("INSERT INTO messages VALUES(DEFAULT,%s,%s,%s,%s,%s)" if using_pg
      else "INSERT INTO messages VALUES(NULL,?,?,?,?,?)",
      (s, r, t, c, now()))

# ======================
# FORCE JOIN
# ======================
async def must_join(update, context):
    if update.effective_user.id in ADMIN_IDS:
        return True
    if not get_bool("force_join_enabled"):
        return True

    ch = get_setting("force_join_channel")
    link = get_setting("force_join_link")

    try:
        m = await context.bot.get_chat_member(ch, update.effective_user.id)
        if m.status in ("member", "administrator", "creator"):
            return True
    except:
        pass

    await update.effective_message.reply_text(f"برای استفاده از ربات اول عضو شو:\n{link}")
    return False

# ======================
# MENUS
# ======================
def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 ۱۵ کاربر آخر", callback_data="last_users")],
        [InlineKeyboardButton("✉️ پیام ناشناس", callback_data="anon_send")],
        [InlineKeyboardButton("⚙️ جوین اجباری", callback_data="toggle_join")]
    ])

# ======================
# START
# ======================
async def start(update: Update, context):
    save_user(update.effective_user)
    if update.effective_user.id in ADMIN_IDS:
        await update.message.reply_text("پنل ادمین", reply_markup=admin_menu())
        return
    if not await must_join(update, context):
        return
    await update.message.reply_text("سلام 👋")

# ======================
# BUTTONS
# ======================
anon_target = {}

async def buttons(update: Update, context):
    qy = update.callback_query
    await qy.answer()
    uid = qy.from_user.id

    if qy.data == "toggle_join":
        set_bool("force_join_enabled", not get_bool("force_join_enabled"))
        await qy.message.reply_text("وضعیت جوین اجباری تغییر کرد")

    elif qy.data == "last_users":
        q("SELECT user_id,full_name,username FROM users ORDER BY last_seen DESC LIMIT 15")
        rows = cur.fetchall()
        txt = "\n\n".join([f"{n or '-'}\nID:{i}\n@{u or '-'}" for i,n,u in rows])
        await qy.message.reply_text(txt or "خالی")

    elif qy.data == "anon_send":
        anon_target[uid] = None
        await qy.message.reply_text("آیدی عددی کاربر رو بفرست")

# ======================
# MESSAGE HANDLER
# ======================
async def messages(update: Update, context):
    uid = update.effective_user.id
    save_user(update.effective_user)

    if uid in anon_target:
        if anon_target[uid] is None:
            anon_target[uid] = int(update.message.text)
            await update.message.reply_text("متن پیام رو بفرست")
        else:
            t = anon_target.pop(uid)
            await context.bot.send_message(t, update.message.text)
            save_msg(uid, t, "anon", update.message.text)
            await update.message.reply_text("ارسال شد")
        return

# ======================
# MAIN (ANTI-DROP)
# ======================
def run_bot():
    while True:
        try:
            app = ApplicationBuilder().token(TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CallbackQueryHandler(buttons))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages))
            app.run_polling(close_loop=False)
        except NetworkError:
            time.sleep(5)
        except Exception:
            time.sleep(5)
