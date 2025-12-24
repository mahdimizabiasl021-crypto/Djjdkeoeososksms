import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q"
ADMIN_IDS = {7986263531}

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
finished_state = set()


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if user.id in ADMIN_IDS:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 آمار کاربران", callback_data="stats_users")],
            [InlineKeyboardButton("📊 گزارش کلی", callback_data="stats_global")],
            [InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="admin_search")]
        ])
        await update.message.reply_text("🛠 پنل مدیریت", reply_markup=kb)
        return

    if context.args:
        owner = int(context.args[0])
        user_links[user.id] = owner
        finished_state.discard(user.id)
        await update.message.reply_text("پیامت رو بفرست ✉️")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 لینک اختصاصی", callback_data="get_link")],
        [InlineKeyboardButton("✉️ ارسال پیام مستقیم", callback_data="send_direct")]
    ])
    await update.message.reply_text("سلام 👋", reply_markup=kb)


# ---------- BUTTONS ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if q.data == "stats_users":
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM messages")
        msgs = cur.fetchone()[0]

        await q.message.reply_text(
            f"👥 کاربران: {users}\n✉️ پیام‌ها: {msgs}"
        )

    elif q.data == "stats_global":
        cur.execute("""
        SELECT sender_id, COUNT(*) as c
        FROM messages
        GROUP BY sender_id
        ORDER BY c DESC
        LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            await q.message.reply_text(
                f"🔥 فعال‌ترین کاربر:\nID: {row[0]}\nپیام‌ها: {row[1]}"
            )
        else:
            await q.message.reply_text("داده‌ای موجود نیست")

    elif q.data == "admin_search":
        context.user_data["wait_id"] = True
        await q.message.reply_text("آیدی عددی کاربر رو بفرست:")

    elif q.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.message.reply_text(link)


# ---------- MESSAGE HANDLER ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    save_user(user)

    if uid in ADMIN_IDS and context.user_data.get("wait_id"):
        context.user_data["wait_id"] = False
        target = int(update.message.text)

        cur.execute("""
        SELECT sender_id, receiver_id, msg_type, timestamp
        FROM messages
        WHERE sender_id=? OR receiver_id=?
        ORDER BY timestamp DESC
        LIMIT 10
        """, (target, target))

        rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("پیامی یافت نشد")
            return

        for r in rows:
            await update.message.reply_text(
                f"📩 {r[3]}\nاز {r[0]} به {r[1]}\nنوع: {r[2]}"
            )
        return

    if uid in finished_state:
        await update.message.reply_text("❗ یکی از گزینه‌ها رو انتخاب کن")
        return

    if uid in user_links:
        owner = user_links[uid]
        await context.bot.forward_message(
            chat_id=owner,
            from_chat_id=uid,
            message_id=update.message.message_id
        )

        save_message(uid, owner, update.message.effective_attachment.__class__.__name__)
        finished_state.add(uid)
        await update.message.reply_text("✅ پیام ارسال شد")


# ---------- MAIN ----------
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
app.run_polling()
