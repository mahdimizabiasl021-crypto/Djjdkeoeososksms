import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ================= CONFIG =================
TOKEN = "8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q"
ADMIN_ID = 7986263531

# ================= DATABASE =================
conn = sqlite3.connect("logs.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    from_id INTEGER,
    to_id INTEGER,
    text TEXT,
    time TEXT
)
""")
conn.commit()


def save_log(user_id, from_id, to_id, text):
    cur.execute(
        "INSERT INTO messages (user_id, from_id, to_id, text, time) VALUES (?, ?, ?, ?, ?)",
        (user_id, from_id, to_id, text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()

# ================= STATES =================
user_links = {}          # second_user_id -> owner_id
reply_state = {}         # owner_id -> (target_user_id, target_message_id)
blocked = {}             # owner_id -> set(user_ids)
send_direct_state = {}   # user_id -> step ("id" or ("text", target_id))

# ================= MENUS =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 دریافت لینک اختصاصی", callback_data="get_link")],
        [InlineKeyboardButton("✉️ ارسال پیام به مخاطب خاص", callback_data="send_direct")]
    ])


def after_send_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 ارسال دوباره پیام", callback_data="send_direct")],
        [InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="main_menu")]
    ])


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"سلام {user.first_name} 👋\nچی دوست داری انجام بدی؟",
        reply_markup=main_menu()
    )


# ================= BUTTONS =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await query.message.reply_text(f"📎 لینک اختصاصی شما:\n{link}")

    elif query.data == "send_direct":
        send_direct_state[uid] = "id"
        await query.message.reply_text("آیدی عددی مخاطب رو بفرست:")

    elif query.data == "main_menu":
        await query.message.reply_text("بازگشت به منوی اصلی 🏠", reply_markup=main_menu())

    elif query.data.startswith("reply_"):
        _, target_id, msg_id = query.data.split("_")
        reply_state[uid] = (int(target_id), int(msg_id))
        await query.message.reply_text("پاسخت رو بنویس:")

    elif query.data.startswith("block_"):
        target = int(query.data.split("_")[1])
        blocked.setdefault(uid, set()).add(target)
        await query.message.reply_text("🚫 کاربر بلاک شد")


# ================= TEXT HANDLER =================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # ---------- ADMIN VIEW ----------
    if uid == ADMIN_ID and text.isdigit():
        target_uid = int(text)
        cur.execute(
            "SELECT from_id, to_id, text, time FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (target_uid,)
        )
        rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("لاگی پیدا نشد")
            return
        msg = "📜 آخرین پیام‌ها:\n\n"
        for r in rows[::-1]:
            msg += f"{r[3]} | {r[0]} → {r[1]}:\n{r[2]}\n\n"
        await update.message.reply_text(msg)
        return

    # ---------- DIRECT SEND ----------
    if uid in send_direct_state:
        step = send_direct_state[uid]

        if step == "id" and text.isdigit():
            send_direct_state[uid] = ("text", int(text))
            await update.message.reply_text("حالا پیامت رو بنویس:")
            return

        if isinstance(step, tuple):
            _, target = step
            await context.bot.send_message(chat_id=target, text=text)
            save_log(uid, uid, target, text)
            send_direct_state.pop(uid)
            await update.message.reply_text("✅ پیام ارسال شد", reply_markup=after_send_menu())
            return

    # ---------- REPLY ----------
    if uid in reply_state:
        target_id, msg_id = reply_state.pop(uid)
        await context.bot.send_message(chat_id=target_id, text=text, reply_to_message_id=msg_id)
        save_log(target_id, uid, target_id, text)
        await update.message.reply_text("✅ پاسخ ارسال شد", reply_markup=after_send_menu())
        return

    # ---------- VIA LINK ----------
    if uid in user_links:
        owner = user_links[uid]
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✉️ پاسخ", callback_data=f"reply_{uid}_{update.message.message_id}"),
                InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{uid}")
            ]
        ])
        await context.bot.forward_message(
            chat_id=owner,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
        await context.bot.send_message(
            chat_id=owner,
            text=f"👤 فرستنده: {uid}",
            reply_markup=kb
        )
        save_log(uid, uid, owner, text)


# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
app.run_polling()
