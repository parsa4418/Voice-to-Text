from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gtts
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

TOKEN = "8607192869:AAFR5T11mG2_SUMOBP9U6bYaDogERzWdRDU"  # ← توکن خودت رو بذار

# ================== سرور کوچک برای Render ==================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_web_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ================== دستور /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 راهنما", callback_data="help")]
    ])
    await update.message.reply_text(
        "🎙️ **ربات تبدیل متن به صدا**\n\n"
        "🔹 یه متن فارسی بفرست.\n"
        "🔹 بات فایل صوتی MP3 برمی‌گردونه.\n\n"
        "⚠️ محدودیت‌ها:\n"
        "- حداکثر ۳۰۰ کاراکتر\n"
        "- زبان: فارسی (با لهجه‌ی رسمی)",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ================== راهنما ==================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "help":
        await query.edit_message_text(
            "📖 **راهنما:**\n\n"
            "✅ یه متن فارسی بفرست.\n"
            "✅ بات با استفاده از Google TTS فایل صوتی می‌سازه.\n"
            "✅ حداکثر ۳۰۰ کاراکتر.\n\n"
            "⚠️ متن باید به فارسی باشه و واضح نوشته بشه.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 برگشت", callback_data="back")]
            ])
        )
    elif query.data == "back":
        await query.edit_message_text(
            "🎙️ **ربات تبدیل متن به صدا**\n\n"
            "یه متن فارسی بفرست تا به صدا تبدیلش کنم.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 راهنما", callback_data="help")]
            ])
        )

# ================== تبدیل متن به صدا ==================
async def text_to_speech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # جلوگیری از تبدیل دستورات
    if text.startswith('/'):
        return

    if len(text) > 300:
        await update.message.reply_text("❌ متن خیلی طولانیه! حداکثر ۳۰۰ کاراکتر.")
        return

    await update.message.reply_text("🔄 در حال تبدیل متن به صدا...")

    try:
        # تبدیل متن به صدا با gTTS
        tts = gtts.gTTS(text, lang='fa', slow=False)
        tts.save("voice.mp3")

        # ارسال فایل صوتی
        with open("voice.mp3", "rb") as audio:
            await update.message.reply_audio(audio, caption="🎵 فایل صوتی شما!")

        os.remove("voice.mp3")

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در تبدیل متن به صدا:\n{e}")

# ================== اجرا ==================
def main():
    # اجرای سرور در ترد جداگانه
    threading.Thread(target=start_web_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_to_speech))

    print("✅ ربات تبدیل متن به صدا روشن شد!")
    app.run_polling()

if __name__ == "__main__":
    main()