from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import speech_recognition as sr
from pydub import AudioSegment
import os
import re
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
import edge_tts

TOKEN = "8607192869:AAFR5T11mG2_SUMOBP9U6bYaDogERzWdRDU"  # ← توکن خودت رو بذار

if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده! توی تنظیمات Render اضافه‌ش کن.")

# ================== سرور برای Render ==================
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
    keyboard = [[InlineKeyboardButton("🎵 راهنما", callback_data="help")]]
    return InlineKeyboardMarkup(keyboard)

# ================== تشخیص زبان و انتخاب صدا ==================
def detect_language_and_voice(text: str) -> str:
    """
    اگر متن حاوی حروف فارسی/عربی باشه صدای فارسی انتخاب می‌شه،
    در غیر این صورت صدای انگلیسی.
    """
    persian_pattern = re.compile(r'[\u0600-\u06FF]')
    if persian_pattern.search(text):
        # نزدیک‌ترین صدای فارسیِ جوون و طبیعی
        return "fa-IR-DilaraNeural"
    else:
        # نزدیک‌ترین صدای انگلیسیِ جوون/شاداب (نزدیک‌ترین گزینه به "بچگونه")
        return "en-US-AnaNeural"

# ================== دستور /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎙️ **ربات تبدیل صدا و متن**\n\n"
        "🔹 **متن به صدا:** یه متن فارسی یا انگلیسی بفرست.\n"
        "🔹 **صدا به متن:** یه فایل صوتی بفرست.\n\n"
        "✅ تشخیص خودکار زبان (فارسی/انگلیسی)\n"
        "⚠️ متن: حداکثر ۳۰۰ کاراکتر\n"
        "⚠️ صدا: حداکثر ۲ مگابایت",
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
            "متن فارسی یا انگلیسی بفرست، بات زبان رو خودش تشخیص می‌ده\n"
            "و با صدای طبیعی فایل MP3 برمی‌گردونه.\n\n"
            "✅ **صدا به متن:**\n"
            "یه فایل صوتی (OGG/MP3) بفرست.\n"
            "بات متن تشخیص داده شده رو برمی‌گردونه.\n\n"
            "⚠️ صدا باید واضح باشه و نویز نداشته باشه.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 برگشت", callback_data="back")]
            ])
        )
    elif query.data == "back":
        await query.edit_message_text(
            "🎙️ **ربات تبدیل صدا و متن**\n\n"
            "🔹 **متن به صدا:** یه متن فارسی یا انگلیسی بفرست.\n"
            "🔹 **صدا به متن:** یه فایل صوتی بفرست.",
            reply_markup=get_main_keyboard()
        )

# ================== تبدیل متن به صدا (با edge-tts) ==================
async def text_to_speech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text.startswith('/'):
        return

    if len(text) > 300:
        await update.message.reply_text("❌ متن خیلی طولانیه! حداکثر ۳۰۰ کاراکتر.")
        return

    voice = detect_language_and_voice(text)
    lang_label = "فارسی" if "fa-IR" in voice else "انگلیسی"

    await update.message.reply_text(f"🔄 در حال تبدیل متن به صدا ({lang_label})...")

    output_file = None
    try:
        # فایل موقت مخصوص هر درخواست، تا با چند کاربر همزمان تداخل نداشته باشه
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            output_file = tmp_mp3.name

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_file)

        with open(output_file, "rb") as audio:
            await update.message.reply_audio(audio, caption=f"🎵 فایل صوتی ({lang_label})")

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در تبدیل متن به صدا:\n{e}")
    finally:
        if output_file and os.path.exists(output_file):
            os.remove(output_file)

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

    ogg_path = None
    wav_path = None
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

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در پردازش فایل:\n{e}")
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)

# ================== اجرا ==================
def main():
    threading.Thread(target=start_web_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_to_speech))
    app.add_handler(MessageHandler(filters.VOICE, speech_to_text))
    app.add_handler(MessageHandler(filters.AUDIO, speech_to_text))

    print("✅ ربات تبدیل صدا و متن (فارسی/انگلیسی) روشن شد!")
    app.run_polling()

if __name__ == "__main__":
    main()
