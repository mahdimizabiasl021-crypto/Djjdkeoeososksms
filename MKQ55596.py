import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q"
ADMIN_ID = 7986263531

# ---------- DATABASE ----------
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


# ---------- STATES ----------
user_links = {}
reply_state = {}
blocked = {}
send_direct_state = {}


# ---------- MENUS ----------
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


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if context.args:
        owner_id = int(context.args[0])

        if owner_id in blocked and user.id in blocked[owner_id]:
            return

        user_links[user.id] = owner_id
        await update.message.reply_text("پیامت رو بنویس ✉️")
        return

    await update.message.reply_text(
        f"سلام {user.first_name} 👋",
        reply_markup=main_menu()
    )


# ---------- BUTTONS ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if q.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={uid}"
        await q.message.reply_text(f"📎 لینک اختصاصی:\n{link}")

    elif q.data == "send_direct":
        send_direct_state[uid] = "id"
        await q.message.reply_text("آیدی عددی مخاطب رو بفرست:")

    elif q.data == "main_menu":
        await q.message.reply_text("منوی اصلی", reply_markup=main_menu())

    elif q.data.startswith("reply_"):
        _, target_id, msg_id = q.data.split("_")
        reply_state[uid] = (int(target_id), int(msg_id))
        await q.message.reply_text("پاسخت رو بنویس:")

    elif q.data.startswith("block_"):
        target = int(q.data.split("_")[1])
        blocked.setdefault(uid, set()).add(target)
        await q.message.reply_text("🚫 بلاک شد")


# ---------- TEXT ----------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # --- ADMIN VIEW ---
    if uid == ADMIN_ID and (text.isdigit()):
        cur.execute(
            "SELECT from_id, to_id, text, time FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (int(text),)
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

    # --- DIRECT SEND ---
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

    # --- REPLY ---
    if uid in reply_state:
        target, msg_id = reply_state.pop(uid)
        await context.bot.send_message(chat_id=target, text=text, reply_to_message_id=msg_id)
        save_log(target, uid, target, text)
        await update.message.reply_text("✅ پاسخ ارسال شد", reply_markup=after_send_menu())
        return

    # --- VIA LINK ---
    if uid in user_links:
        owner = user_links
