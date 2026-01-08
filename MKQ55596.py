import os
import sqlite3
import time
import traceback

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

# ---------------- TOKEN LOADER ----------------
def load_token() -> str:
    token = os.environ.get("BOT_TOKEN")
    if token and token.strip():
        return token.strip()

    for name in ("Token.txt", "token.txt"):
        try:
            with open(name, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
        except FileNotFoundError:
            pass

    raise ValueError("BOT_TOKEN is not set and Token.txt/token.txt not found or empty")


# ---------------- DATABASE URL LOADER ----------------
def load_database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url and url.strip():
        return url.strip()

    for name in ("Database.txt", "database.txt"):
        try:
            with open(name, "r", encoding="utf-8") as f:
                u = f.read().strip()
                if u:
                    return u
        except FileNotFoundError:
            pass

    return None


TOKEN = load_token()
DATABASE_URL = load_database_url()
USING_PG = bool(DATABASE_URL)

# ---------- ADMIN ----------
ADMIN_IDS = {6474515118}

# =========================
#   DATABASE (Postgres via psycopg3 + auto reconnect OR SQLite fallback)
# =========================
cur = None
db = None

def now_ts() -> int:
    return int(time.time())


if USING_PG:
    import psycopg
    from psycopg import OperationalError

    def db_connect():
        global db, cur
        db = psycopg.connect(DATABASE_URL)
        db.autocommit = True
        cur = db.cursor()

    db_connect()

    def q(sql: str, params=None):
        global cur
        try:
            cur.execute(sql, params or ())
        except OperationalError as e:
            msg = str(e).lower()
            if (
                "connection is lost" in msg
                or "closed" in msg
                or "terminated" in msg
                or "server closed the connection" in msg
                or "connection not open" in msg
            ):
                db_connect()
                cur.execute(sql, params or ())
                return
            raise

else:
    db = sqlite3.connect("bot.db", check_same_thread=False)
    cur = db.cursor()

    def q(sql: str, params=None):
        cur.execute(sql, params or ())
        db.commit()


# ---------- schema ----------
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


# ---------- settings helpers ----------
def set_setting(key: str, value: str):
    if USING_PG:
        q(
            "INSERT INTO settings(key,value) VALUES(%s,%s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            (key, value)
        )
    else:
        q(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )

def get_setting(key: str, default: str = "") -> str:
    if USING_PG:
        q("SELECT value FROM settings WHERE key=%s", (key,))
    else:
        q("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else default

def get_bool_setting(key: str, default: bool = False) -> bool:
    v = get_setting(key, "1" if default else "0")
    return v == "1"

def set_bool_setting(key: str, value: bool):
    set_setting(key, "1" if value else "0")


# defaults
if get_setting("force_join_channel", "") == "":
    set_setting("force_join_channel", "@YOUR_CHANNEL")
if get_setting("force_join_link", "") == "":
    set_setting("force_join_link", "https://t.me/YOUR_CHANNEL")
if get_setting("force_join_enabled", "") == "":
    set_bool_setting("force_join_enabled", False)


# ---------- data helpers ----------
def save_user(user):
    full_name = (user.full_name or "").strip()
    username = (user.username or "").strip() if user.username else None
    is_admin = int(user.id in ADMIN_IDS)
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
        """, (user.id, username, full_name, is_admin, ts))
    else:
        q("""
            INSERT INTO users (user_id, username, full_name, is_admin, last_seen)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              full_name=excluded.full_name,
              is_admin=excluded.is_admin,
              last_seen=excluded.last_seen
        """, (user.id, username, full_name, is_admin, ts))


def save_message(sender, receiver, msg_type, content=None):
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


# ---------- FORCE JOIN CHECK ----------
async def must_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user and update.effective_user.id in ADMIN_IDS:
        return True

    enabled = get_bool_setting("force_join_enabled", False)
    if not enabled:
        return True

    channel = get_setting("force_join_channel", "@YOUR_CHANNEL")
    link = get_setting("force_join_link", "https://t.me/YOUR_CHANNEL")

    try:
        member = await context.bot.get_chat_member(channel, update.effective_user.id)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True
    except Exception:
        pass

    text = f"برای استفاده از ربات اول باید عضو کانال بشی:\n{link}"
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text)
    return False


# ---------- helper: find last owner from DB (for reply/block permissions + send_again) ----------
def get_last_owner_for_sender(sender_id: int) -> int | None:
    try:
        if USING_PG:
            q(
                "SELECT receiver_id FROM messages WHERE sender_id=%s AND msg_type=%s ORDER BY ts DESC LIMIT 1",
                (sender_id, "forward")
            )
        else:
            q(
                "SELECT receiver_id FROM messages WHERE sender_id=? AND msg_type=? ORDER BY ts DESC LIMIT 1",
                (sender_id, "forward")
            )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


# ---------- STATES ----------
user_links = {}        # user_id -> owner_id (active link mode)
reply_state = {}       # replier_id -> target_sender_id
blocked = {}           # owner_id -> set(user_ids)
send_direct_state = set()

admin_search_state = set()
admin_broadcast_state = set()
admin_set_channel_state = set()
admin_set_link_state = set()

# admin anonymous send
admin_anon_target_state = set()
admin_anon_message_state = {}  # admin_id -> target_user_id

# ✅ for fixing send_again + permissions
last_owner_map = {}              # sender_id -> owner_id (memory quick path)
last_link_owner_for_user = {}    # user_id -> owner_id (for send_again after link usage)
last_reply_target_for_owner = {} # owner_id -> target_sender_id (برای سازگاری، اگر جایی لازم شد)


# ---------- MENUS ----------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 دریافت لینک اختصاصی", callback_data="get_link")],
        [InlineKeyboardButton("✉️ ارسال پیام مستقیم", callback_data="send_direct")]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 آمار کاربران", callback_data="admin_stats")],
        [InlineKeyboardButton("🆕 ۱۵ کاربر آخر", callback_data="admin_latest_users")],
        [InlineKeyboardButton("🔍 مشاهده پیام‌های کاربر (با محتوا)", callback_data="admin_search")],
        [InlineKeyboardButton("✉️ پیام ناشناس به کاربر", callback_data="admin_anon_send")],
        [InlineKeyboardButton("📢 ارسال پیام به همه", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ تنظیمات جوین اجباری", callback_data="admin_settings")],
    ])

def admin_settings_menu():
    enabled = get_bool_setting("force_join_enabled", False)
    status_text = "روشن ✅" if enabled else "خاموش ❌"
    ch = get_setting("force_join_channel", "@YOUR_CHANNEL")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔒 جوین اجباری: {status_text}", callback_data="toggle_force_join")],
        [InlineKeyboardButton(f"📢 تنظیم کانال (فعلی: {ch})", callback_data="set_force_join_channel")],
        [InlineKeyboardButton("🔗 تنظیم لینک کانال", callback_data="set_force_join_link")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_admin")],
    ])

def after_send_menu(mode: str, target_id: int) -> InlineKeyboardMarkup:
    """
    mode:
      - "user_link": کاربر عادی → دوباره به همون owner لینک ناشناس
      - "owner_reply": ادمین / owner → دوباره به همون کاربر
    target_id:
      - اگر mode == user_link → owner_id
      - اگر mode == owner_reply → user_id (فرستنده)
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✉️ ارسال دوباره پیام",
                callback_data=f"send_again|{mode}|{target_id}"
            )
        ],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_menu")]
    ])


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    # start with link: /start <owner_id>
    if context.args:
        if not await must_join(update, context):
            return

        owner_id = int(context.args[0])
        if owner_id in blocked and user.id in blocked[owner_id]:
            return

        user_links[user.id] = owner_id
        last_link_owner_for_user[user.id] = owner_id
        await update.message.reply_text("پیامت رو بفرست ✉️")
        return

    if user.id in ADMIN_IDS:
        await update.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_menu())
        return

    if not await must_join(update, context):
        return

    await update.message.reply_text("سلام 👋", reply_markup=main_menu())


# ---------- BUTTONS ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qy = update.callback_query
    await qy.answer()
    uid = qy.from_user.id

    # join check for normal users on usage actions
    if uid not in ADMIN_IDS:
        if qy.data in ("get_link", "send_direct", "back_menu") or qy.data.startswith("send_again"):
            if not await must_join(update, context):
                return

    if qy.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await qy.message.reply_text(link)

    elif qy.data == "send_direct":
        send_direct_state.add(uid)
        await qy.message.reply_text("آیدی عددی مخاطب رو بفرست:")

    elif qy.data.startswith("send_again|"):
        # ساختار: send_again|<mode>|<target_id>
        try:
            _, mode, target_str = qy.data.split("|", 2)
            target = int(target_str)
        except Exception:
            await qy.message.reply_text("❌ داده دکمه مشکل داره.")
            return

        if mode == "user_link":
            # کاربر می‌خواد دوباره برای همون owner لینک ناشناس پیام بفرسته
            if uid in ADMIN_IDS:
                await qy.message.reply_text("این دکمه مخصوص کاربره.")
                return

            user_links[uid] = target  # target = owner_id
            last_link_owner_for_user[uid] = target
            await qy.message.reply_text("پیامت رو بفرست ✉️")
            return

        if mode == "owner_reply":
            # ادمین یا owner دوباره می‌خواد به همون کاربر جواب بده
            owner_of_sender = last_owner_map.get(target) or get_last_owner_for_sender(target)
            if uid not in ADMIN_IDS and uid != owner_of_sender:
                await qy.message.reply_text("⛔️ اجازه نداری.")
                return

            reply_state[uid] = target  # target = sender_id
            last_reply_target_for_owner[uid] = target
            await qy.message.reply_text("پاسخت رو بفرست ✉️")
            return

        # اگر mode چیز دیگه‌ای بود:
        await qy.message.reply_text("❌ حالت ناشناخته.")
        return

    elif qy.data == "back_menu":
        user_links.pop(uid, None)
        await qy.message.reply_text("منوی اصلی 👇", reply_markup=main_menu())

    # -------- ADMIN --------
    elif qy.data == "admin_stats":
        if uid not in ADMIN_IDS:
            return
        q("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        await qy.message.reply_text(f"👥 تعداد کاربران: {count}")

    elif qy.data == "admin_latest_users":
        if uid not in ADMIN_IDS:
            return
        if USING_PG:
            q("SELECT user_id, full_name, username FROM users ORDER BY last_seen DESC NULLS LAST LIMIT 15")
        else:
            q("SELECT user_id, full_name, username FROM users ORDER BY last_seen DESC LIMIT 15")
        rows = cur.fetchall()
        if not rows:
            await qy.message.reply_text("هنوز کاربری ثبت نشده.")
            return
        lines = []
        for user_id, full_name, username in rows:
            name = full_name if full_name else "-"
            uname = f"@{username}" if username else "-"
            lines.append(f"👤 {name}\nID: {user_id}\nUsername: {uname}\n")
        await qy.message.reply_text("🆕 ۱۵ کاربر آخر:\n\n" + "\n".join(lines))

    elif qy.data == "admin_search":
        if uid not in ADMIN_IDS:
            return
        admin_search_state.add(uid)
        await qy.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif qy.data == "admin_anon_send":
        if uid not in ADMIN_IDS:
            return
        admin_anon_target_state.add(uid)
        await qy.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif qy.data == "admin_broadcast":
        if uid not in ADMIN_IDS:
            return
        admin_broadcast_state.add(uid)
        await qy.message.reply_text("پیام همگانی رو بفرست:")

    elif qy.data == "admin_settings":
        if uid not in ADMIN_IDS:
            return
        await qy.message.reply_text("⚙️ تنظیمات جوین اجباری", reply_markup=admin_settings_menu())

    elif qy.data == "toggle_force_join":
        if uid not in ADMIN_IDS:
            return
        set_bool_setting("force_join_enabled", not get_bool_setting("force_join_enabled", False))
        await qy.message.reply_text("✅ ذخیره شد.", reply_markup=admin_settings_menu())

    elif qy.data == "set_force_join_channel":
        if uid not in ADMIN_IDS:
            return
        admin_set_channel_state.add(uid)
        await qy.message.reply_text("یوزرنیم کانال رو بفرست (مثل @mychannel) یا -100...:")

    elif qy.data == "set_force_join_link":
        if uid not in ADMIN_IDS:
            return
        admin_set_link_state.add(uid)
        await qy.message.reply_text("لینک کانال رو بفرست (مثل https://t.me/mychannel):")

    elif qy.data == "back_admin":
        if uid not in ADMIN_IDS:
            return
        await qy.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_menu())

    elif qy.data.startswith("reply_"):
        target_sender = int(qy.data.split("_")[1])

        # ✅ FIX: allow admin OR owner who received the message
        owner_of_sender = last_owner_map.get(target_sender) or get_last_owner_for_sender(target_sender)
        if uid not in ADMIN_IDS and uid != owner_of_sender:
            await qy.message.reply_text("⛔️ اجازه نداری.")
            return

        reply_state[uid] = target_sender
        last_reply_target_for_owner[uid] = target_sender
        await qy.message.reply_text("پاسخت رو بفرست:")

    elif qy.data.startswith("block_"):
        target_sender = int(qy.data.split("_")[1])

        # ✅ FIX: allow admin OR owner who received the message
        owner_of_sender = last_owner_map.get(target_sender) or get_last_owner_for_sender(target_sender)
        if uid not in ADMIN_IDS and uid != owner_of_sender:
            await qy.message.reply_text("⛔️ اجازه نداری.")
            return

        blocked.setdefault(uid, set()).add(target_sender)
        await qy.message.reply_text("🚫 کاربر بلاک شد")


# ---------- MESSAGE HANDLER ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    save_user(user)

    # join check for normal users
    if uid not in ADMIN_IDS:
        if not await must_join(update, context):
            return

    # admin set channel/link
    if uid in ADMIN_IDS and uid in admin_set_channel_state:
        admin_set_channel_state.remove(uid)
        txt = (update.message.text or "").strip()
        if not txt:
            await update.message.reply_text("❌ مقدار معتبر نیست.")
            return
        set_setting("force_join_channel", txt)
        await update.message.reply_text("✅ کانال ذخیره شد.", reply_markup=admin_settings_menu())
        return

    if uid in ADMIN_IDS and uid in admin_set_link_state:
        admin_set_link_state.remove(uid)
        txt = (update.message.text or "").strip()
        if not txt:
            await update.message.reply_text("❌ مقدار معتبر نیست.")
            return
        set_setting("force_join_link", txt)
        await update.message.reply_text("✅ لینک ذخیره شد.", reply_markup=admin_settings_menu())
        return

    # admin anonymous send flow
    if uid in ADMIN_IDS and uid in admin_anon_target_state:
        txt = (update.message.text or "").strip()
        if txt.isdigit():
            admin_anon_target_state.remove(uid)
            admin_anon_message_state[uid] = int(txt)
            await update.message.reply_text("متن پیام ناشناس رو بفرست:")
        else:
            await update.message.reply_text("فقط آیدی عددی بفرست.")
        return

    if uid in ADMIN_IDS and uid in admin_anon_message_state:
        target = admin_anon_message_state.pop(uid)
        msg_text = extract_content(update)
        try:
            await context.bot.send_message(chat_id=target, text=msg_text)
            save_message(uid, target, "admin_anonymous", msg_text)
            await update.message.reply_text(
                "✅ پیام ناشناس ارسال شد.",
                reply_markup=after_send_menu("owner_reply", target)
            )
        except Exception:
            await update.message.reply_text("❌ ارسال نشد (ممکنه کاربر بات رو استاپ کرده باشه).")
        return

    # admin search show content
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
                f"📩 {ts}\nاز {sender_id} به {receiver_id}\nنوع: {msg_type}\nمحتوا: {content or '(بدون متن/فایل)'}"
            )
        return

    # broadcast
    if uid in admin_broadcast_state:
        admin_broadcast_state.remove(uid)
        q("SELECT user_id FROM users WHERE is_admin=0")
        users = cur.fetchall()
        for (u2,) in users:
            try:
                await context.bot.copy_message(
                    chat_id=u2,
                    from_chat_id=uid,
                    message_id=update.message.message_id
                )
            except Exception:
                pass
        await update.message.reply_text("✅ پیام همگانی ارسال شد")
        return

    # reply flow (admin OR owner)
    if uid in reply_state:
        target_sender = reply_state.pop(uid)
        last_reply_target_for_owner[uid] = target_sender

        await context.bot.copy_message(
            chat_id=target_sender,
            from_chat_id=uid,
            message_id=update.message.message_id
        )
        save_message(uid, target_sender, "reply", extract_content(update))
        await update.message.reply_text(
            "✅ پاسخ ارسال شد",
            reply_markup=after_send_menu("owner_reply", target_sender)
        )
        return

    # send_direct flow (simple)
    if uid in send_direct_state:
        if update.message.text and update.message.text.isdigit():
            target = int(update.message.text)
            send_direct_state.remove(uid)
            # store in reply_state-like temporary to send next message
            reply_state[uid] = target
            last_reply_target_for_owner[uid] = target
            await update.message.reply_text("پیامت رو بفرست:")
        else:
            await update.message.reply_text("فقط آیدی عددی بفرست.")
        return

    # user via link -> forward to owner
    if uid in user_links:
        owner = user_links[uid]

        # blocked check
        if owner in blocked and uid in blocked[owner]:
            return

        await context.bot.forward_message(
            chat_id=owner,
            from_chat_id=uid,
            message_id=update.message.message_id
        )

        await context.bot.send_message(
            chat_id=owner,
            text=f"👤 فرستنده:\nID: {uid}\nUsername: @{user.username}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✉️ پاسخ", callback_data=f"reply_{uid}"),
                    InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{uid}")
                ]
            ])
        )

        save_message(uid, owner, "forward", extract_content(update))

        # ✅ remember mapping for permission + send_again
        last_owner_map[uid] = owner
        last_link_owner_for_user[uid] = owner

        # we end this one-shot session (like your original logic)
        user_links.pop(uid, None)

        await update.message.reply_text(
            "✅ پیام ارسال شد",
            reply_markup=after_send_menu("user_link", owner)
        )
        return


# ---------- PTB ERROR HANDLER ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("PTB ERROR:", repr(context.error))
    traceback.print_exc()


# ---------- MAIN (RECONNECT SAFE) ----------
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
            app.add_error_handler(on_error)

            app.run_polling(drop_pending_updates=True, close_loop=False, poll_interval=1.0)

        except NetworkError as e:
            print("NetworkError, reconnecting...", repr(e))
            time.sleep(5)
        except Exception as e:
            print("BOT LOOP CRASH:", repr(e))
            time.sleep(5)
