from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import speech_recognition as sr
from pydub import AudioSegment
import os
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import gtts

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

# ================== منوی اصلی ==================
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎵 راهنما", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ================== دستور /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎙️ **ربات تبدیل صدا و متن**\n\n"
        "🔹 **متن به صدا:** یه متن فارسی بفرست.\n"
        "🔹 **صدا به متن:** یه فایل صوتی بفرست.\n\n"
        "⚠️ محدودیت‌ها:\n"
        "- متن: حداکثر ۳۰۰ کاراکتر\n"
        "- صدا: حداکثر ۲ مگابایت",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

# ================== راهنما ==================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "help":
        await query.edit_message_text(
            "📖 **راهنما:**\n\n"
            "✅ **متن به صدا:**\n"
            "یه متن فارسی (حداکثر ۳۰۰ کاراکتر) بفرست.\n"
            "بات فایل صوتی MP3 برمی‌گردونه.\n\n"
            "✅ **صدا به متن:**\n"
            "یه فایل صوتی (OGG یا MP3) بفرست.\n"
            "بات متن تشخیص داده شده رو برمی‌گردونه.\n\n"
            "⚠️ صدا باید واضح باشه و زبان فارسی داشته باشه.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 برگشت", callback_data="back")]
            ])
        )
    elif query.data == "back":
        await query.edit_message_text(
            "🎙️ **ربات تبدیل صدا و متن**\n\n"
            "🔹 **متن به صدا:** یه متن فارسی بفرست.\n"
            "🔹 **صدا به متن:** یه فایل صوتی بفرست.",
            reply_markup=get_main_keyboard()
        )

# ================== تبدیل متن به صدا (با چندین زبان) ==================
async def text_to_speech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text.startswith('/'):
        return

    if len(text) > 300:
        await update.message.reply_text("❌ متن خیلی طولانیه! حداکثر ۳۰۰ کاراکتر.")
        return

    await update.message.reply_text("🔄 در حال تبدیل متن به صدا...")

    languages = ['fa', 'com', 'ar', 'en']  # زبان‌های مختلف برای امتحان

    for lang in languages:
        try:
            tts = gtts.gTTS(text, lang=lang, slow=False)
            tts.save("voice.mp3")
            
            with open("voice.mp3", "rb") as audio:
                await update.message.reply_audio(audio, caption=f"🎵 فایل صوتی شما (زبان: {lang})!")
            
            os.remove("voice.mp3")
            return  # اگر موفق شد، از حلقه خارج میشه
        except:
            continue  # اگر خطا داد، زبان بعدی رو امتحان کن

    # اگه هیچ زبانی جواب نداد
    await update.message.reply_text("❌ خطا در تبدیل متن به صدا. لطفاً متن رو کوتاه‌تر یا ساده‌تر کن.")

# ================== تبدیل صدا به متن ==================
async def speech_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.voice and not update.message.audio:
        await update.message.reply_text("❌ لطفاً یه فایل صوتی بفرست.")
        return

    if update.message.voice:
        file = await context.bot.get_file(update.message.voice.file_id)
        file_size = update.message.voice.file_size
    else:
        file = await context.bot.get_file(update.message.audio.file_id)
        file_size = update.message.audio.file_size

    if file_size > 2 * 1024 * 1024:
        await update.message.reply_text("❌ حجم فایل زیاد است. حداکثر ۲ مگابایت.")
        return

    await update.message.reply_text("🔄 در حال تبدیل صدا به متن...")

    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
            ogg_path = tmp_ogg.name
            await file.download_to_drive(ogg_path)

        wav_path = ogg_path.replace(".ogg", ".wav")
        audio = AudioSegment.from_ogg(ogg_path)
        audio.export(wav_path, format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            try:
                text = recognizer.recognize_google(audio_data, language="fa-IR")
                await update.message.reply_text(f"📝 **متن تشخیص داده شده (فارسی):**\n\n{text}", parse_mode="Markdown")
            except sr.UnknownValueError:
                try:
                    text = recognizer.recognize_google(audio_data, language="en-US")
                    await update.message.reply_text(f"📝 **متن تشخیص داده شده (انگلیسی):**\n\n{text}", parse_mode="Markdown")
                except:
                    await update.message.reply_text("❌ متوجه صدا نشدم. لطفاً واضح‌تر صحبت کن.")
            except sr.RequestError:
                await update.message.reply_text("❌ خطا در ارتباط با سرور تشخیص صدا.")

        os.remove(ogg_path)
        os.remove(wav_path)

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در پردازش فایل:\n{e}")

# ================== اجرا ==================
def main():
    threading.Thread(target=start_web_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_to_speech))
    app.add_handler(MessageHandler(filters.VOICE, speech_to_text))
    app.add_handler(MessageHandler(filters.AUDIO, speech_to_text))

    print("✅ ربات تبدیل صدا و متن روشن شد!")
    app.run_polling()

if __name__ == "__main__":
    main()
