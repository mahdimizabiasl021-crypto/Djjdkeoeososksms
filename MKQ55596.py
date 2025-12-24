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
def load_token() -> str:
    t = os.environ.get("BOT_TOKEN")
    if t and t.strip():
        return t.strip()
    for name in ("Token.txt", "token.txt"):
        if os.path.exists(name):
            val = open(name, "r", encoding="utf-8").read().strip()
            if val:
                return val
    raise RuntimeError("BOT_TOKEN not found (env or Token.txt)")

TOKEN = load_token()

# ======================
# DATABASE_URL LOADER
# ======================
def load_database_url() -> str | None:
    u = os.environ.get("DATABASE_URL")
    if u and u.strip():
        return u.strip()
    for name in ("Database.txt", "database.txt"):
        if os.path.exists(name):
            val = open(name, "r", encoding="utf-8").read().strip()
            if val:
                return val
    return None

DATABASE_URL = load_database_url()
USING_PG = bool(DATABASE_URL)

# ======================
# ADMIN
# ======================
ADMIN_IDS = {6474515118}

# ======================
# DB init
# ======================
cur = None
db = None

def now_ts() -> int:
    return int(time.time())

if USING_PG:
    import psycopg  # psycopg3

    db = psycopg.connect(DATABASE_URL)
    db.autocommit = True
    cur = db.cursor()

    def q(sql: str, params=None):
        cur.execute(sql, params or ())
else:
    db = sqlite3.connect("bot.db", check_same_thread=False)
    cur = db.cursor()

    def q(sql: str, params=None):
        cur.execute(sql, params or ())
        db.commit()

# ======================
# Schema
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

q("""
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    sender_id BIGINT,
    receiver_id BIGINT,
    msg_type TEXT,
    content TEXT,
    ts BIGINT
)
""" if USING_PG else """
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
# Settings helpers
# ======================
def set_setting(k: str, v: str):
    if USING_PG:
        q(
            "INSERT INTO settings(key,value) VALUES(%s,%s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            (k, v)
        )
    else:
        q(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, v)
        )

def get_setting(k: str, default: str = "") -> str:
    if USING_PG:
        q("SELECT value FROM settings WHERE key=%s", (k,))
    else:
        q("SELECT value FROM settings WHERE key=?", (k,))
    r = cur.fetchone()
    return r[0] if r and r[0] is not None else default

def get_bool(k: str, default: bool = False) -> bool:
    v = get_setting(k, "1" if default else "0")
    return v == "1"

def set_bool(k: str, v: bool):
    set_setting(k, "1" if v else "0")

# defaults
if get_setting("force_join_channel", "") == "":
    set_setting("force_join_channel", "@YOUR_CHANNEL")
if get_setting("force_join_link", "") == "":
    set_setting("force_join_link", "https://t.me/YOUR_CHANNEL")
if get_setting("force_join_enabled", "") == "":
    set_bool("force_join_enabled", False)

# ======================
# Data helpers
# ======================
def save_user(u):
    username = (u.username or "").strip() if u.username else None
    full_name = (u.full_name or "").strip()
    is_admin = int(u.id in ADMIN_IDS)
    ts = now_ts()

    if USING_PG:
        q("""
        INSERT INTO users (user_id, username, full_name, is_admin, last_seen)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT(user_id) DO UPDATE SET
          username=EXCLUDED.username,
          full_name=EXCLUDED.full_name,
          is_admin=EXCLUDED.is_admin,
          last_seen=EXCLUDED.last_seen
        """, (u.id, username, full_name, is_admin, ts))
    else:
        q("""
        INSERT INTO users (user_id, username, full_name, is_admin, last_seen)
        VALUES (?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username,
          full_name=excluded.full_name,
          is_admin=excluded.is_admin,
          last_seen=excluded.last_seen
        """, (u.id, username, full_name, is_admin, ts))

def save_message(sender, receiver, msg_type, content):
    ts = now_ts()
    if USING_PG:
        q(
            "INSERT INTO messages (sender_id, receiver_id, msg_type, content, ts) VALUES (%s,%s,%s,%s,%s)",
            (sender, receiver, msg_type, content, ts)
        )
    else:
        q(
            "INSERT INTO messages (sender_id, receiver_id, msg_type, content, ts) VALUES (?,?,?,?,?)",
            (sender, receiver, msg_type, content, ts)
        )

def extract_content(update: Update) -> str:
    m = update.message
    if not m:
        return ""
    if m.text:
        return m.text
    if m.caption:
        return m.caption
    if m.photo:
        return "[photo]"
    if m.video:
        return "[video]"
    if m.document:
        return "[document]"
    if m.voice:
        return "[voice]"
    if m.audio:
        return "[audio]"
    if m.sticker:
        return "[sticker]"
    return "[other]"

# ======================
# Force join
# ======================
async def must_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user.id in ADMIN_IDS:
        return True
    if not get_bool("force_join_enabled", False):
        return True

    ch = get_setting("force_join_channel", "@YOUR_CHANNEL")
    link = get_setting("force_join_link", "https://t.me/YOUR_CHANNEL")

    try:
        member = await context.bot.get_chat_member(ch, update.effective_user.id)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True
    except Exception:
        pass

    await update.effective_message.reply_text(f"برای استفاده از ربات اول عضو کانال شو:\n{link}")
    return False

# ======================
# States
# ======================
user_links = {}
reply_state = {}
blocked = {}
admin_search_state = set()
admin_broadcast_state = set()
admin_set_channel_state = set()
admin_set_link_state = set()

admin_anon_target_state = set()     # admin enters target id
admin_anon_message_state = {}       # admin_id -> target_user_id

# ======================
# Menus
# ======================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 دریافت لینک اختصاصی", callback_data="get_link")],
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 آمار کاربران", callback_data="admin_stats")],
        [InlineKeyboardButton("🆕 ۱۵ کاربر آخر", callback_data="admin_latest_users")],
        [InlineKeyboardButton("🔍 مشاهده پیام‌های کاربر", callback_data="admin_search")],
        [InlineKeyboardButton("✉️ پیام ناشناس به کاربر", callback_data="admin_anon_send")],
        [InlineKeyboardButton("📢 ارسال پیام به همه", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ تنظیمات جوین اجباری", callback_data="admin_settings")],
    ])

def admin_settings_menu():
    enabled = get_bool("force_join_enabled", False)
    status = "روشن ✅" if enabled else "خاموش ❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔒 جوین اجباری: {status}", callback_data="toggle_force_join")],
        [InlineKeyboardButton("📢 تنظیم کانال", callback_data="set_force_join_channel")],
        [InlineKeyboardButton("🔗 تنظیم لینک کانال", callback_data="set_force_join_link")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_admin")],
    ])

# ======================
# Handlers
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)

    if update.effective_user.id in ADMIN_IDS:
        await update.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_menu())
        return

    if not await must_join(update, context):
        return

    await update.message.reply_text("سلام 👋", reply_markup=main_menu())

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qy = update.callback_query
    await qy.answer()
    uid = qy.from_user.id

    if qy.data == "get_link":
        if uid not in ADMIN_IDS:
            if not await must_join(update, context):
                return
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await qy.message.reply_text(link)
        return

    # ------- admin actions -------
    if uid not in ADMIN_IDS:
        return

    if qy.data == "admin_stats":
        q("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        await qy.message.reply_text(f"👥 تعداد کاربران: {count}")

    elif qy.data == "admin_latest_users":
        if USING_PG:
            q("SELECT user_id, full_name, username FROM users ORDER BY last_seen DESC NULLS LAST LIMIT 15")
        else:
            q("SELECT user_id, full_name, username FROM users ORDER BY last_seen DESC LIMIT 15")
        rows = cur.fetchall()
        if not rows:
            await qy.message.reply_text("هنوز کاربری ثبت نشده.")
            return
        out = []
        for user_id, full_name, username in rows:
            out.append(f"👤 {full_name or '-'}\nID: {user_id}\nUsername: @{username}" if username else f"👤 {full_name or '-'}\nID: {user_id}\nUsername: -")
        await qy.message.reply_text("🆕 ۱۵ کاربر آخر:\n\n" + "\n\n".join(out))

    elif qy.data == "admin_search":
        admin_search_state.add(uid)
        await qy.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif qy.data == "admin_anon_send":
        admin_anon_target_state.add(uid)
        await qy.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif qy.data == "admin_broadcast":
        admin_broadcast_state.add(uid)
        await qy.message.reply_text("پیام همگانی رو بفرست:")

    elif qy.data == "admin_settings":
        await qy.message.reply_text("⚙️ تنظیمات", reply_markup=admin_settings_menu())

    elif qy.data == "toggle_force_join":
        set_bool("force_join_enabled", not get_bool("force_join_enabled", False))
        await qy.message.reply_text("✅ ذخیره شد.", reply_markup=admin_settings_menu())

    elif qy.data == "set_force_join_channel":
        admin_set_channel_state.add(uid)
        await qy.message.reply_text("یوزرنیم کانال رو بفرست (مثل @mychannel) یا -100... :")

    elif qy.data == "set_force_join_link":
        admin_set_link_state.add(uid)
        await qy.message.reply_text("لینک کانال رو بفرست (مثل https://t.me/mychannel):")

    elif qy.data == "back_admin":
        await qy.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_menu())

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    save_user(u)

    # join check for normal users
    if uid not in ADMIN_IDS:
        if not await must_join(update, context):
            return
        return  # فعلاً کاربران عادی منوی خاصی ندارن (رباتت را اگر خواستی می‌چسبونیم به سیستم قبلی)

    # ---------- admin set join channel/link ----------
    if uid in admin_set_channel_state:
        admin_set_channel_state.remove(uid)
        txt = (update.message.text or "").strip()
        if not txt:
            await update.message.reply_text("❌ مقدار معتبر نیست.")
            return
        set_setting("force_join_channel", txt)
        await update.message.reply_text("✅ کانال ذخیره شد.", reply_markup=admin_settings_menu())
        return

    if uid in admin_set_link_state:
        admin_set_link_state.remove(uid)
        txt = (update.message.text or "").strip()
        if not txt:
            await update.message.reply_text("❌ مقدار معتبر نیست.")
            return
        set_setting("force_join_link", txt)
        await update.message.reply_text("✅ لینک ذخیره شد.", reply_markup=admin_settings_menu())
        return

    # ---------- admin anonymous send flow ----------
    if uid in admin_anon_target_state:
        txt = (update.message.text or "").strip()
        if not txt.isdigit():
            await update.message.reply_text("فقط آیدی عددی بفرست.")
            return
        admin_anon_target_state.remove(uid)
        admin_anon_message_state[uid] = int(txt)
        await update.message.reply_text("متن پیام ناشناس رو بفرست:")
        return

    if uid in admin_anon_message_state:
        target = admin_anon_message_state.pop(uid)
        msg_text = extract_content(update)
        try:
            await context.bot.send_message(chat_id=target, text=msg_text)
            save_message(uid, target, "admin_anonymous", msg_text)
            await update.message.reply_text("✅ پیام ناشناس ارسال شد.")
        except Exception:
            await update.message.reply_text("❌ ارسال نشد (ممکنه کاربر بات رو استاپ کرده باشه).")
        return

    # ---------- admin search ----------
    if uid in admin_search_state and update.message.text and update.message.text.isdigit():
        admin_search_state.remove(uid)
        target = int(update.message.text)

        if USING_PG:
            q(
                "SELECT sender_id, receiver_id, msg_type, content, ts FROM messages "
                "WHERE sender_id=%s OR receiver_id=%s ORDER BY ts DESC LIMIT 50",
                (target, target)
            )
        else:
            q(
                "SELECT sender_id, receiver_id, msg_type, content, ts FROM messages "
                "WHERE sender_id=? OR receiver_id=? ORDER BY ts DESC LIMIT 50",
                (target, target)
            )
        rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("پیامی ثبت نشده")
            return
        for sender_id, receiver_id, msg_type, content, ts in rows:
            await update.message.reply_text(
                f"📩 {ts}\nاز {sender_id} به {receiver_id}\nنوع: {msg_type}\nمحتوا: {content or '(بدون متن)'}"
            )
        return

    # ---------- admin broadcast ----------
    if uid in admin_broadcast_state:
        admin_broadcast_state.remove(uid)
        msg_id = update.message.message_id

        q("SELECT user_id FROM users WHERE is_admin=0")
        users = cur.fetchall()
        for (chat_id,) in users:
            try:
                await context.bot.copy_message(chat_id=chat_id, from_chat_id=uid, message_id=msg_id)
            except Exception:
                pass
        await update.message.reply_text("✅ پیام همگانی ارسال شد")
        return

def run_bot():
    while True:
        try:
            app = (
                ApplicationBuilder()
                .token(TOKEN)
                .connect_timeout(30)
                .read_timeout(90)
                .write_timeout(90)
                .pool_timeout(30)
                .build()
            )
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CallbackQueryHandler(buttons))
            app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
            app.run_polling(drop_pending_updates=True, close_loop=False, poll_interval=1.0)

        except NetworkError as e:
            print("NetworkError, reconnecting...", repr(e))
            time.sleep(5)
        except Exception as e:
            print("Unexpected error, restarting bot...", repr(e))
            time.sleep(5)

