# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║  ربات آزمون ترجمه شفاهی (NAATI Mock)  |  تک‌فایل ماژولار           ║
╚══════════════════════════════════════════════════════════════════╝

فهرست بخش‌ها (برای پیدا کردن سریع کد: Ctrl+F و جستجوی "[SECTION n]")

 [SECTION 1]  ایمپورت‌ها
 [SECTION 2]  تنظیمات و متغیرهای محیطی (توکن، رمز، کلید Gemini، مدل)
 [SECTION 3]  آبجکت‌های اصلی (bot, dp, gemini) + پرامپت پیش‌فرض
 [SECTION 4]  وب‌سرور Flask (فقط برای health check هاستینگ)
 [SECTION 5]  دیتابیس (ساخت جدول‌ها، تنظیمات: پرامپت و مدل)
 [SECTION 6]  State ها (مراحل گفتگو برای ادمین و کاربر)
 [SECTION 7]  ابزارهای کمکی تلگرام (کیبورد، ارسال پیام بلند، بررسی ادمین)
 [SECTION 8]  سرویس تصحیح هوش مصنوعی (Gemini)
 [SECTION 9]  بخش کاربر: /start، لیست آزمون‌ها، برگزاری آزمون
 [SECTION 10] مرکز کارنامه: دکمه‌های «تصحیح با هوش مصنوعی» و «بررسی دستی»، تایید / سفارشی / رد
 [SECTION 11] ادمین: ورود با رمز و پنل اصلی
 [SECTION 12] ادمین: ویرایش پرامپت و تغییر مدل Gemini
 [SECTION 13] ادمین: پیام همگانی
 [SECTION 14] ادمین: ساخت آزمون و سگمنت
 [SECTION 15] ادمین: مدیریت و حذف آزمون/سگمنت
 [SECTION 16] ادمین: مشاهده نتایج و پاکسازی دیتابیس
 [SECTION 17] نقطه شروع برنامه (main)

نکته: در aiogram ترتیب ثبت هندلرها مهم است؛ اولین هندلری که شرطش بخورد اجرا می‌شود.
"""

# ══════════════════════════════════════════════════════════════════
# [SECTION 1] ایمپورت‌ها
# ══════════════════════════════════════════════════════════════════
import os
import io
import asyncio
import logging
import sqlite3
import threading
from contextlib import closing

from flask import Flask
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════════════════════════
# [SECTION 2] تنظیمات و متغیرهای محیطی
#   همه‌ی موارد محرمانه از Environment Variable (پنل Render) خوانده می‌شوند.
# ══════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# مدل پیش‌فرض. از داخل ربات (پنل ادمین > تغییر مدل) هم قابل تغییر است.
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "180"))  # ثانیه برای هر درخواست
DB_PATH = os.environ.get("DB_PATH", "bot_database.db")

# افکت شمارش معکوس قبل از پخش هر سگمنت (برای خاموش کردن: COUNTDOWN_ENABLED=0)
COUNTDOWN_ENABLED = os.environ.get("COUNTDOWN_ENABLED", "1") == "1"

CHANNEL_USERNAME = "@Free_naati"
CHANNEL_LINK = "https://t.me/Free_naati"

_missing = [k for k, v in {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "GEMINI_API_KEY": GEMINI_API_KEY,
}.items() if not v]
if _missing:
    raise RuntimeError(f"Environment variable(s) not set: {', '.join(_missing)}")

# ══════════════════════════════════════════════════════════════════
# [SECTION 3] آبجکت‌های اصلی + پرامپت پیش‌فرض
# ══════════════════════════════════════════════════════════════════
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
gemini = genai.Client(api_key=GEMINI_API_KEY)

background_tasks = set()      # نگه‌داری رفرنس تسک‌های پس‌زمینه (تا garbage collect نشوند)
grading_in_progress = set()   # شناسه‌ی آزمون‌هایی که همین الان در حال تصحیح با هوش مصنوعی‌اند

# افکت کانفتی 🎉 تلگرام (message effect) برای پیام پایان آزمون. فقط در چت خصوصی کار می‌کند.
CONFETTI_EFFECT_ID = "5046509860389126442"

DEFAULT_PROMPT = """You are an expert NAATI CCL / interpreting examiner.
You will receive, for each segment: (1) the ORIGINAL audio the candidate had to interpret, and (2) the CANDIDATE's spoken interpretation.
Listen to the audio itself (not a transcript) and evaluate:
- Accuracy: completeness and correctness of meaning, numbers, names, omissions, additions.
- Language: grammar, vocabulary, register in the target language.
- Delivery: pronunciation, accent clarity, fluency, hesitations, fillers, pauses, tone, pace.
Rules: Base everything ONLY on what you actually hear. Never invent content. If audio is unclear, say so.
Write the whole report in Persian, as PLAIN TEXT only (no markdown, no asterisks).
Format:
نمرات: دقت X%، زبان X%، ارائه X%، کل X%
تحلیل هر سگمنت: (نکات مشخص، خطاها، نمونه‌ها)
نقاط قوت:
نقاط ضعف:
تمرین‌های پیشنهادی:
"""

# ══════════════════════════════════════════════════════════════════
# [SECTION 4] وب‌سرور Flask (health check برای Render)
# ══════════════════════════════════════════════════════════════════
app = Flask(__name__)


@app.route("/")
def health_check():
    return "Bot is running alive!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


# ══════════════════════════════════════════════════════════════════
# [SECTION 5] دیتابیس
#   جدول‌ها: admins, users, tests, segments, results, settings, pending_reports
# ══════════════════════════════════════════════════════════════════
def db():
    """اتصال به دیتابیس: with db() as conn: ..."""
    return closing(sqlite3.connect(DB_PATH))


def init_db():
    with db() as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, failed_attempts INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS tests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, test_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, file_id TEXT, segment_index INTEGER, segment_lang TEXT, segment_tip TEXT, FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE)")
        c.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, test_title TEXT, analysis TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS pending_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, test_title TEXT, report TEXT)")
        # آزمون‌های انجام‌شده توسط کاربران (تا ادمین هر وقت خواست تصحیح کند) + نسخه‌ی کپی اطلاعات سگمنت‌ها
        c.execute("CREATE TABLE IF NOT EXISTS submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, user_label TEXT, test_id INTEGER, test_title TEXT, test_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS submission_items (id INTEGER PRIMARY KEY AUTOINCREMENT, submission_id INTEGER, segment_index INTEGER, voice_file_id TEXT, seg_file_id TEXT, seg_lang TEXT, seg_mime TEXT, seg_key TEXT)")

        # مهاجرت: ستون نوع فایل صوتی سگمنت
        try:
            c.execute("ALTER TABLE segments ADD COLUMN file_mime TEXT")
        except sqlite3.OperationalError:
            pass

        # مهاجرت: ستون کلید تصحیح هر سگمنت
        try:
            c.execute("ALTER TABLE segments ADD COLUMN answer_key TEXT")
        except sqlite3.OperationalError:
            pass

        # مهاجرت: ستون is_admin (رفع باگ: کسی که یک بار رمز غلط می‌زد ادمین حساب می‌شد)
        try:
            c.execute("ALTER TABLE admins ADD COLUMN is_admin INTEGER DEFAULT 0")
            c.execute("UPDATE admins SET is_admin = 1 WHERE failed_attempts = 0 AND is_blocked = 0")
        except sqlite3.OperationalError:
            pass

        # حذف آزمون نمونه‌ی قدیمی که فایل صوتی واقعی ندارد
        c.execute("SELECT id FROM tests WHERE title = ?", ("آزمون تعیین سطح نمونه",))
        row = c.fetchone()
        if row:
            c.execute("SELECT COUNT(*) FROM segments WHERE test_id = ? AND file_id != 'sample_file_id'", (row[0],))
            if c.fetchone()[0] == 0:
                c.execute("DELETE FROM segments WHERE test_id = ?", (row[0],))
                c.execute("DELETE FROM tests WHERE id = ?", (row[0],))
        conn.commit()


init_db()


def get_setting(key, default=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def del_setting(key):
    with db() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


def get_prompt():
    return get_setting("grading_prompt", DEFAULT_PROMPT)


def get_model():
    return get_setting("gemini_model", DEFAULT_GEMINI_MODEL)


# ══════════════════════════════════════════════════════════════════
# [SECTION 6] State ها
# ══════════════════════════════════════════════════════════════════
class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_test_title = State()
    waiting_for_segment_lang = State()
    waiting_for_segment_audio = State()
    waiting_for_segment_tip = State()
    waiting_for_segment_key = State()
    waiting_for_broadcast_message = State()
    waiting_for_custom_report = State()
    waiting_for_prompt = State()
    waiting_for_model = State()


class UserTestStates(StatesGroup):
    taking_test = State()
    waiting_for_tip_ack = State()


# ══════════════════════════════════════════════════════════════════
# [SECTION 7] ابزارهای کمکی تلگرام
# ══════════════════════════════════════════════════════════════════
def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 شروع آزمون"), KeyboardButton(text="🔙 منوی قبل")]],
        resize_keyboard=True,
    )


async def download_bytes(file_id):
    """دانلود فایل تلگرام به صورت بایت (برای ارسال به Gemini)."""
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    return buf.getvalue()


async def send_long(chat_id, text, reply_markup=None):
    """ارسال متن بلند در چند پیام (حداکثر ۴۰۰۰ کاراکتر هر پیام)."""
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [" "]
    for i, chunk in enumerate(chunks):
        await bot.send_message(chat_id, chunk, reply_markup=reply_markup if i == len(chunks) - 1 else None)


async def play_countdown(message: Message):
    """افکت شمارش معکوس: یک پیام که با ویرایش، ۳ ← ۲ ← ۱ ← حرکت می‌شود و بعد پاک می‌شود."""
    if not COUNTDOWN_ENABLED:
        return
    frames = ["3️⃣ ...", "2️⃣ حاضری؟", "1️⃣ آماده ...", "🚀 حرکت!"]
    try:
        msg = await message.answer(frames[0])
        for frame in frames[1:]:
            await asyncio.sleep(1)
            await msg.edit_text(frame)
        await asyncio.sleep(0.7)
        await msg.delete()
    except Exception as err:
        logging.warning(f"countdown effect failed: {err}")


FINISH_TEXT = (
    "🎉 تبریک! آزمون شما به پایان رسید.\n\n"
    "✅ پاسخ‌های شما با موفقیت ثبت شد.\n"
    "🤖 جواب‌های شما برای تصحیح به سیستم هوش مصنوعی و استاد ارسال شد.\n"
    "⏳ بعد از بررسی و تایید، کارنامه‌تان تا چند ساعت آینده برای شما ارسال می‌شود.\n\n"
    "🙏 لطفاً تا آن زمان ربات را متوقف (Stop) نکنید."
)


async def send_finish_message(message: Message):
    """پیام پایان آزمون همراه با افکت کانفتی تلگرام؛ اگر افکت پشتیبانی نشد، پیام ساده می‌رود."""
    try:
        await message.answer(FINISH_TEXT, reply_markup=get_main_reply_keyboard(), message_effect_id=CONFETTI_EFFECT_ID)
    except Exception as err:
        logging.warning(f"message effect failed, sending plain message: {err}")
        await message.answer(FINISH_TEXT, reply_markup=get_main_reply_keyboard())


def user_label(user):
    """نام نمایشی کاربر: @username اگر داشت، وگرنه نام کامل."""
    if user.username:
        return f"@{user.username}"
    return user.full_name or f"id:{user.id}"


def is_admin_user(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ? AND is_admin = 1 AND is_blocked = 0", (user_id,)
        ).fetchone()
    return bool(row)


async def require_admin(callback: CallbackQuery):
    """برای دکمه‌های پنل ادمین: اگر کلیک‌کننده ادمین نبود False برمی‌گرداند."""
    if not is_admin_user(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return False
    return True


# ══════════════════════════════════════════════════════════════════
# [SECTION 8] سرویس تصحیح هوش مصنوعی (Gemini)
#   روش کار: برای هر سگمنت یک درخواست جدا (صدای اصلی + ویس کاربر، مستقیم به‌صورت
#   صوت، بدون تبدیل به متن) و در پایان یک درخواست جمع‌بندی برای نوشتن کارنامه نهایی
#   طبق پرامپت ادمین. تصحیح سگمنت‌ها جدا انجام می‌شود تا درخواست‌ها سبک بمانند.
# ══════════════════════════════════════════════════════════════════
async def gemini_call(contents, system_prompt=None, model=None, attempts=3):
    """یک درخواست به Gemini با timeout و تلاش مجدد برای خطاهای موقت (429/503)."""
    model = model or get_model()
    config = types.GenerateContentConfig(system_instruction=system_prompt) if system_prompt else None
    last_err = None
    for attempt in range(attempts):
        try:
            resp = await asyncio.wait_for(
                gemini.aio.models.generate_content(model=model, contents=contents, config=config),
                timeout=GEMINI_TIMEOUT,
            )
            return (resp.text or "").strip()
        except Exception as err:
            last_err = err
            msg = str(err)
            transient = any(k in msg for k in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")) or isinstance(err, asyncio.TimeoutError)
            if transient and attempt < attempts - 1:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            raise
    raise last_err


async def list_gemini_models():
    """لیست مدل‌های Gemini که generateContent را پشتیبانی می‌کنند."""
    names = []
    pager = await gemini.aio.models.list()
    async for m in pager:
        actions = getattr(m, "supported_actions", None) or []
        name = (m.name or "").replace("models/", "")
        if "generateContent" in actions and name.startswith("gemini") and "embedding" not in name:
            names.append(name)
    return names


def load_submission(sid):
    """خواندن آزمون ذخیره‌شده‌ی کاربر: (اطلاعات کلی، لیست سگمنت‌ها)"""
    with db() as conn:
        sub = conn.execute(
            "SELECT id, user_id, user_label, test_title, test_type FROM submissions WHERE id = ?", (sid,)
        ).fetchone()
        items = conn.execute(
            "SELECT segment_index, voice_file_id, seg_file_id, seg_lang, seg_mime, seg_key "
            "FROM submission_items WHERE submission_id = ? ORDER BY segment_index",
            (sid,),
        ).fetchall()
    return sub, items


async def grade_with_gemini(test_title, test_type, items):
    system_prompt = get_prompt()
    notes = []

    for it in items:
        idx, voice_id, seg_file_id, seg_lang, seg_mime, seg_key = it
        direction = "English to Persian" if seg_lang == "en" else "Persian to English"
        src_bytes = await download_bytes(seg_file_id)
        usr_bytes = await download_bytes(voice_id)

        parts = [
            types.Part.from_text(text=f"Test: {test_title} ({test_type}). Now evaluate ONLY segment {idx} ({direction})."),
            types.Part.from_text(text="ORIGINAL audio:"),
            types.Part.from_bytes(data=src_bytes, mime_type=seg_mime or "audio/ogg"),
            types.Part.from_text(text="CANDIDATE's spoken interpretation:"),
            types.Part.from_bytes(data=usr_bytes, mime_type="audio/ogg"),
        ]
        if seg_key:
            parts.append(types.Part.from_text(text=(
                "Reference correction key written by the human examiner for this segment "
                "(treat it as the ground truth for accuracy and key points):\n" + seg_key
            )))
        parts.append(types.Part.from_text(text=(
            "Write detailed examiner notes in Persian for this segment only: accuracy (omissions, additions, "
            "numbers, names), language quality, delivery (pronunciation, fluency, hesitations, fillers, pauses, tone), "
            "concrete examples of errors, and a score for this segment. Base everything only on what you hear."
        )))
        note = await gemini_call(parts, system_prompt=system_prompt)
        notes.append(f"=== Segment {idx} ({direction}) ===\n{note or '(no output)'}")

    final_contents = [
        f"Test: {test_title} ({test_type}).",
        "Below are your own detailed examiner notes for every segment (based on listening to the audio):",
        "\n\n".join(notes),
        "Now write the FINAL complete report for the candidate, following exactly the instructions and format in your system prompt.",
    ]
    return await gemini_call(final_contents, system_prompt=system_prompt)


async def run_ai_grading(sid, admin_chat_id):
    """
    با زدن دکمه‌ی «دریافت تصحیح از هوش مصنوعی» اجرا می‌شود:
    تصحیح با Gemini > ارسال کارنامه‌ی پیشنهادی (با دکمه‌های تایید/ویرایش/رد) > ارسال ویس‌های کاربر در پایین آن.
    """
    sub, items = load_submission(sid)
    if not sub:
        await bot.send_message(admin_chat_id, "⚠️ این آزمون در دیتابیس پیدا نشد (شاید دیتابیس پاک شده است).")
        return
    _, user_id, label, test_title, test_type = sub

    status = await bot.send_message(admin_chat_id, f"⏳ در حال تصحیح آزمون {label} با هوش مصنوعی...\n(بسته به تعداد سگمنت‌ها ممکن است چند دقیقه طول بکشد)")

    try:
        report = await grade_with_gemini(test_title, test_type, items)
        if not report:
            raise RuntimeError("Gemini returned an empty response")
    except Exception as err:
        logging.error(f"Gemini grading failed: {err}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 تلاش مجدد با هوش مصنوعی", callback_data=f"sub_ai_{sid}")],
            [InlineKeyboardButton(text="🎧 بررسی دستی", callback_data=f"sub_manual_{sid}")],
        ])
        await status.edit_text(
            f"⚠️ تصحیح خودکار ناموفق بود:\n{str(err)[:800]}\n\n"
            "اگر خطا درباره‌ی مدل است، از پنل ادمین > «تغییر مدل Gemini» مدل را عوض کنید.",
            reply_markup=kb,
        )
        return

    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_reports (user_id, username, test_title, report) VALUES (?, ?, ?, ?)",
            (user_id, label, test_title, report),
        )
        pid = cur.lastrowid
        conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تایید و ارسال به کاربر", callback_data=f"approve_{pid}")],
        [InlineKeyboardButton(text="✏️ ارسال متن سفارشی (ویرایش‌شده)", callback_data=f"custom_rep_{user_id}")],
        [InlineKeyboardButton(text="❌ رد کردن", callback_data=f"reject_{user_id}")],
    ])
    try:
        await status.delete()
    except Exception:
        pass
    await send_long(
        admin_chat_id,
        f"🤖 کارنامه پیشنهادی هوش مصنوعی\n👤 {label} (id: {user_id})\n📚 {test_title}\n\n{report}",
        reply_markup=kb,
    )

    # ویس‌های کاربر زیر کارنامه
    for it in items:
        try:
            await bot.send_voice(admin_chat_id, voice=it[1], caption=f"🎙 ویس سگمنت {it[0]} - {label}")
            await asyncio.sleep(0.3)
        except Exception as err:
            logging.error(f"send voice to admin {admin_chat_id}: {err}")


# ══════════════════════════════════════════════════════════════════
# [SECTION 9] بخش کاربر: /start، لیست آزمون‌ها، برگزاری آزمون
# ══════════════════════════════════════════════════════════════════
@dp.message(F.text.in_(["/start", "🚀 شروع آزمون", "🔙 منوی قبل"]))
async def cmd_start_or_menu(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or ""

    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        admin_row = conn.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        conn.commit()
        tests = conn.execute("SELECT id, title, test_type FROM tests").fetchall()

    if admin_row and admin_row[0] == 1:
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return

    if not tests:
        await message.answer("سلام! در حال حاضر هیچ آزمونی فعال نشده است.", reply_markup=get_main_reply_keyboard())
        return

    buttons = []
    for t_id, t_title, t_type in tests:
        type_label = "🎯 تعیین سطح" if t_type == "placement" else "📝 ماک کامل"
        buttons.append([InlineKeyboardButton(text=f"{type_label}: {t_title}", callback_data=f"start_test_{t_id}")])

    await message.answer(
        "سلام! به بخش آزمون‌های ترجمه شفاهی خوش آمدید.\n\nلطفاً یکی از آزمون‌های زیر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
   

@dp.callback_query(F.data.startswith("start_test_"))
async def user_start_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])

    with db() as conn:
        test_row = conn.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,)).fetchone()
        segments = conn.execute(
            "SELECT id, file_id, segment_lang, segment_tip, file_mime, answer_key FROM segments WHERE test_id = ? ORDER BY segment_index ASC",
            (test_id,),
        ).fetchall()

    if not test_row:
        await callback.message.answer("⚠️ این آزمون دیگر وجود ندارد.")
        await callback.answer()
        return

    if not segments:
        await callback.message.answer("⚠️ این آزمون هنوز سگمنتی ندارد.")
        await callback.answer()
        return

    test_title, test_type = test_row

    await state.set_state(UserTestStates.taking_test)
    await state.update_data(
        test_id=test_id,
        test_title=test_title,
        test_type=test_type,
        segments=segments,
        current_index=0,
        user_voices=[],
    )

    await show_segment_tip(callback.message, state)
    await callback.answer()


async def show_segment_tip(message: Message, state: FSMContext):
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    seg = segments[curr_idx]

    tip_text = seg[3] if seg[3] else "• حتماً کاغذ و قلم همراه داشته باشید تا نکات و اعداد کلیدی را یادداشت کنید."

    await state.set_state(UserTestStates.waiting_for_tip_ack)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، متوجه شدم و آماده‌ام", callback_data="ack_tip_and_play")]
    ])

    msg = await message.answer(f"💡 راهنمای سگمنت {curr_idx + 1} از {len(segments)}:\n\n{tip_text}", reply_markup=keyboard)
    await state.update_data(last_tip_msg_id=msg.message_id)


@dp.callback_query(UserTestStates.waiting_for_tip_ack, F.data == "ack_tip_and_play")
async def ack_tip_and_play(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserTestStates.taking_test)
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    seg = segments[curr_idx]

    tip_msg_id = data.get("last_tip_msg_id")
    if tip_msg_id:
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=tip_msg_id)
        except Exception:
            pass

    await callback.answer()  # زودتر جواب بده تا دکمه در حال بارگذاری نماند
    await play_countdown(callback.message)  # افکت ۳-۲-۱-حرکت

    lang_dir = "انگلیسی به فارسی 🇬🇧 ➡️ 🇮🇷" if seg[2] == "en" else "فارسی به انگلیسی 🇮🇷 ➡️ 🇬🇧"

    info_msg = await callback.message.answer(
        f"🎧 سگمنت {curr_idx + 1} ({lang_dir})\n\nبه فایل صوتی زیر گوش دهید و ترجمه خود را به صورت ویس ارسال کنید:"
    )

    try:
        audio_msg = await callback.message.answer_voice(voice=seg[1])
    except Exception:
        try:
            audio_msg = await callback.message.answer_audio(audio=seg[1])
        except Exception:
            audio_msg = await callback.message.answer_document(document=seg[1])

    await state.update_data(last_info_msg_id=info_msg.message_id, last_audio_msg_id=audio_msg.message_id)


@dp.message(UserTestStates.taking_test, F.voice)
async def handle_user_voice_test(message: Message, state: FSMContext):
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    user_voices = data.get("user_voices", [])

    for m_id in [data.get("last_info_msg_id"), data.get("last_audio_msg_id"), message.message_id]:
        if m_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=m_id)
            except Exception:
                pass

    user_voices.append({"segment_index": curr_idx + 1, "voice_file_id": message.voice.file_id})

    await message.answer(f"✅ جواب سگمنت {curr_idx + 1} با موفقیت ارسال شد.")

    next_idx = curr_idx + 1
    if next_idx < len(segments):
        await state.update_data(current_index=next_idx, user_voices=user_voices)
        await show_segment_tip(message, state)
        return

    # ---- آخرین سگمنت: پایان آزمون ----
    test_id = data.get("test_id")
    test_title = data.get("test_title")
    test_type = data.get("test_type")
    user_id = message.from_user.id
    label = user_label(message.from_user)

    # ذخیره‌ی آزمون کاربر در دیتابیس (تصحیح بعداً با دکمه‌ی ادمین انجام می‌شود)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO submissions (user_id, user_label, test_id, test_title, test_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, label, test_id, test_title, test_type),
        )
        sid = cur.lastrowid
        for i, seg in enumerate(segments):
            cur.execute(
                "INSERT INTO submission_items (submission_id, segment_index, voice_file_id, seg_file_id, seg_lang, seg_mime, seg_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, user_voices[i]["segment_index"], user_voices[i]["voice_file_id"],
                 seg[1], seg[2], seg[4] if len(seg) > 4 else None, seg[5] if len(seg) > 5 else None),
            )
        conn.commit()
        admin_ids = [r[0] for r in conn.execute("SELECT user_id FROM admins WHERE is_admin = 1 AND is_blocked = 0").fetchall()]

    # 🎉 پیام پایان آزمون با افکت کانفتی
    await send_finish_message(message)

    # اعلان به ادمین‌ها با دو دکمه
    admin_text = (
        "📥 آزمون جدیدی انجام شده است\n"
        "━━━━━━━━━━━━━━━━\n"
        f"📚 آزمون: {test_title}\n"
        f"👤 کاربر: {label}\n"
        f"🆔 شناسه: {user_id}\n"
        f"🎙 تعداد پاسخ‌ها: {len(segments)}\n"
        "━━━━━━━━━━━━━━━━\n"
        "یکی از روش‌های بررسی را انتخاب کنید 👇"
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 دریافت تصحیح از هوش مصنوعی", callback_data=f"sub_ai_{sid}")],
        [InlineKeyboardButton(text="🎧 بررسی دستی", callback_data=f"sub_manual_{sid}")],
    ])
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_kb)
        except Exception as err:
            logging.error(f"Failed to notify admin {admin_id}: {err}")

    await state.clear()


@dp.message(UserTestStates.taking_test)
async def remind_send_voice(message: Message):
    await message.answer("🎙 لطفاً پاسخ خود را به صورت ویس (voice) ارسال کنید.")


# ══════════════════════════════════════════════════════════════════
# [SECTION 10] کارنامه: تایید / ارسال سفارشی / رد
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("sub_ai_"))
async def admin_sub_ai(callback: CallbackQuery):
    """دکمه‌ی «دریافت تصحیح از هوش مصنوعی»"""
    if not await require_admin(callback):
        return
    sid = int(callback.data.split("_")[2])
    if sid in grading_in_progress:
        await callback.answer("⏳ تصحیح این آزمون همین الان در حال انجام است...", show_alert=True)
        return
    await callback.answer("درخواست برای هوش مصنوعی ارسال شد ⏳")
    grading_in_progress.add(sid)

    async def _job():
        try:
            await run_ai_grading(sid, callback.from_user.id)
        except Exception as err:
            logging.error(f"run_ai_grading crashed: {err}")
            try:
                await bot.send_message(callback.from_user.id, f"❌ خطای غیرمنتظره: {str(err)[:500]}")
            except Exception:
                pass
        finally:
            grading_in_progress.discard(sid)

    task = asyncio.create_task(_job())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@dp.callback_query(F.data.startswith("sub_manual_"))
async def admin_sub_manual(callback: CallbackQuery):
    """دکمه‌ی «بررسی دستی»: ویس‌های کاربر یکی‌یکی، و بعد از هر ویس «کلید تصحیح» همان سگمنت."""
    if not await require_admin(callback):
        return
    sid = int(callback.data.split("_")[2])
    sub, items = load_submission(sid)
    if not sub:
        await callback.answer("این آزمون در دیتابیس پیدا نشد.", show_alert=True)
        return
    await callback.answer()
    _, user_id, label, test_title, test_type = sub
    chat_id = callback.from_user.id

    await bot.send_message(chat_id, f"🎧 بررسی دستی\n📚 {test_title}\n👤 {label}\n\nبعد از هر ویس، کلید تصحیح همان سگمنت می‌آید 👇")
    for it in items:
        idx, voice_id, _sf, _lang, _mime, key = it
        try:
            await bot.send_voice(chat_id, voice=voice_id, caption=f"🎙 ویس سگمنت {idx} - {label}")
        except Exception as err:
            await bot.send_message(chat_id, f"⚠️ ارسال ویس سگمنت {idx} ناموفق بود: {err}")
        if key:
            await send_long(chat_id, f"🔑 کلید تصحیح سگمنت {idx}:\n{key}")
        else:
            await bot.send_message(chat_id, f"🔑 برای سگمنت {idx} کلید تصحیحی ثبت نشده است.")
        await asyncio.sleep(0.3)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ ارسال کارنامه برای کاربر", callback_data=f"custom_rep_{user_id}")
    ]])
    await bot.send_message(chat_id, "✅ همه‌ی ویس‌ها ارسال شد. بعد از بررسی، کارنامه را با دکمه‌ی زیر برای کاربر بفرستید:", reply_markup=kb)


@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve_report(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    pid = int(callback.data.split("_")[1])
    with db() as conn:
        row = conn.execute("SELECT user_id, username, test_title, report FROM pending_reports WHERE id=?", (pid,)).fetchone()
        if not row:
            await callback.answer("این کارنامه پیدا نشد (قبلاً ارسال شده؟)", show_alert=True)
            return
        user_id, username, test_title, report = row
        conn.execute("INSERT INTO results (user_id, username, test_title, analysis) VALUES (?, ?, ?, ?)", (user_id, username, test_title, report))
        conn.execute("DELETE FROM pending_reports WHERE id=?", (pid,))
        conn.commit()

    try:
        await send_long(
            user_id,
            f"🎉 کارنامه و نتیجه آزمون شما:\n\n{report}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)]]),
        )
        await callback.message.answer("✅ کارنامه برای کاربر ارسال شد.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    except Exception as e:
        await callback.message.answer(f"❌ خطا در ارسال به کاربر: {e}")
    await callback.answer()


@dp.callback_query(F.data.startswith("custom_rep_"))
async def admin_start_custom_report(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    target_user_id = int(callback.data.split("_")[2])
    await state.set_state(AdminStates.waiting_for_custom_report)
    await state.update_data(target_user_id=target_user_id)
    await callback.message.answer("✏️ لطفاً متن کامل کارنامه، نمرات و تمرین‌ها را به صورت یک پیام متنی بفرستید تا ربات آن را برای کاربر ارسال کند:")
    await callback.answer()


@dp.message(AdminStates.waiting_for_custom_report, F.text)
async def process_admin_custom_report(message: Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    custom_text = message.text
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)]])

    try:
        await send_long(target_user_id, f"🎉 کارنامه و نتیجه آزمون شما تایید و ارسال شد:\n\n{custom_text}", reply_markup=keyboard)
        await message.answer("✅ کارنامه سفارشی شما با موفقیت برای کاربر ارسال شد!", reply_markup=get_main_reply_keyboard())
    except Exception as e:
        await message.answer(f"❌ خطا در ارسال به کاربر: {e}")


@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject_report(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    try:
        target_user_id = int(callback.data.split("_")[1])
    except Exception:
        target_user_id = 0

    try:
        if target_user_id:
            await bot.send_message(
                chat_id=target_user_id,
                text="❌ متأسفانه کارنامه آزمون شما نیاز به ویرایش یا بازبینی مجدد دارد.",
                reply_markup=get_main_reply_keyboard(),
            )
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer("❌ وضعیت: رد شد")
    except Exception as e:
        await callback.answer(f"خطا: {e}", show_alert=True)
        return
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# [SECTION 11] ادمین: ورود با رمز و پنل اصلی
# ══════════════════════════════════════════════════════════════════
async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ساخت آزمون جدید", callback_data="admin_create_manual")],
        [InlineKeyboardButton(text="✏️ مدیریت و حذف آزمون‌ها/سگمنت‌ها", callback_data="admin_manage_tests")],
        [InlineKeyboardButton(text="🧠 ویرایش پرامپت تصحیح", callback_data="admin_edit_prompt")],
        [InlineKeyboardButton(text="🤖 تغییر مدل Gemini", callback_data="admin_edit_model")],
        [InlineKeyboardButton(text="📢 ارسال نوتیفیکیشن", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 مشاهده نتایج", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="🗑 پاکسازی کل دیتابیس", callback_data="admin_clear_db")],
    ])
    await message.answer("پنل مدیریت پیشرفته:", reply_markup=keyboard)


@dp.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    user_id = message.from_user.id
    with db() as conn:
        row = conn.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,)).fetchone()

    if row and row[0] == 1:
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return

    if is_admin_user(user_id):
        await show_admin_panel(message)
    else:
        await message.answer("رمز عبور پنل ادمین را وارد کنید:")
        await state.set_state(AdminStates.waiting_for_password)


@dp.message(AdminStates.waiting_for_password)
async def process_admin_password(message: Message, state: FSMContext):
    user_id = message.from_user.id
    with db() as conn:
        row = conn.execute("SELECT failed_attempts FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        failed = row[0] if row else 0

        if message.text == ADMIN_PASSWORD:
            conn.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked, is_admin) VALUES (?, 0, 0, 1)", (user_id,))
            conn.commit()
            ok = True
        else:
            failed += 1
            blocked = 1 if failed >= 3 else 0
            conn.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked, is_admin) VALUES (?, ?, ?, 0)", (user_id, failed, blocked))
            conn.commit()
            ok = False

    if ok:
        await state.clear()
        try:
            await message.delete()  # پاک کردن پیام حاوی رمز
        except Exception:
            pass
        await message.answer("ورود با موفقیت انجام شد!")
        await show_admin_panel(message)
    elif failed >= 3:
        await state.clear()
        await message.answer("❌ رمز عبور ۳ بار به اشتباه وارد شد.")
    else:
        await message.answer("رمز عبور اشتباه است.")


@dp.callback_query(F.data == "admin_back_to_main")
async def admin_back_to_main(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    await show_admin_panel(callback.message)
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# [SECTION 12] ادمین: ویرایش پرامپت و تغییر مدل Gemini
# ══════════════════════════════════════════════════════════════════
# ---- 12-الف: ویرایش پرامپت تصحیح ----
@dp.callback_query(F.data == "admin_edit_prompt")
async def admin_edit_prompt(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    await state.set_state(AdminStates.waiting_for_prompt)
    current = get_prompt()
    preview = current[:600] + ("..." if len(current) > 600 else "")
    await callback.message.answer(
        f"🧠 پرامپت فعلی ({len(current)} کاراکتر):\n\n{preview}\n\n"
        "──────────\n"
        "پرامپت جدید را در «یک پیام» بفرستید.\n"
        "• برای برگشت به پیش‌فرض بنویسید: default\n"
        "• برای لغو بنویسید: cancel"
    )
    await callback.answer()


@dp.message(AdminStates.waiting_for_prompt, F.text)
async def save_prompt(message: Message, state: FSMContext):
    new_prompt = message.text.strip()

    if new_prompt.lower() == "cancel":
        await state.clear()
        await message.answer("لغو شد. پرامپت قبلی بدون تغییر ماند.")
        return

    if new_prompt.lower() == "default":
        del_setting("grading_prompt")
        await state.clear()
        await message.answer("✅ پرامپت به حالت پیش‌فرض برگشت.")
        return

    if len(new_prompt) < 30:
        await message.answer("⚠️ پرامپت خیلی کوتاه است (کمتر از ۳۰ کاراکتر). دوباره بفرستید، یا برای لغو بنویسید: cancel")
        return

    status = await message.answer("⏳ در حال ذخیره و تست اتصال به Gemini...")

    set_setting("grading_prompt", new_prompt)
    await state.clear()
    saved_ok = (get_prompt() == new_prompt)  # بررسی اینکه واقعاً ذخیره شده

    try:
        await gemini_call("Connection test: reply with the single word OK.", system_prompt=new_prompt, attempts=1)
        test_line = f"✅ تست Gemini موفق بود (کلید API و مدل «{get_model()}» درست کار می‌کنند)."
    except Exception as e:
        test_line = (
            f"⚠️ پرامپت ذخیره شد ولی تست Gemini خطا داد:\n{str(e)[:600]}\n\n"
            "اگر خطا درباره‌ی مدل است، از پنل ادمین > «تغییر مدل Gemini» مدل را عوض کنید."
        )

    warn = ""
    if len(new_prompt) >= 3900:
        warn = "\n⚠️ پرامپت شما خیلی بلند است. تلگرام پیام‌های بالای ۴۰۹۶ کاراکتر را تکه‌تکه می‌کند و ممکن است فقط بخش اول ذخیره شده باشد."

    preview = new_prompt[:300] + ("..." if len(new_prompt) > 300 else "")
    await status.edit_text(
        f"{'✅ پرامپت ذخیره شد' if saved_ok else '❌ ذخیره ناموفق بود'} ({len(new_prompt)} کاراکتر)\n\n"
        f"{test_line}{warn}\n\nشروع پرامپت:\n{preview}"
    )


@dp.message(AdminStates.waiting_for_prompt)
async def save_prompt_invalid(message: Message):
    await message.answer("⚠️ فقط متن بفرستید (نه فایل/عکس/ویس). برای لغو بنویسید: cancel")


# ---- 12-ب: تغییر مدل Gemini ----
@dp.callback_query(F.data == "admin_edit_model")
async def admin_edit_model(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    await callback.answer()
    status = await callback.message.answer("⏳ در حال گرفتن لیست مدل‌های در دسترس...")
    try:
        models = await list_gemini_models()
        models = sorted(set(models))
        listing = "\n".join(f"• {m}" for m in models[:25]) if models else "(لیستی دریافت نشد)"
    except Exception as e:
        listing = f"(خطا در گرفتن لیست: {str(e)[:200]})"

    await state.set_state(AdminStates.waiting_for_model)
    await status.edit_text(
        f"🤖 مدل فعلی: {get_model()}\n\n"
        f"مدل‌های در دسترس کلید شما:\n{listing}\n\n"
        "──────────\n"
        "اسم مدل جدید را بفرستید (مثلاً gemini-3.8-flash).\n"
        "مدل قبل از ذخیره تست می‌شود و اگر کار نکند ذخیره نمی‌شود.\n"
        "• برگشت به پیش‌فرض: default\n"
        "• لغو: cancel"
    )


@dp.message(AdminStates.waiting_for_model, F.text)
async def save_model(message: Message, state: FSMContext):
    name = message.text.strip().replace("models/", "")

    if name.lower() == "cancel":
        await state.clear()
        await message.answer("لغو شد. مدل قبلی بدون تغییر ماند.")
        return

    if name.lower() == "default":
        del_setting("gemini_model")
        await state.clear()
        await message.answer(f"✅ مدل به پیش‌فرض برگشت: {DEFAULT_GEMINI_MODEL}")
        return

    if " " in name or len(name) > 80:
        await message.answer("⚠️ اسم مدل معتبر نیست. فقط اسم مدل را بفرستید، یا برای لغو بنویسید: cancel")
        return

    status = await message.answer(f"⏳ در حال تست مدل {name} ...")
    try:
        await gemini_call("Connection test: reply with the single word OK.", model=name, attempts=1)
    except Exception as e:
        await status.edit_text(f"❌ مدل {name} کار نکرد و ذخیره نشد:\n{str(e)[:600]}\n\nاسم دیگری بفرستید یا بنویسید: cancel")
        return

    set_setting("gemini_model", name)
    await state.clear()
    await status.edit_text(f"✅ مدل تست شد و ذخیره شد: {name}\nاز این به بعد تصحیح‌ها با این مدل انجام می‌شود.")


# ══════════════════════════════════════════════════════════════════
# [SECTION 13] ادمین: پیام همگانی
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.message.answer("📢 متن پیام همگانی را بفرستید:")
    await callback.answer()


@dp.message(AdminStates.waiting_for_broadcast_message, F.text)
async def process_broadcast_message(message: Message, state: FSMContext):
    broadcast_text = message.text
    await state.clear()
    status_msg = await message.answer("⏳ در حال ارسال...")
    with db() as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()

    success_count = 0
    for user in users:
        try:
            await bot.send_message(chat_id=user[0], text=f"🔔 اطلاعیه جدید:\n\n{broadcast_text}")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await status_msg.edit_text(f"✅ ارسال به {success_count} کاربر انجام شد.")
    await show_admin_panel(message)


# ══════════════════════════════════════════════════════════════════
# [SECTION 14] ادمین: ساخت آزمون و سگمنت
#   مسیر: نوع آزمون > عنوان > (زبان > صوت > راهنما) × تعداد سگمنت > پایان
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "admin_create_manual")
async def admin_create_manual(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="تعیین سطح", callback_data="type_placement"),
         InlineKeyboardButton(text="ماک کامل", callback_data="type_mock")]
    ])
    await callback.message.answer("نوع آزمون را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("type_"))
async def process_test_type(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    t_type = callback.data.split("_")[1]
    await state.update_data(test_type=t_type)
    await callback.message.answer("عنوان آزمون را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_test_title)
    await callback.answer()


@dp.message(AdminStates.waiting_for_test_title, F.text)
async def process_test_title(message: Message, state: FSMContext):
    title = message.text
    data = await state.get_data()
    t_type = data.get("test_type")

    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO tests (title, test_type) VALUES (?, ?)", (title, t_type))
            conn.commit()
            test_id = cur.lastrowid
    except sqlite3.IntegrityError:
        await message.answer("این عنوان تکراری است. لطفاً عنوان دیگری بفرستید:")
        return

    await state.update_data(test_id=test_id, segment_index=1)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="انگلیسی 🇬🇧", callback_data="lang_en"),
         InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await message.answer(f"آزمون «{title}» ثبت شد.\n\nزبان سگمنت شماره ۱:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("lang_"))
async def process_segment_lang(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    lang = callback.data.split("_")[1]
    await state.update_data(segment_lang=lang)
    await callback.message.answer("فایل صوتی سگمنت را بفرستید:")
    await state.set_state(AdminStates.waiting_for_segment_audio)
    await callback.answer()


@dp.message(AdminStates.waiting_for_segment_audio, F.voice | F.audio | F.document)
async def process_segment_audio(message: Message, state: FSMContext):
    if message.voice:
        file_id, mime = message.voice.file_id, "audio/ogg"
    elif message.audio:
        file_id, mime = message.audio.file_id, message.audio.mime_type or "audio/mpeg"
    else:
        file_id, mime = message.document.file_id, message.document.mime_type or "audio/mpeg"
    await state.update_data(file_id=file_id, file_mime=mime)
    await message.answer("متن راهنما (Tip) این سگمنت را بفرستید (توصیه‌ها یا نکات برای کاربر قبل از پخش صوت):")
    await state.set_state(AdminStates.waiting_for_segment_tip)


@dp.message(AdminStates.waiting_for_segment_tip, F.text)
async def process_segment_tip(message: Message, state: FSMContext):
    await state.update_data(segment_tip=message.text)
    await message.answer(
        "🔑 حالا «کلید تصحیح» این سگمنت را بفرستید.\n\n"
        "مثلاً ترجمه‌ی درست، اعداد و نکات کلیدی که باید در جواب کاربر باشد. "
        "این متن فقط برای شما نمایش داده می‌شود (در بررسی دستی، زیر ویس کاربر) و به هوش مصنوعی هم به‌عنوان مرجع داده می‌شود.\n\n"
        "اگر کلیدی ندارید بنویسید: -"
    )
    await state.set_state(AdminStates.waiting_for_segment_key)


@dp.message(AdminStates.waiting_for_segment_key, F.text)
async def process_segment_key(message: Message, state: FSMContext):
    data = await state.get_data()
    test_id = data.get("test_id")
    file_id = data.get("file_id")
    file_mime = data.get("file_mime")
    segment_tip = data.get("segment_tip")
    seg_index = data.get("segment_index")
    segment_lang = data.get("segment_lang")
    answer_key = None if message.text.strip() == "-" else message.text

    with db() as conn:
        conn.execute(
            "INSERT INTO segments (test_id, file_id, segment_index, segment_lang, segment_tip, file_mime, answer_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (test_id, file_id, seg_index, segment_lang, segment_tip, file_mime, answer_key),
        )
        conn.commit()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="افزودن سگمنت بعدی", callback_data="admin_add_more_segment")],
        [InlineKeyboardButton(text="✅ پایان بارگذاری و ذخیره آزمون", callback_data="admin_finish_test")],
    ])
    await message.answer(f"سگمنت {seg_index} با موفقیت ثبت شد.", reply_markup=keyboard)


@dp.callback_query(F.data == "admin_add_more_segment")
async def admin_add_more_segment(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    data = await state.get_data()
    new_index = (data.get("segment_index") or 0) + 1
    await state.update_data(segment_index=new_index)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="انگلیسی 🇬🇧", callback_data="lang_en"),
         InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await callback.message.answer(f"زبان سگمنت {new_index}:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "admin_finish_test")
async def admin_finish_test(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    await state.clear()
    await callback.message.edit_text("✅ آزمون با موفقیت ذخیره شد!")
    await show_admin_panel(callback.message)
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# [SECTION 15] ادمین: مدیریت و حذف آزمون/سگمنت
# ══════════════════════════════════════════════════════════════════
async def render_manage_tests(callback: CallbackQuery):
    with db() as conn:
        tests = conn.execute("SELECT id, title, test_type FROM tests").fetchall()

    if not tests:
        await callback.message.edit_text("هیچ آزمونی وجود ندارد.")
        await show_admin_panel(callback.message)
        return

    buttons = []
    for t in tests:
        buttons.append([
            InlineKeyboardButton(text=f"📂 {t[1]}", callback_data=f"manage_test_{t[0]}"),
            InlineKeyboardButton(text="❌ حذف آزمون", callback_data=f"del_test_{t[0]}"),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin_back_to_main")])
    await callback.message.edit_text("مدیریت و حذف آزمون‌ها:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def render_manage_single_test(callback: CallbackQuery, test_id: int):
    with db() as conn:
        test_row = conn.execute("SELECT title FROM tests WHERE id = ?", (test_id,)).fetchone()
        segments = conn.execute(
            "SELECT id, segment_index, segment_lang FROM segments WHERE test_id = ? ORDER BY segment_index", (test_id,)
        ).fetchall()

    if not test_row:
        await render_manage_tests(callback)
        return

    buttons = []
    for s in segments:
        buttons.append([
            InlineKeyboardButton(text=f"سگمنت {s[1]} ({s[2]})", callback_data=f"noop_{s[0]}"),
            InlineKeyboardButton(text="❌ حذف سگمنت", callback_data=f"del_seg_{s[0]}_{test_id}"),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_manage_tests")])
    await callback.message.edit_text(f"مدیریت سگمنت‌های آزمون «{test_row[0]}»:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data == "admin_manage_tests")
async def admin_manage_tests(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    await render_manage_tests(callback)
    await callback.answer()


@dp.callback_query(F.data.startswith("del_test_"))
async def admin_delete_test(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    test_id = int(callback.data.split("_")[2])
    with db() as conn:
        conn.execute("DELETE FROM segments WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        conn.commit()
    await callback.answer("آزمون و سگمنت‌های آن حذف شدند!", show_alert=True)
    await render_manage_tests(callback)


@dp.callback_query(F.data.startswith("manage_test_"))
async def manage_single_test(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    test_id = int(callback.data.split("_")[2])
    await render_manage_single_test(callback, test_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("noop_"))
async def noop(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("del_seg_"))
async def admin_delete_segment(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    parts = callback.data.split("_")
    seg_id = int(parts[2])
    test_id = int(parts[3])

    with db() as conn:
        conn.execute("DELETE FROM segments WHERE id = ?", (seg_id,))
        conn.commit()
    await callback.answer("سگمنت حذف شد!", show_alert=True)
    await render_manage_single_test(callback, test_id)


# ══════════════════════════════════════════════════════════════════
# [SECTION 16] ادمین: مشاهده نتایج و پاکسازی دیتابیس
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "admin_view_results")
async def admin_view_results(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    with db() as conn:
        rows = conn.execute("SELECT username, test_title, date FROM results ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        await callback.message.answer("نتیجه‌ای ثبت نشده است.")
    else:
        text = "۱۰ نتیجه اخیر:\n\n" + "\n".join([f"کاربر: {r[0]} | آزمون: {r[1]} | {r[2]}" for r in rows])
        await callback.message.answer(text)
    await callback.answer()


@dp.callback_query(F.data == "admin_clear_db")
async def admin_clear_db(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ بله، همه چیز پاک شود", callback_data="admin_clear_db_confirm")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data="admin_back_to_main")],
    ])
    await callback.message.answer("آیا مطمئنی؟ همه آزمون‌ها، سگمنت‌ها، نتایج و لیست کاربران پاک می‌شود.", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "admin_clear_db_confirm")
async def admin_clear_db_confirm(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    with db() as conn:
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM pending_reports")
        conn.execute("DELETE FROM submission_items")
        conn.execute("DELETE FROM submissions")
        conn.execute("DELETE FROM segments")
        conn.execute("DELETE FROM tests")
        conn.execute("DELETE FROM users")
        conn.commit()
    await callback.message.answer("پاکسازی کل اطلاعات انجام شد.")
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# [SECTION 17] نقطه شروع برنامه
# ══════════════════════════════════════════════════════════════════
async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info("Flask web server started.")
    logging.info(f"Gemini model in use: {get_model()}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
