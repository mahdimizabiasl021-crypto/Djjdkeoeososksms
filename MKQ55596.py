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


TOKEN = load_token()

# ---------- ADMIN ----------
ADMIN_IDS = {6474515118}

# ---------- DATABASE ----------
db = sqlite3.connect("bot.db", check_same_thread=False)
cur = db.cursor()

# users: ذخیره نام و یوزرنیم برای لیست ۱۵ نفر آخر
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_admin INTEGER
)
""")

# messages: ذخیره محتوا
cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER,
    receiver_id INTEGER,
    msg_type TEXT,
    content TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

# settings: تنظیمات پنل ادمین
cur.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
db.commit()

# مهاجرت‌ها (اگر از قبل جدول‌ها با ستون کمتر ساخته شده بودند)
try:
    cur.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
    db.commit()
except Exception:
    pass

try:
    cur.execute("ALTER TABLE messages ADD COLUMN content TEXT")
    db.commit()
except Exception:
    pass


def set_setting(key: str, value: str):
    cur.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
    )
    db.commit()


def get_setting(key: str, default: str = "") -> str:
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else default


def get_bool_setting(key: str, default: bool = False) -> bool:
    v = get_setting(key, "1" if default else "0")
    return v == "1"


def set_bool_setting(key: str, value: bool):
    set_setting(key, "1" if value else "0")


# defaults (یک بار)
if get_setting("force_join_channel", "") == "":
    set_setting("force_join_channel", "@YOUR_CHANNEL")
if get_setting("force_join_link", "") == "":
    set_setting("force_join_link", "https://t.me/YOUR_CHANNEL")
if get_setting("force_join_enabled", "") == "":
    set_bool_setting("force_join_enabled", False)


def save_user(user):
    full_name = (user.full_name or "").strip()
    username = (user.username or "").strip() if user.username else None

    cur.execute("""
    INSERT INTO users (user_id, username, full_name, is_admin)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username,
        full_name=excluded.full_name,
        is_admin=excluded.is_admin
    """, (user.id, username, full_name, int(user.id in ADMIN_IDS)))
    db.commit()


def save_message(sender, receiver, msg_type, content=None):
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, msg_type, content) VALUES (?, ?, ?, ?)",
        (sender, receiver, msg_type, content)
    )
    db.commit()


# ---------- STATES ----------
user_links = {}
reply_state = {}
blocked = {}
send_direct_state = set()  # (همچنان مثل قبل نگه داشته شده)
admin_search_state = set()
admin_broadcast_state = set()

admin_set_channel_state = set()
admin_set_link_state = set()


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
        [InlineKeyboardButton("🔍 مشاهده پیام‌های کاربر", callback_data="admin_search")],
        [InlineKeyboardButton("📢 ارسال پیام به همه", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ تنظیمات ربات", callback_data="admin_settings")],
    ])


def admin_settings_menu():
    enabled = get_bool_setting("force_join_enabled", False)
    status_text = "روشن ✅" if enabled else "خاموش ❌"
    channel = get_setting("force_join_channel", "@YOUR_CHANNEL")
    link = get_setting("force_join_link", "https://t.me/YOUR_CHANNEL")

    # فقط برای نمایش سریع
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔒 جوین اجباری: {status_text}", callback_data="toggle_force_join")],
        [InlineKeyboardButton("📢 تنظیم کانال جوین اجباری", callback_data="set_force_join_channel")],
        [InlineKeyboardButton("🔗 تنظیم لینک کانال", callback_data="set_force_join_link")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_admin")],
    ])


def after_send_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ ارسال دوباره پیام", callback_data="send_again")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_menu")]
    ])


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


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    # start with link
    if context.args:
        if not await must_join(update, context):
            return

        owner_id = int(context.args[0])
        if owner_id in blocked and user.id in blocked[owner_id]:
            return
        user_links[user.id] = owner_id
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
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    # join check for normal users on usage actions
    if uid not in ADMIN_IDS:
        if q.data in ("get_link", "send_direct", "send_again", "back_menu"):
            if not await must_join(update, context):
                return

    if q.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.message.reply_text(link)

    elif q.data == "send_direct":
        send_direct_state.add(uid)
        await q.message.reply_text("آیدی عددی مخاطب رو بفرست:")

    elif q.data == "send_again":
        await q.message.reply_text("پیامت رو بفرست:")

    elif q.data == "back_menu":
        user_links.pop(uid, None)
        await q.message.reply_text("منوی اصلی 👇", reply_markup=main_menu())

    elif q.data == "admin_stats":
        if uid not in ADMIN_IDS:
            return
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        await q.message.reply_text(f"👥 تعداد کاربران: {count}")

    elif q.data == "admin_latest_users":
        if uid not in ADMIN_IDS:
            return
        cur.execute("""
            SELECT user_id, full_name, username
            FROM users
            ORDER BY rowid DESC
            LIMIT 15
        """)
        rows = cur.fetchall()
        if not rows:
            await q.message.reply_text("هنوز کاربری ثبت نشده.")
            return

        lines = []
        for user_id, full_name, username in rows:
            name = full_name if full_name else "-"
            uname = f"@{username}" if username else "-"
            lines.append(f"👤 {name}\nID: {user_id}\nUsername: {uname}\n")

        await q.message.reply_text("🆕 ۱۵ کاربر آخر:\n\n" + "\n".join(lines))

    elif q.data == "admin_search":
        if uid not in ADMIN_IDS:
            return
        admin_search_state.add(uid)
        await q.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif q.data == "admin_broadcast":
        if uid not in ADMIN_IDS:
            return
        admin_broadcast_state.add(uid)
        await q.message.reply_text("پیام همگانی رو بفرست:")

    elif q.data.startswith("reply_"):
        if uid not in ADMIN_IDS:
            return
        target = int(q.data.split("_")[1])
        reply_state[uid] = target
        await q.message.reply_text("پاسخت رو بفرست:")

    elif q.data.startswith("block_"):
        if uid not in ADMIN_IDS:
            return
        target = int(q.data.split("_")[1])
        blocked.setdefault(uid, set()).add(target)
        await q.message.reply_text("🚫 کاربر بلاک شد")

    # ---------- ADMIN SETTINGS ----------
    elif q.data == "admin_settings":
        if uid not in ADMIN_IDS:
            return
        await q.message.reply_text("⚙️ تنظیمات ربات", reply_markup=admin_settings_menu())

    elif q.data == "toggle_force_join":
        if uid not in ADMIN_IDS:
            return
        cur_state = get_bool_setting("force_join_enabled", False)
        set_bool_setting("force_join_enabled", not cur_state)
        await q.message.reply_text("✅ ذخیره شد.", reply_markup=admin_settings_menu())

    elif q.data == "set_force_join_channel":
        if uid not in ADMIN_IDS:
            return
        admin_set_channel_state.add(uid)
        await q.message.reply_text("یوزرنیم کانال رو بفرست (مثل @mychannel) یا آیدی عددی کانال (مثل -100...):")

    elif q.data == "set_force_join_link":
        if uid not in ADMIN_IDS:
            return
        admin_set_link_state.add(uid)
        await q.message.reply_text("لینک کانال رو بفرست (مثل https://t.me/mychannel):")

    elif q.data == "back_admin":
        if uid not in ADMIN_IDS:
            return
        await q.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_menu())


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

    # admin search show content
    if uid in admin_search_state and update.message.text and update.message.text.isdigit():
        admin_search_state.remove(uid)
        target = int(update.message.text)

        cur.execute("""
        SELECT sender_id, receiver_id, msg_type, content, timestamp
        FROM messages
        WHERE sender_id=? OR receiver_id=?
        ORDER BY timestamp DESC
        LIMIT 50
        """, (target, target))
        rows = cur.fetchall()

        if not rows:
            await update.message.reply_text("پیامی ثبت نشده")
            return

        for r in rows:
            content = r[3] if r[3] else "(بدون متن/فایل)"
            await update.message.reply_text(
                f"📩 {r[4]}\nاز {r[0]} به {r[1]}\nنوع: {r[2]}\nمحتوا: {content}"
            )
        return

    # admin broadcast
    if uid in admin_broadcast_state:
        admin_broadcast_state.remove(uid)
        cur.execute("SELECT user_id FROM users WHERE is_admin=0")
        users = cur.fetchall()

        for (u,) in users:
            try:
                await context.bot.copy_message(
                    chat_id=u,
                    from_chat_id=uid,
                    message_id=update.message.message_id
                )
            except Exception:
                pass

        await update.message.reply_text("✅ پیام همگانی ارسال شد")
        return

    # admin reply
    if uid in reply_state:
        target = reply_state.pop(uid)

        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=uid,
            message_id=update.message.message_id
        )

        content = extract_content(update)
        save_message(uid, target, "reply", content)

        await update.message.reply_text("✅ پاسخ ارسال شد", reply_markup=after_send_menu())
        return

    # user via link: forward to owner
    if uid in user_links:
        owner = user_links[uid]

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

        content = extract_content(update)
        save_message(uid, owner, "forward", content)

        user_links.pop(uid, None)
        await update.message.reply_text("✅ پیام ارسال شد", reply_markup=after_send_menu())
        return


# ---------- MAIN (RECONNECT SAFE) ----------
def run_bot():
    # ✅ اگر شبکه قطع شد (ReadError/NetworkError)، خودش دوباره وصل می‌شود
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

            app.run_polling(
                drop_pending_updates=True,
                close_loop=False,
                poll_interval=1.0,
            )

        except NetworkError as e:
            print("NetworkError, reconnecting...", repr(e))
            time.sleep(5)

        except Exception as e:
            print("Unexpected error, restarting bot...", repr(e))
            time.sleep(5)
