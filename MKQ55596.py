import os
import sqlite3
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q")  # ✅ از Render ENV
if not TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")

ADMIN_IDS = {6474515118}  # آیدی عددی خودت

# ---------- DATABASE ----------
db = sqlite3.connect("bot.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    is_admin INTEGER
)
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER,
    receiver_id INTEGER,
    msg_type TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()


def save_user(user):
    cur.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?, ?)",
        (user.id, user.username, int(user.id in ADMIN_IDS))
    )
    db.commit()


def save_message(sender, receiver, msg_type):
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, msg_type) VALUES (?, ?, ?)",
        (sender, receiver, msg_type)
    )
    db.commit()


# ---------- STATES ----------
user_links = {}
reply_state = {}
blocked = {}
send_direct_state = set()
admin_search_state = set()
admin_broadcast_state = set()


# ---------- MENUS ----------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 دریافت لینک اختصاصی", callback_data="get_link")],
        [InlineKeyboardButton("✉️ ارسال پیام مستقیم", callback_data="send_direct")]
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 آمار کاربران", callback_data="admin_stats")],
        [InlineKeyboardButton("🔍 مشاهده پیام‌های کاربر", callback_data="admin_search")],
        [InlineKeyboardButton("📢 ارسال پیام به همه", callback_data="admin_broadcast")]
    ])


def after_send_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ ارسال دوباره پیام", callback_data="send_again")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_menu")]
    ])


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if context.args:
        owner_id = int(context.args[0])
        if owner_id in blocked and user.id in blocked[owner_id]:
            return
        user_links[user.id] = owner_id
        await update.message.reply_text("پیامت رو بفرست ✉️")
        return

    if user.id in ADMIN_IDS:
        await update.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_menu())
        return

    await update.message.reply_text("سلام 👋", reply_markup=main_menu())


# ---------- BUTTONS ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

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
        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        await q.message.reply_text(f"👥 تعداد کاربران: {count}")

    elif q.data == "admin_search":
        admin_search_state.add(uid)
        await q.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif q.data == "admin_broadcast":
        admin_broadcast_state.add(uid)
        await q.message.reply_text("پیام همگانی رو بفرست:")

    elif q.data.startswith("reply_"):
        target = int(q.data.split("_")[1])
        reply_state[uid] = target
        await q.message.reply_text("پاسخت رو بفرست:")

    elif q.data.startswith("block_"):
        target = int(q.data.split("_")[1])
        blocked.setdefault(uid, set()).add(target)
        await q.message.reply_text("🚫 کاربر بلاک شد")


# ---------- MESSAGE HANDLER ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    save_user(user)

    if uid in admin_search_state and update.message.text and update.message.text.isdigit():
        admin_search_state.remove(uid)
        target = int(update.message.text)

        cur.execute("""
        SELECT sender_id, receiver_id, msg_type, timestamp
        FROM messages
        WHERE sender_id=? OR receiver_id=?
        ORDER BY timestamp DESC
        """, (target, target))
        rows = cur.fetchall()

        if not rows:
            await update.message.reply_text("پیامی ثبت نشده")
            return

        for r in rows:
            await update.message.reply_text(f"📩 {r[3]}\nاز {r[0]} به {r[1]}\nنوع: {r[2]}")
        return

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

    if uid in reply_state:
        target = reply_state.pop(uid)

        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=uid,
            message_id=update.message.message_id
        )
        save_message(uid, target, "reply")
        await update.message.reply_text("✅ پاسخ ارسال شد", reply_markup=after_send_menu())
        return

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

        save_message(uid, owner, "forward")
        user_links.pop(uid, None)
        await update.message.reply_text("✅ پیام ارسال شد", reply_markup=after_send_menu())
        return


# ---------- MAIN ----------
def run_bot():
    # ✅ Fix for Python 3.13 + threads: create an event loop in this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    app.run_polling()
