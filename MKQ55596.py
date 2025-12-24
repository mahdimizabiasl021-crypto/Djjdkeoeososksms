import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q"
ADMIN_IDS = {6474515118}

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
reply_state = {}      # owner_id -> (target_id, reply_to_message_id)
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
        owner_id = int(context.args[0])
        if owner_id in blocked and user.id in blocked[owner_id]:
            return
        user_links[user.id] = owner_id
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
    try:
        await q.answer()
    except:
        return

    uid = q.from_user.id

    if q.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.message.reply_text(link)

    elif q.data == "send_direct":
        send_direct_state.add(uid)
        finished_state.discard(uid)
        await q.message.reply_text("آیدی عددی مخاطب رو بفرست:")

    elif q.data.startswith("reply_"):
        _, target_id, reply_msg_id = q.data.split("_")
        reply_state[uid] = (int(target_id), int(reply_msg_id))
        finished_state.discard(uid)
        await q.message.reply_text("پاسخت رو بفرست:")

    elif q.data.startswith("block_"):
        target = int(q.data.split("_")[1])
        blocked.setdefault(uid, set()).add(target)
        await q.message.reply_text("🚫 کاربر بلاک شد")

    elif q.data == "send_again":
        finished_state.discard(uid)
        await q.message.reply_text("پیامت رو بفرست:")

    elif q.data == "back_menu":
        await start(update, context)

    elif q.data == "stats_users":
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM messages")
        msgs = cur.fetchone()[0]
        await q.message.reply_text(f"👥 کاربران: {users}\n✉️ پیام‌ها: {msgs}")

    elif q.data == "stats_global":
        cur.execute("""
        SELECT sender_id, COUNT(*) FROM messages
        GROUP BY sender_id ORDER BY COUNT(*) DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            await q.message.reply_text(f"🔥 فعال‌ترین کاربر:\nID: {row[0]}\nپیام‌ها: {row[1]}")
        else:
            await q.message.reply_text("داده‌ای موجود نیست")

    elif q.data == "admin_search":
        context.user_data["wait_id"] = True
        await q.message.reply_text("آیدی عددی کاربر رو بفرست:")


# ---------- MESSAGE HANDLER ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    save_user(user)

    action_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✉️ ارسال دوباره پیام", callback_data="send_again"),
            InlineKeyboardButton("🔙 بازگشت به منو اصلی", callback_data="back_menu")
        ]
    ])

    # ADMIN SEARCH RESULT
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
            await update.message.reply_text("پیامی ثبت نشده")
            return
        for r in rows:
            await update.message.reply_text(
                f"📩 {r[3]}\nاز {r[0]} به {r[1]}\nنوع: {r[2]}"
            )
        return

    # BLOCK EXTRA MESSAGE
    if uid in finished_state:
        await update.message.reply_text(
            "❗ یکی از گزینه‌ها رو انتخاب کن",
            reply_markup=action_kb
        )
        return

    # DIRECT SEND STEP
    if uid in send_direct_state and update.message.text.isdigit():
        reply_state[uid] = (int(update.message.text), None)
        send_direct_state.remove(uid)
        await update.message.reply_text("پیامت رو بفرست:")
        return

    # OWNER REPLY (COPY + REPLY)
    if uid in reply_state:
        target, reply_to = reply_state.pop(uid)
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=uid,
            message_id=update.message.message_id,
            reply_to_message_id=reply_to
        )
        save_message(uid, target, "reply")
        finished_state.add(uid)
        await update.message.reply_text("📩 پاسخ داده شد", reply_markup=action_kb)
        return

    # USER VIA LINK (FORWARD)
    if uid in user_links:
        owner = user_links[uid]

        fwd = await context.bot.forward_message(
            chat_id=owner,
            from_chat_id=uid,
            message_id=update.message.message_id
        )

        await context.bot.send_message(
            chat_id=owner,
            text=f"👤 فرستنده:\nID: {uid}\nUsername: @{user.username}",
        )

        await context.bot.send_message(
            chat_id=owner,
            text="⬇️ انتخاب کنید:",
            reply_to_message_id=fwd.message_id,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✉️ پاسخ", callback_data=f"reply_{uid}_{fwd.message_id}"),
                    InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{uid}")
                ]
            ])
        )

        save_message(uid, owner, "forward")
        finished_state.add(uid)
        await update.message.reply_text("✅ پیام ارسال شد", reply_markup=action_kb)
        return


# ---------- MAIN ----------
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
app.run_polling()
