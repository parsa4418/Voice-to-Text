import asyncio
import os
import re
import sqlite3
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import edge_tts
from faster_whisper import WhisperModel
from pydub import AudioSegment
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ⚠️ توکن رو از Environment Variable بخون، نه اینکه توی کد بذاری
# چون این توکن قبلاً یه بار توی چت افشا شده، حتماً از BotFather ریجنریتش کن
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده! توی Environment Variables اضافه‌ش کن.")

BOT_USERNAME = os.environ.get("BOT_USERNAME", "your_bot_username")  # بدون @

# آیدی عددی خودت رو اینجا بذار (با @userinfobot توی تلگرام می‌تونی بگیریش)
# می‌تونی چندتا آیدی هم با کاما جدا کنی: "111111,222222"
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

START_COINS = 2
COINS_PER_REFERRAL = 1
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 مگابایت

# مدل Whisper محلی: tiny سبک‌ترینه (مناسب پلن رایگان رندر)، base دقیق‌تره ولی سنگین‌تر
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "tiny")
_whisper_model = None


def get_whisper_model() -> WhisperModel:
    """مدل رو فقط بار اول لود می‌کنه (لود کردنش کند و سنگینه)."""
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model

ASK_TARGET_ID, ASK_AMOUNT = range(2)

DB_PATH = "bot_database.db"

# ================== دیتابیس ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            referred_by INTEGER,
            referrals INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, coins, referred_by, referrals FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def create_user(user_id: int, referrer_id: int = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, coins, referred_by, referrals) VALUES (?, ?, ?, 0)",
        (user_id, START_COINS, referrer_id),
    )
    conn.commit()
    conn.close()


def add_coins(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def increment_referrals(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def use_coin(user_id: int) -> bool:
    """اگه کاربر سکه داشته باشه یکی کم می‌کنه و True برمی‌گردونه، وگرنه False."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row or row[0] <= 0:
        conn.close()
        return False
    cur.execute("UPDATE users SET coins = coins - 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True


def get_or_register_user(user_id: int, referrer_id: int = None):
    """کاربر رو برمی‌گردونه، اگه وجود نداشت می‌سازتش و به معرف سکه می‌ده."""
    row = get_user(user_id)
    is_new = row is None
    if is_new:
        valid_referrer = None
        if referrer_id and referrer_id != user_id and get_user(referrer_id):
            valid_referrer = referrer_id
        create_user(user_id, valid_referrer)
        if valid_referrer:
            add_coins(valid_referrer, COINS_PER_REFERRAL)
            increment_referrals(valid_referrer)
        row = get_user(user_id)
    return row, is_new


# ================== سرور برای Render ==================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# ================== کیبوردها ==================
def get_main_keyboard(user_id: int = None):
    keyboard = [
        [InlineKeyboardButton("💰 موجودی سکه", callback_data="balance")],
        [InlineKeyboardButton("➕ افزایش سکه", callback_data="get_coins")],
        [InlineKeyboardButton("🎙️ راهنما", callback_data="help")],
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("🎁 دادن سکه به کاربر (ادمین)", callback_data="admin_gift")])
    return InlineKeyboardMarkup(keyboard)


def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]])


def get_no_coins_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ افزایش سکه", callback_data="get_coins")]])


# ================== تشخیص زبان صدا برای TTS ==================
def detect_language_and_voice(text: str) -> str:
    persian_pattern = re.compile(r"[\u0600-\u06FF]")
    if persian_pattern.search(text):
        return "fa-IR-DilaraNeural"
    return "en-US-AnaNeural"


# ================== متن خوش‌آمد ==================
def welcome_text(coins: int) -> str:
    return (
        "🎙️ **ربات تبدیل صدا، فیلم و آهنگ به متن**\n\n"
        "🔹 **صدا به متن:** ویس، فایل صوتی، آهنگ یا فیلم بفرست تا متنش رو بگیری\n"
        "🔹 **متن به صدا:** یه متن فارسی یا انگلیسی بفرست\n\n"
        "✅ تشخیص خودکار زبان (فارسی/انگلیسی)\n"
        f"💰 موجودی فعلی شما: **{coins} سکه**\n\n"
        "هر تبدیل ۱ سکه هزینه داره."
    )


# ================== دستور /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referrer_id = None
    if context.args:
        try:
            referrer_id = int(context.args[0])
        except ValueError:
            referrer_id = None

    row, is_new = get_or_register_user(user_id, referrer_id)
    coins = row[1]

    if is_new:
        await update.message.reply_text(
            f"🎉 خوش اومدی! {START_COINS} سکه هدیه برات فعال شد.\n\n" + welcome_text(coins),
            reply_markup=get_main_keyboard(user_id),
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            welcome_text(coins), reply_markup=get_main_keyboard(user_id), parse_mode="Markdown"
        )


# ================== دکمه‌ها ==================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "help":
        await query.edit_message_text(
            "📖 **راهنما:**\n\n"
            "✅ **صدا/فیلم/آهنگ به متن:**\n"
            "هر فایل صوتی، ویس، آهنگ یا فیلمی بفرست، بات متنش رو استخراج می‌کنه.\n"
            "هر تبدیل ۱ سکه هزینه داره.\n\n"
            "✅ **متن به صدا:**\n"
            "متن فارسی یا انگلیسی بفرست، بات با صدای طبیعی فایل صوتی برمی‌گردونه.\n\n"
            f"⚠️ حداکثر حجم فایل: {MAX_FILE_SIZE // (1024*1024)} مگابایت",
            reply_markup=get_back_keyboard(),
        )

    elif query.data == "balance":
        row = get_user(user_id)
        coins = row[1] if row else 0
        referrals = row[3] if row else 0
        await query.edit_message_text(
            f"💰 موجودی شما: **{coins} سکه**\n👥 تعداد زیرمجموعه‌ها: **{referrals} نفر**",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown",
        )

    elif query.data == "get_coins":
        link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
        await query.edit_message_text(
            "➕ **افزایش سکه**\n\n"
            "با دعوت از دوستات سکه رایگان بگیر!\n"
            f"🎁 با هر زیرمجموعه (کسی که با لینک تو بات رو استارت کنه) **{COINS_PER_REFERRAL} سکه** میگیری.\n\n"
            "🔗 لینک اختصاصی تو:\n"
            f"`{link}`\n\n"
            "این لینک رو برای دوستات بفرست تا هر دو نفرتون سکه هدیه بگیرید 🎉",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown",
        )

    elif query.data == "back":
        row = get_user(user_id)
        coins = row[1] if row else 0
        await query.edit_message_text(
            welcome_text(coins), reply_markup=get_main_keyboard(user_id), parse_mode="Markdown"
        )


# ================== پنل ادمین: دادن سکه به کاربر ==================
async def admin_gift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        await query.answer("⛔️ فقط ادمین دسترسی داره.", show_alert=True)
        return ConversationHandler.END

    await query.edit_message_text(
        "🎁 **دادن سکه به کاربر**\n\n"
        "آیدی عددی کاربر رو بفرست (مثلاً: 123456789)\n"
        "برای لغو /cancel رو بفرست."
    )
    return ASK_TARGET_ID


async def admin_gift_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ آیدی باید فقط عدد باشه. دوباره بفرست یا /cancel بزن.")
        return ASK_TARGET_ID

    context.user_data["gift_target_id"] = int(text)
    await update.message.reply_text("چند تا سکه می‌خوای بهش بدی؟ (فقط عدد بفرست)")
    return ASK_AMOUNT


async def admin_gift_receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.lstrip("-").isdigit() or int(text) == 0:
        await update.message.reply_text("❌ یه عدد معتبر (غیر صفر) بفرست یا /cancel بزن.")
        return ASK_AMOUNT

    amount = int(text)
    target_id = context.user_data.get("gift_target_id")

    row = get_user(target_id)
    if not row:
        # کاربر هنوز بات رو استارت نکرده؛ باهاش یه رکورد می‌سازیم
        create_user(target_id, referrer_id=None)

    add_coins(target_id, amount)
    new_row = get_user(target_id)
    await update.message.reply_text(
        f"✅ انجام شد! {amount} سکه به کاربر `{target_id}` اضافه شد.\n"
        f"موجودی فعلی‌ش: {new_row[1]} سکه",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎁 مدیر بات {amount} سکه بهت هدیه داد! موجودی فعلیت: {new_row[1]} سکه",
        )
    except Exception:
        pass  # کاربر شاید بات رو بلاک کرده باشه

    context.user_data.pop("gift_target_id", None)
    return ConversationHandler.END


async def admin_gift_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gift_target_id", None)
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END


# ================== تبدیل متن به صدا (edge-tts) ==================
async def text_to_speech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("/"):
        return

    if len(text) > 300:
        await update.message.reply_text("❌ متن خیلی طولانیه! حداکثر ۳۰۰ کاراکتر.")
        return

    user_id = update.effective_user.id
    get_or_register_user(user_id)

    if not use_coin(user_id):
        await update.message.reply_text(
            "❌ سکه‌هات تموم شده! برای گرفتن سکه رایگان دوستاتو دعوت کن:",
            reply_markup=get_no_coins_keyboard(),
        )
        return

    voice = detect_language_and_voice(text)
    lang_label = "فارسی" if "fa-IR" in voice else "انگلیسی"
    await update.message.reply_text(f"🔄 در حال تبدیل متن به صدا ({lang_label})...")

    output_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            output_file = tmp_mp3.name

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_file)

        with open(output_file, "rb") as audio:
            await update.message.reply_audio(audio, caption=f"🎵 فایل صوتی ({lang_label})")

    except Exception as e:
        add_coins(user_id, 1)  # برگردوندن سکه در صورت خطا
        await update.message.reply_text(f"❌ خطا در تبدیل متن به صدا:\n{e}")
    finally:
        if output_file and os.path.exists(output_file):
            os.remove(output_file)


# ================== تشخیص متن از فایل صوتی/تصویری ==================
def transcribe_file(input_path: str) -> str:
    """
    هر فرمت صوتی یا تصویری رو می‌گیره، صداش رو استخراج می‌کنه (اگه فیلم باشه)
    و با مدل Whisper محلی متنش رو برمی‌گردونه. اگه چیزی تشخیص نده رشته خالی برمی‌گردونه.
    این تابع خودش CPU-bound و کندشونده، پس همیشه باید توی thread جدا صدا زده بشه.
    """
    wav_path = input_path + "_converted.wav"
    try:
        # pydub با کمک ffmpeg تقریباً همه‌ی فرمت‌های صوتی و تصویری رو می‌خونه
        # و در صورت فیلم بودن، خودکار فقط ترک صدا رو استخراج می‌کنه
        audio = AudioSegment.from_file(input_path)
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(wav_path, format="wav")

        model = get_whisper_model()
        # Whisper خودش زبان رو تشخیص می‌ده و طول فایل رو خودش مدیریت می‌کنه (نیازی به تکه‌تکه کردن دستی نیست)
        segments, _info = model.transcribe(wav_path, beam_size=5, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


async def speech_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    # پیدا کردن فایل، فارغ از اینکه ویس، آهنگ، فیلم یا داکیومنته
    tg_file_obj = None
    file_size = 0
    suffix = ".dat"

    if message.voice:
        tg_file_obj = message.voice
        suffix = ".ogg"
    elif message.audio:
        tg_file_obj = message.audio
        suffix = ".mp3"
    elif message.video:
        tg_file_obj = message.video
        suffix = ".mp4"
    elif message.video_note:
        tg_file_obj = message.video_note
        suffix = ".mp4"
    elif message.document and message.document.mime_type and (
        message.document.mime_type.startswith("audio/") or message.document.mime_type.startswith("video/")
    ):
        tg_file_obj = message.document
        suffix = os.path.splitext(message.document.file_name or "")[1] or ".dat"
    else:
        await message.reply_text("❌ لطفاً یه فایل صوتی، آهنگ، ویس یا فیلم بفرست.")
        return

    file_size = tg_file_obj.file_size or 0
    if file_size > MAX_FILE_SIZE:
        await message.reply_text(f"❌ حجم فایل زیاده. حداکثر {MAX_FILE_SIZE // (1024*1024)} مگابایت.")
        return

    user_id = update.effective_user.id
    get_or_register_user(user_id)

    if not use_coin(user_id):
        await message.reply_text(
            "❌ سکه‌هات تموم شده! برای گرفتن سکه رایگان دوستاتو دعوت کن:",
            reply_markup=get_no_coins_keyboard(),
        )
        return

    await message.reply_text("🔄 در حال تبدیل به متن... (فایل‌های حجیم کمی زمان می‌بره)")

    input_path = None
    try:
        tg_file = await context.bot.get_file(tg_file_obj.file_id)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            input_path = tmp_file.name
        await tg_file.download_to_drive(input_path)

        text = await asyncio.to_thread(transcribe_file, input_path)  # رشته خالی یعنی چیزی تشخیص داده نشد

        if text:
            await message.reply_text(f"📝 **متن تشخیص داده شده:**\n\n{text}", parse_mode="Markdown")
        else:
            add_coins(user_id, 1)  # برگردوندن سکه چون چیزی تشخیص داده نشد
            await message.reply_text("❌ متوجه صدا نشدم. لطفاً یه فایل واضح‌تر بفرست (سکه‌ت برگشت).")

    except Exception as e:
        add_coins(user_id, 1)  # برگردوندن سکه در صورت خطای فنی
        await message.reply_text(f"❌ خطا در پردازش فایل (سکه‌ت برگشت):\n{e}")
    finally:
        if input_path and os.path.exists(input_path):
            os.remove(input_path)


# ================== اجرا ==================
def main():
    init_db()
    threading.Thread(target=start_web_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    admin_gift_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_gift_start, pattern="^admin_gift$")],
        states={
            ASK_TARGET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_gift_receive_id)],
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_gift_receive_amount)],
        },
        fallbacks=[CommandHandler("cancel", admin_gift_cancel)],
    )
    app.add_handler(admin_gift_conv)

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_to_speech))
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE | filters.Document.ALL,
            speech_to_text,
        )
    )

    print("✅ ربات تبدیل صدا/فیلم/آهنگ به متن + سیستم سکه روشن شد!")
    app.run_polling()


if __name__ == "__main__":
    main()
