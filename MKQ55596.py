from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

TOKEN = "8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q"

links = {}       # user_id -> owner_id
blocked = {}     # owner_id -> set(user_ids)
reply_state = {} # owner_id -> target_user_id

# ---------- start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.args:
        owner_id = int(context.args[0])

        if owner_id in blocked and user_id in blocked[owner_id]:
            return

        links[user_id] = owner_id
        await update.message.reply_text("پیامت رو بفرست")
    else:
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        await update.message.reply_text(
            f"لینک اختصاصی شما:\n{link}"
        )

# ---------- receive from second person ----------
async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in links:
        return

    owner_id = links[user_id]

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✉️ پاسخ", callback_data=f"reply_{user_id}"),
            InlineKeyboardButton("🚫 بلاک", callback_data=f"block_{user_id}")
        ]
    ])

    await context.bot.forward_message(
        chat_id=owner_id,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id
    )

    await context.bot.send_message(
        chat_id=owner_id,
        text=f"👤 فرستنده:\n"
             f"ID: {user_id}\n"
             f"Username: @{update.effective_user.username}",
        reply_markup=keyboard
    )

# ---------- buttons ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    owner_id = query.from_user.id

    if data.startswith("reply_"):
        target_id = int(data.split("_")[1])
        reply_state[owner_id] = target_id
        await query.message.reply_text("پیامت رو بفرست")

    elif data.startswith("block_"):
        target_id = int(data.split("_")[1])
        blocked.setdefault(owner_id, set()).add(target_id)
        await query.message.reply_text("کاربر بلاک شد")

# ---------- owner reply ----------
async def owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id = update.effective_user.id

    if owner_id not in reply_state:
        return

    target_id = reply_state.pop(owner_id)

    await context.bot.send_message(
        chat_id=target_id,
        text=update.message.text
    )

# ---------- main ----------
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, owner_reply))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message))

app.run_polling()
