from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8325553827:AAEhSzQRUHrixbFy4EY1qK0E73pIdgp6b3Q"

user_links = {}      # second_user_id -> owner_id
reply_state = {}     # owner_id -> target_user_id
blocked = {}         # owner_id -> set(user_ids)
send_direct_state = set()  # users waiting to send direct message


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if context.args:
        owner_id = int(context.args[0])

        if owner_id in blocked and user.id in blocked[owner_id]:
            return

        user_links[user.id] = owner_id
        await update.message.reply_text(
            "پیامت رو بنویس، مستقیم ارسال میشه ✉️"
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 دریافت لینک اختصاصی", callback_data="get_link")],
        [InlineKeyboardButton("✉️ ارسال پیام به مخاطب خاص", callback_data="send_direct")]
    ])

    await update.message.reply_text(
        f"سلام {user.first_name} 👋\n"
        "می‌خوای چیکار کنی؟",
        reply_markup=keyboard
    )


# ---------- BUTTONS ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "get_link":
        link = f"https://t.me/{context.bot.username}?start={user_id}"
        await query.message.reply_text(f"📎 لینک اختصاصی شما:\n{link}")

    elif query.data == "send_direct":
        send_direct_state.add(user_id)
        await query.message.reply_text("آیدی عددی مخاطب رو بفرست:")

    elif query.data.startswith("reply_"):
        target_id = int(query.data.split("_")[1])
        reply_state[user_id] = target_id
        await query.message.reply_text("پیامت رو بنویس:")

    elif query.data.startswith("block_"):
        target_id = int(query.data.split("_")[1])
        blocked.setdefault(user_id, set()).add(target_id)
        await query.message.reply_text("🚫 کاربر بلاک شد")


# ---------- TEXT HANDLER ----------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # direct send step 1 (get target id)
    if user_id in send_direct_state and text.isdigit():
        reply_state[user_id] = int(text)
        send_direct_state.remove(user_id)
        await update.message.reply_text("حالا پیامت رو بنویس:")
        return

    # owner replying (COPY, not forward)
    if user_id in reply_state:
        target_id = reply_state.pop(user_id)
        await context.bot.send_message(chat_id=target_id, text=text)
        return

    # second user sending message via link (FORWARD)
    if user_id in user_links:
        owner_id = user_links[user_id]

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
        return


# ---------- MAIN ----------
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

app.run_polling()
