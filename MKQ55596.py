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
    last_msg_id INTEGER,
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


def save_message(sender, receiver, last_msg_id, msg_type):
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, last_msg_id, msg_type) VALUES (?, ?, ?, ?)",
        (sender, receiver, last_msg_id, msg_type)
    )
    db.commit()


# ---------- STATES ----------
user_links = {}
reply_state = {}      # owner_id -> target_user_id
blocked = {}
last_user_message = {}  # target_user_id -> last message_id
send_direct_state = set()


# ---------- MENUS ----------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 دریافت لینک اختصاصی", callback_data="get_link")],
        [InlineKeyboardButton("✉️ ارسال پیام مستقیم", callback_data="send_direct")]
    ])


def after_send_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ ارسال دوباره پیام به همین مخاطب", callback_data="send_again")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_menu")]
    ])


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    # 🔹 حالت لینک اختصاصی (اولویت اول)
    if context.args:
        try:
            owner_id = int(context.args[0])
        except:
            return

        if owner_id in blocked and user.id in blocked[owner_id]:
            return

        user_links[user.id] = owner_id
        send_direct_state.discard(user.id)

        await update.message.reply_text("پیامت رو بفرست ✉️")
        return

    # 🔹 پنل ادمین
    if user.id in ADMIN_IDS:
        await update.message.reply_text("🛠 پنل مدیریت", reply_markup=main_menu())
        return

    # 🔹 کاربر عادی
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
        await q.message.reply_text("منوی اصلی 👇", reply_markup=main_menu())

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

    # DIRECT SEND STEP
    if uid in send_direct_state and update.message.text.isdigit():
        reply_state[uid] = int(update.message.text)
        send_direct_state.remove(uid)
        await update.message.reply_text("پیامت رو بفرست:")
        return

    # OWNER REPLY
    if uid in reply_state:
        target = reply_state.pop(uid)

        reply_to = last_user_message.get(target)
        if reply_to:
            await context.bot.copy_message(
                chat_id=target,
                from_chat_id=uid,
                message_id=update.message.message_id,
                reply_to_message_id=reply_to
            )
        else:
            await context.bot.copy_message(
                chat_id=target,
                from_chat_id=uid,
                message_id=update.message.message_id
            )

        save_message(uid, target, reply_to, "reply")

        await update.message.reply_text(
            "✅ پاسخ ارسال شد",
            reply_markup=after_send_menu()
        )
        return

    # USER VIA LINK
    if uid in user_links:
        owner = user_links[uid]

        fwd = await context.bot.forward_message(
            chat_id=owner,
            from_chat_id=uid,
            message_id=update.message.message_id
        )

        last_user_message[uid] = update.message.message_id

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
                    InlineKeyboardButton("✉️ پاسخ", callback_data=f"reply_{uid}"),
                    InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{uid}")
                ]
            ])
        )

        save_message(uid, owner, update.message.message_id, "forward")

        await update.message.reply_text(
            "✅ پیام ارسال شد",
            reply_markup=after_send_menu()
        )
        return


# ---------- MAIN ----------
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
app.run_polling()
