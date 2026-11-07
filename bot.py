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
import json
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
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

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

# سقف نمره‌ی Accuracy برای هر نوع آزمون (نمره‌ی کل را استاد دستی می‌دهد).
# سقف هر سگمنت = سقف کل ÷ تعداد سگمنت‌های آزمون.
ACCURACY_MAX = {"mock": 45, "placement": 100}
# محدوده‌ی کسر نمره‌ی هر خطا و مقیاس نمره‌های زبان/ارائه/تلفظ داخل «پرامپت تصحیح» تعریف می‌شود
# (از پنل ادمین قابل تغییر است)؛ کد فقط جلوی نمره‌های غیرمنطقی را می‌گیرد.
TEST_TYPE_FA = {"placement": "🎯 تعیین سطح", "mock": "📝 ماک کامل"}

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

DEFAULT_PROMPT = """Role: You are an expert, strict and professional NAATI CCL examiner. You evaluate a bilingual dialogue interpreting test (English-Persian / Persian-English) against official NAATI-style criteria.

1. ACCURACY (compare what the candidate said with the examiner's original segment and key):
- Each segment starts with full marks. Points are deducted per error. A segment never goes below 0.
- Deduction scale per error: from 0.5 up to 5 points, in steps of 0.5. The maximum deduction of 5 is reserved for a major sentence of the segment that is completely omitted or completely distorted.
- Error types:
  * Omission: deleting important information, numbers, dates, names or key parts of the message.
  * Insertion: adding information, words or personal comments that the speaker did not say.
  * Distortion: changing the meaning, tense, logic, conditions or specific terms (for example "according to regulations").
  * Lexical/Grammar: inappropriate collocations or structural shifts that affect meaning.
- Mandatory self-correction: before you finish, review your own list once more and remove any deduction that is not justified or whose weight does not match the severity of the error.

2. QUALITY SCORES (judge from the AUDIO itself, each on a strict scale from 1 to 10):
- Quality of Language: register, natural collocations, avoiding word-for-word translation, correct functional grammar.
- Quality of Delivery: hesitations, disruptive pauses and fillers, excessive self-correction, intonation matching the speaker's tone, professional confidence.
- Pronunciation: clarity and intelligibility (a native accent is not required, clarity is).

Rules: base everything ONLY on what you actually hear. Never invent content. If the audio is unclear, say so.
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

        # نتیجه‌ی تصحیح هوش مصنوعی برای هر سگمنت (تا اگر وسط کار خطا شد، از همان‌جا ادامه بدهیم)
        c.execute("CREATE TABLE IF NOT EXISTS ai_segment_results (submission_id INTEGER, segment_index INTEGER, result_json TEXT, PRIMARY KEY (submission_id, segment_index))")

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

        # مهاجرت: متن اصلی هر سگمنت (answer_key = ترجمه‌ی درست) و کپی آن در آزمون‌های ارسالی
        try:
            c.execute("ALTER TABLE segments ADD COLUMN source_text TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE submission_items ADD COLUMN seg_source TEXT")
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


def prompt_key(test_type):
    return f"grading_prompt_{test_type}" if test_type in TEST_TYPE_FA else "grading_prompt"


def get_prompt(test_type=None):
    """پرامپت تصحیح: اول مخصوص همان نوع آزمون، بعد پرامپت مشترک قدیمی، بعد پیش‌فرض."""
    return get_setting(prompt_key(test_type)) or get_setting("grading_prompt") or DEFAULT_PROMPT


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
    waiting_for_segment_source = State()
    waiting_for_segment_key = State()
    waiting_for_broadcast_message = State()
    waiting_for_custom_report = State()
    waiting_for_prompt = State()
    waiting_for_total = State()


class UserTestStates(StatesGroup):
    taking_test = State()
    waiting_for_tip_ack = State()


# ══════════════════════════════════════════════════════════════════
# [SECTION 7] ابزارهای کمکی تلگرام
# ══════════════════════════════════════════════════════════════════
# ---------- سیستم ناوبری (منوی قبل / منوی اصلی) ----------
# برای هر چت یک «پشته‌ی منو» نگه می‌داریم: [منوی اصلی, پنل مدیریت, مدیریت آزمون‌ها, ...]
# بالاترین عنصر = جایی که کاربر الان هست؛ عنصر زیرش = «منوی قبل».
# هم دکمه‌های شیشه‌ای زیر پیام‌ها و هم کیبورد ثابت پایین چت از همین پشته ساخته می‌شوند.
BTN_START = "🚀 شروع آزمون"
BTN_HOME = "🏠 منوی اصلی"
BTN_EXAM_HOME = "🏠 منوی اصلی (انصراف از آزمون)"
BTN_BACK = "🔙 "          # متن دکمه‌ی بازگشت = "🔙 " + اسم منوی قبل

nav_stacks = {}           # chat_id -> [(key, label), ...]
last_kb_sig = {}          # chat_id -> آخرین کیبورد پایین چت که برایش فرستاده‌ایم


def nav_reset(chat_id):
    nav_stacks[chat_id] = [("main", "منوی اصلی")]


def nav_stack(chat_id):
    if chat_id not in nav_stacks:
        nav_reset(chat_id)
    return nav_stacks[chat_id]


def nav_enter(chat_id, key, label):
    """ورود به یک منو. اگر قبلاً در پشته بود، پشته تا همان‌جا برمی‌گردد (حلقه نمی‌سازد)."""
    st = nav_stack(chat_id)
    for i, (k, _) in enumerate(st):
        if k == key:
            del st[i + 1:]
            return
    st.append((key, label))


def nav_back(chat_id):
    st = nav_stack(chat_id)
    if len(st) > 1:
        st.pop()
    return st[-1][0]


def nav_row(chat_id):
    """ردیف دکمه‌های شیشه‌ای «بازگشت به منوی قبل» + «منوی اصلی». در منوی اصلی چیزی نمی‌دهد."""
    st = nav_stack(chat_id)
    if len(st) <= 1:
        return []
    home = InlineKeyboardButton(text=BTN_HOME, callback_data="nav_home")
    if len(st) == 2:  # منوی قبل همان منوی اصلی است، دو دکمه‌ی تکراری نمی‌گذاریم
        return [[home]]
    return [[InlineKeyboardButton(text=f"{BTN_BACK}{st[-2][1]}", callback_data="nav_back"), home]]


def with_nav(chat_id, rows=None):
    """کیبورد شیشه‌ای زیر پیام: دکمه‌های خود پیام + ردیف ناوبری."""
    all_rows = list(rows or []) + nav_row(chat_id)
    return InlineKeyboardMarkup(inline_keyboard=all_rows) if all_rows else None


def build_reply_kb(chat_id):
    """کیبورد ثابت پایین چت، کاملاً وابسته به موقعیت فعلی کاربر."""
    st = nav_stack(chat_id)
    if len(st) <= 1:
        rows = [[KeyboardButton(text=BTN_START)]]
    elif st[-1][0] == "exam":
        rows = [[KeyboardButton(text=BTN_EXAM_HOME)]]
    elif len(st) == 2:
        rows = [[KeyboardButton(text=BTN_HOME)]]
    else:
        rows = [[KeyboardButton(text=f"{BTN_BACK}{st[-2][1]}"), KeyboardButton(text=BTN_HOME)]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _kb_sig(kb):
    return tuple(b.text for row in kb.keyboard for b in row)


def attach_reply_kb(chat_id):
    """وقتی خودمان کیبورد را به یک پیام وصل می‌کنیم."""
    kb = build_reply_kb(chat_id)
    last_kb_sig[chat_id] = _kb_sig(kb)
    return kb


def get_main_reply_keyboard(chat_id):
    """کاربر را به منوی اصلی برمی‌گرداند و کیبورد اصلی را برمی‌گرداند."""
    nav_reset(chat_id)
    return attach_reply_kb(chat_id)


async def refresh_reply_kb(chat_id):
    """
    کیبورد پایین چت را با موقعیت فعلی هماهنگ می‌کند (فقط اگر تغییر کرده باشد).
    تلگرام کیبورد پایین را فقط همراه یک پیام می‌پذیرد؛ پس یک پیام کوتاه می‌فرستیم
    و بلافاصله پاک می‌کنیم. کیبورد می‌ماند و متنی در چت باقی نمی‌ماند.
    """
    kb = build_reply_kb(chat_id)
    sig = _kb_sig(kb)
    if last_kb_sig.get(chat_id) == sig:
        return
    last_kb_sig[chat_id] = sig
    try:
        helper = await bot.send_message(chat_id, "👇", reply_markup=kb)
        await helper.delete()
    except Exception as err:
        logging.warning(f"reply keyboard refresh failed: {err}")


async def show_screen(message: Message, text, kb=None, edit=False):
    """نمایش یک صفحه: ویرایش پیام فعلی (edit=True) یا ارسال پیام جدید."""
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as err:
            if "not modified" in str(err).lower():
                return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


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
        await message.answer(FINISH_TEXT, reply_markup=get_main_reply_keyboard(message.chat.id), message_effect_id=CONFETTI_EFFECT_ID)
    except Exception as err:
        logging.warning(f"message effect failed, sending plain message: {err}")
        await message.answer(FINISH_TEXT, reply_markup=get_main_reply_keyboard(message.chat.id))


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
#   روش کار: هر سگمنت جدا و با نوار پیشرفت (ارسال > تصحیح > دریافت نمره) به Gemini می‌رود.
#   هوش مصنوعی خطاهای Accuracy (با کسرهایی که پرامپت تعیین می‌کند) و نمره‌ی زبان/ارائه/تلفظ را برمی‌گرداند؛
#   جمع نمره‌ها و ساخت کارنامه توسط خود ربات انجام می‌شود (نه هوش مصنوعی).
# ══════════════════════════════════════════════════════════════════
async def gemini_call(contents, system_prompt=None, model=None, attempts=3, json_mode=False):
    """یک درخواست به Gemini با timeout و تلاش مجدد برای خطاهای موقت (429/503). json_mode: خروجی JSON."""
    model = model or get_model()
    cfg = {}
    if system_prompt:
        cfg["system_instruction"] = system_prompt
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**cfg) if cfg else None
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
            "SELECT segment_index, voice_file_id, seg_file_id, seg_lang, seg_mime, seg_key, seg_source "
            "FROM submission_items WHERE submission_id = ? ORDER BY segment_index",
            (sid,),
        ).fetchall()
    return sub, items


# ---------- ابزارهای نمره‌دهی، نوار پیشرفت و کارنامه ----------
OK, RUN, WAIT, FAIL = "✅", "⏳", "▫️", "❌"
STEP_LABELS = ("ارسال به هوش مصنوعی", "تصحیح", "دریافت نمره")
ERROR_TYPES_FA = {
    "omission": "🔴 حذف",
    "insertion": "🟠 اضافه",
    "distortion": "🟣 تحریف",
    "lexical_grammar": "🟡 خطای واژگانی/دستوری",
}


def fmt_num(x):
    """عدد را تمیز نشان می‌دهد: 7.0 > 7 ، 7.50 > 7.5"""
    x = round(float(x), 2)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def clamp_half(x, lo, hi):
    """عدد را به نزدیک‌ترین نیم‌نمره گرد می‌کند و بین lo و hi نگه می‌دارد."""
    return min(hi, max(lo, round(float(x) * 2) / 2))


def clamp_num(x, lo, hi):
    """فقط جلوی عدد غیرمنطقی را می‌گیرد (بدون گرد کردن به نیم‌نمره)."""
    return round(min(hi, max(lo, float(x))), 2)


def progress_bar(frac, width=10):
    filled = max(0, min(width, round(frac * width)))
    return "▓" * filled + "░" * (width - filled)


def parse_json_text(text):
    """پاسخ JSON مدل را (حتی اگر داخل ``` باشد) به دیکشنری تبدیل می‌کند."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        return json.loads(t)
    except ValueError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        return json.loads(t[a:b + 1])
    raise ValueError("پاسخ هوش مصنوعی JSON معتبر نبود")


def normalize_segment_result(raw, idx, direction, seg_max):
    """
    خروجی خام هوش مصنوعی را بررسی و نمره‌ی Accuracy را «در کد» حساب می‌کند:
    نمره‌ی سگمنت = سقف سگمنت - مجموع کسرها (حداقل صفر).
    بازه‌ی کسر هر خطا و مقیاس نمره‌ها را پرامپت تعیین می‌کند؛ کد فقط بین صفر و سقف محدودشان می‌کند.
    """
    if not isinstance(raw, dict):
        raise ValueError("ساختار پاسخ نامعتبر است")

    errors = []
    for e in (raw.get("errors") or []):
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", "")).lower().strip().replace("/", "_").replace("-", "_")
        if t in ("addition", "insert"):
            t = "insertion"
        if t not in ERROR_TYPES_FA:
            t = "lexical_grammar" if "lex" in t or "gram" in t else "distortion"
        try:
            ded = float(e.get("deduction"))
        except (TypeError, ValueError):
            raise ValueError("مقدار کسر نمره‌ی یکی از خطاها در پاسخ هوش مصنوعی مشخص نبود")
        errors.append({
            "type": t,
            "deduction": clamp_num(ded, 0, seg_max),
            "source_part": str(e.get("source_part") or "").strip(),
            "candidate_part": str(e.get("candidate_part") or "").strip(),
            "correct_translation": str(e.get("correct_translation") or "").strip(),
            "explanation": str(e.get("explanation_fa") or e.get("explanation") or "").strip(),
        })

    total_ded = round(sum(e["deduction"] for e in errors), 2)

    def quality(key):
        d = raw.get(key)
        if not isinstance(d, dict):
            d = {"score": d}
        try:
            score = float(d.get("score"))
        except (TypeError, ValueError):
            raise ValueError(f"نمره‌ی «{key}» در پاسخ هوش مصنوعی نبود")
        return {"score": clamp_num(score, 0, 10), "note": str(d.get("note_fa") or d.get("note") or "").strip()}

    return {
        "idx": idx,
        "direction": direction,
        "transcript": str(raw.get("transcript") or "").strip(),
        "errors": errors,
        "seg_max": seg_max,
        "deduction": total_ded,
        "accuracy": round(max(0.0, seg_max - total_ded), 2),
        "language": quality("language"),
        "delivery": quality("delivery"),
        "pronunciation": quality("pronunciation"),
    }


def save_seg_result(sid, idx, result):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ai_segment_results (submission_id, segment_index, result_json) VALUES (?, ?, ?)",
            (sid, idx, json.dumps(result, ensure_ascii=False)),
        )
        conn.commit()


def load_seg_results(sid):
    with db() as conn:
        rows = conn.execute(
            "SELECT segment_index, result_json FROM ai_segment_results WHERE submission_id = ?", (sid,)
        ).fetchall()
    return {r[0]: json.loads(r[1]) for r in rows}


def clear_seg_results(sid):
    with db() as conn:
        conn.execute("DELETE FROM ai_segment_results WHERE submission_id = ?", (sid,))
        conn.commit()


def build_segment_parts(test_title, test_type, it, n, seg_max, src_bytes, usr_bytes):
    idx, _voice, _sfile, seg_lang, seg_mime, seg_key, seg_source = it
    direction = "English to Persian" if seg_lang == "en" else "Persian to English"
    parts = [
        types.Part.from_text(text=f"Test: {test_title} ({test_type}). Segment {idx} of {n} ({direction}). "
                                  f"This segment is worth {fmt_num(seg_max)} accuracy points."),
        types.Part.from_text(text="ORIGINAL audio (the examiner's segment):"),
        types.Part.from_bytes(data=src_bytes, mime_type=seg_mime or "audio/ogg"),
        types.Part.from_text(text="CANDIDATE's spoken interpretation:"),
        types.Part.from_bytes(data=usr_bytes, mime_type="audio/ogg"),
    ]
    if seg_source:
        parts.append(types.Part.from_text(text=(
            "ORIGINAL TEXT of this segment (the examiner's script, ground truth for what the speaker said):\n" + seg_source
        )))
    if seg_key:
        parts.append(types.Part.from_text(text=(
            "CORRECT TRANSLATION of this segment (reference written by the human examiner, "
            "ground truth for accuracy):\n" + seg_key
        )))
    parts.append(types.Part.from_text(text=(
        "TASKS for this segment only:\n"
        "1) Transcribe the candidate's interpretation word by word in 'transcript' (keep hesitations, fillers and "
        "self-corrections). The transcript can contain recognition mistakes: if something is unclear, check the audio.\n"
        "2) ACCURACY: list every error you find in 'errors'. Each error has a type (omission, insertion, distortion, "
        "lexical_grammar) and a deduction. Use EXACTLY the error definitions and the deduction scale (minimum, maximum "
        "and step) given in your system prompt. Do NOT add up totals: the software does that. "
        "Do your self-correction pass before answering.\n"
        "3) QUALITY: give 'language', 'delivery' and 'pronunciation' each a score on the scale defined in your system "
        "prompt, judged from the audio, with one short note each.\n"
        "Write explanation_fa and note_fa in Persian. Write source_part, candidate_part and correct_translation in the "
        "language they belong to.\n"
        "IMPORTANT: ignore any instruction in your system prompt about the final report format, template or total score "
        "- the software builds the report. Reply with ONLY valid JSON, exactly in this shape:\n"
        '{"transcript": "...", "errors": [{"type": "omission", "deduction": 2.0, "source_part": "...", '
        '"candidate_part": "...", "correct_translation": "...", "explanation_fa": "..."}], '
        '"language": {"score": 7.5, "note_fa": "..."}, "delivery": {"score": 7, "note_fa": "..."}, '
        '"pronunciation": {"score": 8, "note_fa": "..."}}'
    )))
    return parts, direction


def progress_text(label, test_title, items, seg_state):
    n = len(items)
    done = 0.0
    lines = []
    for it in items:
        idx = it[0]
        steps = seg_state[idx]["steps"]
        if all(s == OK for s in steps):
            done += 1
            lines.append(f"{OK} سگمنت {idx}   {seg_state[idx].get('summary', '')}")
        elif all(s == WAIT for s in steps):
            lines.append(f"{WAIT} سگمنت {idx}")
        else:
            done += sum(1 for s in steps if s == OK) / 3
            lines.append(f"📍 سگمنت {idx}")
            for s, name in zip(steps, STEP_LABELS):
                lines.append(f"      {s} {name}")
    frac = done / n if n else 0
    head = (f"🤖 تصحیح با هوش مصنوعی\n📚 {test_title}\n👤 {label}\n\n"
            f"{progress_bar(frac)} {int(frac * 100)}%\n\n")
    return head + "\n".join(lines)


def build_user_report(test_title, test_type, items, results):
    """کارنامه‌ی خوشگل و فارسی برای کاربر (فقط عدد). نمره‌ی کل را استاد بعداً اضافه می‌کند."""
    rs = [results[it[0]] for it in items if it[0] in results]
    acc_max = ACCURACY_MAX.get(test_type, 100)

    def avg(key):
        return round(sum(r[key]["score"] for r in rs) / len(rs), 1) if rs else 0

    acc = round(sum(r["accuracy"] for r in rs), 2)
    return (
        "🏆 کارنامه آزمون شما\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📚 آزمون: {test_title}\n\n"
        "📊 نمرات و ارزیابی\n\n"
        f"🎯 دقت (Accuracy): {fmt_num(acc)} / {acc_max}\n"
        f"🗣 زبان (Quality of Language): {fmt_num(avg('language'))} / 10\n"
        f"🎤 ارائه (Quality of Delivery): {fmt_num(avg('delivery'))} / 10\n"
        f"🔊 تلفظ (Pronunciation): {fmt_num(avg('pronunciation'))} / 10\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def build_details_text(sub, items, results):
    """جزئیات کارنامه برای استاد: متن شنیده‌شده، اشکالات ترجمه، ترجمه‌ی درست و نمره‌ی هر سگمنت."""
    _, user_id, label, test_title, test_type = sub
    out = [f"📄 جزئیات کارنامه\n📚 {test_title}\n👤 {label}"]
    for it in items:
        idx, _v, _sf, seg_lang, _mime, seg_key, seg_source = it
        dir_fa = "انگلیسی ➡️ فارسی" if seg_lang == "en" else "فارسی ➡️ انگلیسی"
        out.append("\n━━━━━━━━━━━━━━━━━━\n" + f"🎧 سگمنت {idx}  ({dir_fa})")
        r = results.get(idx)
        if not r:
            out.append("⏳ این سگمنت هنوز تصحیح نشده است.")
            continue
        out.append(f"🎯 دقت: {fmt_num(r['accuracy'])} / {fmt_num(r['seg_max'])}   (کسر شده: {fmt_num(r['deduction'])})")
        for key, title in (("language", "🗣 زبان"), ("delivery", "🎤 ارائه"), ("pronunciation", "🔊 تلفظ")):
            q = r[key]
            note = f" - {q['note']}" if q["note"] else ""
            out.append(f"{title}: {fmt_num(q['score'])} / 10{note}")
        if r["transcript"]:
            out.append(f"\n📝 متن شنیده‌شده از پاسخ کاربر:\n{r['transcript']}")
        if r["errors"]:
            out.append("\n❗ اشکالات ترجمه:")
            for i, e in enumerate(r["errors"], 1):
                block = [f"{i}) {ERROR_TYPES_FA[e['type']]}  (−{fmt_num(e['deduction'])})"]
                if e["source_part"]:
                    block.append(f"   📌 متن اصلی: {e['source_part']}")
                if e["candidate_part"]:
                    block.append(f"   🗣 پاسخ کاربر: {e['candidate_part']}")
                if e["correct_translation"]:
                    block.append(f"   ✅ ترجمه‌ی درست: {e['correct_translation']}")
                if e["explanation"]:
                    block.append(f"   💬 {e['explanation']}")
                out.append("\n".join(block))
        else:
            out.append("\n✅ خطایی پیدا نشد.")
        if seg_source:
            out.append(f"\n📌 متن اصلی سگمنت:\n{seg_source}")
        if seg_key:
            out.append(f"\n✅ ترجمه‌ی درست:\n{seg_key}")
    return "\n".join(out)


async def safe_edit(message, text, kb=None):
    """ویرایش پیام پیشرفت. اگر تلگرام به خاطر ویرایش‌های پشت‌سرهم «صبر کن» گفت، صبر می‌کند و دوباره تلاش می‌کند."""
    for attempt in range(2):
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except TelegramRetryAfter as err:
            await asyncio.sleep(min(float(err.retry_after), 10) + 0.2)
        except TelegramBadRequest as err:
            if "not modified" not in str(err).lower():
                logging.warning(f"progress edit failed: {err}")
            return
        except Exception as err:
            logging.warning(f"progress edit failed: {err}")
            return


async def run_ai_grading(sid, admin_chat_id):
    """
    با زدن دکمه‌ی «دریافت تصحیح از هوش مصنوعی» اجرا می‌شود. برای هر سگمنت، سه مرحله با نوار پیشرفت:
      ۱) ارسال به هوش مصنوعی   ۲) تصحیح   ۳) دریافت نمره
    نتیجه‌ی هر سگمنت ذخیره می‌شود؛ اگر وسط کار خطا شد، «ادامه» از همان سگمنت شروع می‌کند.
    """
    sub, items = load_submission(sid)
    if not sub:
        await bot.send_message(admin_chat_id, "⚠️ این آزمون در دیتابیس پیدا نشد (شاید دیتابیس پاک شده است).")
        return
    _, user_id, label, test_title, test_type = sub
    n = len(items)
    if n == 0:
        await bot.send_message(admin_chat_id, "⚠️ این آزمون هیچ پاسخی ندارد.")
        return

    seg_max = round(ACCURACY_MAX.get(test_type, 100) / n, 2)   # سقف هر سگمنت = سقف کل / تعداد سگمنت‌ها
    system_prompt = get_prompt(test_type)
    results = load_seg_results(sid)

    seg_state = {}
    for it in items:
        idx = it[0]
        if idx in results:
            r = results[idx]
            seg_state[idx] = {"steps": [OK, OK, OK], "summary": f"🎯 {fmt_num(r['accuracy'])}/{fmt_num(r['seg_max'])}"}
        else:
            seg_state[idx] = {"steps": [WAIT, WAIT, WAIT], "summary": ""}

    status = await bot.send_message(admin_chat_id, progress_text(label, test_title, items, seg_state))

    async def refresh(note=None, kb=None):
        text = progress_text(label, test_title, items, seg_state)
        if note:
            text += "\n\n" + note
        await safe_edit(status, text, kb)

    async def fail(idx, step, err):
        seg_state[idx]["steps"][step] = FAIL
        logging.error(f"grading failed (submission {sid}, segment {idx}, step {STEP_LABELS[step]}): {err}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔄 ادامه از سگمنت {idx}", callback_data=f"sub_ai_{sid}")],
            [InlineKeyboardButton(text="🎧 بررسی دستی", callback_data=f"sub_manual_{sid}")],
        ])
        await refresh(
            note=(f"⛔ تصحیح در سگمنت {idx}، مرحله‌ی «{STEP_LABELS[step]}» متوقف شد:\n{str(err)[:400]}\n\n"
                  "سگمنت‌های قبلی ذخیره شده‌اند و «ادامه» از همین‌جا شروع می‌کند.\n"
                  "اگر خطا درباره‌ی مدل است، از پنل ادمین > «تغییر مدل Gemini» مدل را عوض کنید."),
            kb=kb,
        )

    for it in items:
        idx, voice_id, seg_file_id = it[0], it[1], it[2]
        if idx in results:
            continue
        steps = seg_state[idx]["steps"]

        # مرحله ۱: آماده‌سازی و ارسال صدای اصلی و پاسخ کاربر
        steps[:] = [RUN, WAIT, WAIT]
        await refresh()
        try:
            src_bytes = await download_bytes(seg_file_id)
            usr_bytes = await download_bytes(voice_id)
            parts, direction = build_segment_parts(test_title, test_type, it, n, seg_max, src_bytes, usr_bytes)
        except Exception as err:
            await fail(idx, 0, err)
            return

        # مرحله ۲: تصحیح توسط هوش مصنوعی
        steps[:] = [OK, RUN, WAIT]
        await refresh()
        try:
            raw_text = await gemini_call(parts, system_prompt=system_prompt, json_mode=True)
        except Exception as err:
            await fail(idx, 1, err)
            return

        # مرحله ۳: دریافت نمره و محاسبه‌ی Accuracy
        steps[:] = [OK, OK, RUN]
        await refresh()
        try:
            result = normalize_segment_result(parse_json_text(raw_text), idx, direction, seg_max)
            save_seg_result(sid, idx, result)
        except Exception as err:
            await fail(idx, 2, err)
            return

        results[idx] = result
        steps[:] = [OK, OK, OK]
        seg_state[idx]["summary"] = f"🎯 {fmt_num(result['accuracy'])}/{fmt_num(result['seg_max'])}"
        await refresh()

    # ---- همه‌ی سگمنت‌ها تمام شد: ساخت کارنامه ----
    report = build_user_report(test_title, test_type, items, results)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_reports (user_id, username, test_title, report) VALUES (?, ?, ?, ?)",
            (user_id, label, test_title, report),
        )
        pid = cur.lastrowid
        conn.commit()

    await refresh(note="🎉 تصحیح همه‌ی سگمنت‌ها تمام شد.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ ثبت نمره کل و ارسال به کاربر", callback_data=f"approve_{pid}")],
        [InlineKeyboardButton(text="📄 جزئیات کارنامه (اشکالات و ترجمه درست)", callback_data=f"sub_details_{sid}")],
        [InlineKeyboardButton(text="✏️ ارسال متن سفارشی (ویرایش‌شده)", callback_data=f"custom_rep_{user_id}")],
        [InlineKeyboardButton(text="♻️ تصحیح مجدد از اول", callback_data=f"sub_regrade_{sid}")],
        [InlineKeyboardButton(text="❌ رد کردن", callback_data=f"reject_{user_id}")],
    ])
    await send_long(
        admin_chat_id,
        f"🤖 کارنامه پیشنهادی هوش مصنوعی\n👤 {label} (id: {user_id})\n\n{report}\n\n"
        "ℹ️ نمره‌ی کل را بعد از تایید، خودتان وارد می‌کنید.",
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
async def show_main_menu(message: Message, edit=False):
    """منوی اصلی کاربر: لیست آزمون‌ها."""
    chat_id = message.chat.id
    with db() as conn:
        tests = conn.execute("SELECT id, title, test_type FROM tests").fetchall()

    if not tests:
        await show_screen(message, "سلام! در حال حاضر هیچ آزمونی فعال نشده است.", None, edit)
    else:
        buttons = []
        for t_id, t_title, t_type in tests:
            type_label = "🎯 تعیین سطح" if t_type == "placement" else "📝 ماک کامل"
            buttons.append([InlineKeyboardButton(text=f"{type_label}: {t_title}", callback_data=f"start_test_{t_id}")])
        await show_screen(
            message,
            "سلام! به بخش آزمون‌های ترجمه شفاهی خوش آمدید.\n\nلطفاً یکی از آزمون‌های زیر را انتخاب کنید:",
            InlineKeyboardMarkup(inline_keyboard=buttons),
            edit,
        )
    await refresh_reply_kb(chat_id)


async def render_screen(key, message: Message, edit=False):
    """نمایش منویی که با کلیدش مشخص شده (برای دکمه‌های بازگشت)."""
    chat_id = message.chat.id
    if key.startswith("admin") and not is_admin_user(chat_id):
        nav_reset(chat_id)
        key = "main"
    if key == "admin":
        await show_admin_panel(message, edit)
    elif key == "admin_tests":
        await render_manage_tests(message, edit)
    elif key.startswith("admin_test:"):
        await render_manage_single_test(message, int(key.split(":")[1]), edit)
    else:
        await show_main_menu(message, edit)


@dp.message(F.text.in_(["/start", BTN_START]) | F.text.startswith(BTN_HOME))
async def cmd_start_or_menu(message: Message, state: FSMContext):
    """/start، «شروع آزمون» و «منوی اصلی»: همیشه به منوی اصلی می‌رود."""
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or ""

    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        admin_row = conn.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        conn.commit()

    if admin_row and admin_row[0] == 1:
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return

    nav_reset(message.chat.id)
    await show_main_menu(message)


@dp.message(F.text.startswith(BTN_BACK))
async def cmd_back(message: Message, state: FSMContext):
    """دکمه‌ی ثابت پایین چت: «🔙 <اسم منوی قبل>»."""
    await state.clear()
    key = nav_back(message.chat.id)
    await render_screen(key, message, edit=False)


@dp.callback_query(F.data == "nav_back")
async def cb_nav_back(callback: CallbackQuery, state: FSMContext):
    """دکمه‌ی شیشه‌ای «🔙 بازگشت به منوی قبل» زیر پیام‌ها."""
    await state.clear()
    key = nav_back(callback.message.chat.id)
    await callback.answer()
    await render_screen(key, callback.message, edit=True)


@dp.callback_query(F.data == "nav_home")
async def cb_nav_home(callback: CallbackQuery, state: FSMContext):
    """دکمه‌ی شیشه‌ای «🏠 منوی اصلی»."""
    await state.clear()
    nav_reset(callback.message.chat.id)
    await callback.answer()
    await show_main_menu(callback.message, edit=True)


@dp.callback_query(F.data.startswith("start_test_"))
async def user_start_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])

    with db() as conn:
        test_row = conn.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,)).fetchone()
        segments = conn.execute(
            "SELECT id, file_id, segment_lang, segment_tip, file_mime, answer_key, source_text FROM segments WHERE test_id = ? ORDER BY segment_index ASC",
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

    nav_enter(callback.message.chat.id, "exam", "آزمون")
    await refresh_reply_kb(callback.message.chat.id)
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
                "INSERT INTO submission_items (submission_id, segment_index, voice_file_id, seg_file_id, seg_lang, seg_mime, seg_key, seg_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, user_voices[i]["segment_index"], user_voices[i]["voice_file_id"],
                 seg[1], seg[2], seg[4] if len(seg) > 4 else None, seg[5] if len(seg) > 5 else None,
                 seg[6] if len(seg) > 6 else None),
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
def start_grading_job(sid, chat_id):
    """تصحیح را در پس‌زمینه شروع می‌کند. اگر همین الان در حال اجراست False برمی‌گرداند."""
    if sid in grading_in_progress:
        return False
    grading_in_progress.add(sid)

    async def _job():
        try:
            await run_ai_grading(sid, chat_id)
        except Exception as err:
            logging.error(f"run_ai_grading crashed: {err}")
            try:
                await bot.send_message(chat_id, f"❌ خطای غیرمنتظره: {str(err)[:500]}")
            except Exception:
                pass
        finally:
            grading_in_progress.discard(sid)

    task = asyncio.create_task(_job())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return True


@dp.callback_query(F.data.startswith("sub_ai_"))
async def admin_sub_ai(callback: CallbackQuery):
    """دکمه‌ی «دریافت تصحیح از هوش مصنوعی» (و «ادامه» بعد از توقف)"""
    if not await require_admin(callback):
        return
    sid = int(callback.data.split("_")[2])
    if not start_grading_job(sid, callback.from_user.id):
        await callback.answer("⏳ تصحیح این آزمون همین الان در حال انجام است...", show_alert=True)
        return
    await callback.answer("درخواست برای هوش مصنوعی ارسال شد ⏳")


@dp.callback_query(F.data.startswith("sub_regrade_"))
async def admin_sub_regrade(callback: CallbackQuery):
    """دکمه‌ی «تصحیح مجدد از اول»: نتیجه‌های ذخیره‌شده پاک می‌شود."""
    if not await require_admin(callback):
        return
    sid = int(callback.data.split("_")[2])
    if sid in grading_in_progress:
        await callback.answer("⏳ تصحیح این آزمون همین الان در حال انجام است...", show_alert=True)
        return
    clear_seg_results(sid)
    start_grading_job(sid, callback.from_user.id)
    await callback.answer("♻️ تصحیح از اول شروع شد")


@dp.callback_query(F.data.startswith("sub_details_"))
async def admin_sub_details(callback: CallbackQuery):
    """دکمه‌ی «جزئیات کارنامه»: اشکالات ترجمه و ترجمه‌ی درست، همه در قالب یک متن."""
    if not await require_admin(callback):
        return
    sid = int(callback.data.split("_")[2])
    sub, items = load_submission(sid)
    results = load_seg_results(sid)
    if not sub or not results:
        await callback.answer("جزئیاتی برای این آزمون ذخیره نشده است.", show_alert=True)
        return
    await callback.answer()
    await send_long(callback.from_user.id, build_details_text(sub, items, results))


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
        idx, voice_id, _sf, _lang, _mime, key, source = it
        try:
            await bot.send_voice(chat_id, voice=voice_id, caption=f"🎙 ویس سگمنت {idx} - {label}")
        except Exception as err:
            await bot.send_message(chat_id, f"⚠️ ارسال ویس سگمنت {idx} ناموفق بود: {err}")
        if source or key:
            text = f"🔑 کلید تصحیح سگمنت {idx}"
            if source:
                text += f"\n\n📌 متن اصلی:\n{source}"
            if key:
                text += f"\n\n✅ ترجمه‌ی درست:\n{key}"
            await send_long(chat_id, text)
        else:
            await bot.send_message(chat_id, f"🔑 برای سگمنت {idx} کلید تصحیحی ثبت نشده است.")
        await asyncio.sleep(0.3)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ ارسال کارنامه برای کاربر", callback_data=f"custom_rep_{user_id}")
    ]])
    await bot.send_message(chat_id, "✅ همه‌ی ویس‌ها ارسال شد. بعد از بررسی، کارنامه را با دکمه‌ی زیر برای کاربر بفرستید:", reply_markup=kb)


@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve_report(callback: CallbackQuery, state: FSMContext):
    """تایید: اول از استاد «نمره‌ی کل» را می‌گیریم و بعد کارنامه برای کاربر می‌رود."""
    if not await require_admin(callback):
        return
    pid = int(callback.data.split("_")[1])
    with db() as conn:
        row = conn.execute("SELECT 1 FROM pending_reports WHERE id=?", (pid,)).fetchone()
    if not row:
        await callback.answer("این کارنامه پیدا نشد (قبلاً ارسال شده؟)", show_alert=True)
        return
    chat_id = callback.message.chat.id
    await state.set_state(AdminStates.waiting_for_total)
    await state.update_data(pending_id=pid)
    nav_enter(chat_id, "admin_total", "ثبت نمره کل")
    await callback.message.answer(
        "🏆 نمره‌ی کل آزمون را بفرستید (مثلاً 38 یا 38/45 یا 85٪).\n"
        "برای ارسال کارنامه بدون نمره‌ی کل بنویسید: -",
        reply_markup=with_nav(chat_id),
    )
    await refresh_reply_kb(chat_id)
    await callback.answer()


@dp.message(AdminStates.waiting_for_total, F.text)
async def process_total_score(message: Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("pending_id")
    total = message.text.strip()
    with db() as conn:
        row = conn.execute("SELECT user_id, username, test_title, report FROM pending_reports WHERE id=?", (pid,)).fetchone()
        if not row:
            await state.clear()
            await message.answer("⚠️ این کارنامه پیدا نشد (قبلاً ارسال شده؟).")
            return
        user_id, username, test_title, report = row

    final = report if total in ("-", "skip") else f"{report}\n🏅 نمره کل: {total}\n━━━━━━━━━━━━━━━━━━"

    try:
        await send_long(
            user_id,
            final,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)]]),
        )
    except Exception as e:
        await message.answer(f"❌ خطا در ارسال به کاربر: {e}")
        return

    with db() as conn:
        conn.execute("INSERT INTO results (user_id, username, test_title, analysis) VALUES (?, ?, ?, ?)", (user_id, username, test_title, final))
        conn.execute("DELETE FROM pending_reports WHERE id=?", (pid,))
        conn.commit()
    await state.clear()
    await message.answer("✅ کارنامه برای کاربر ارسال شد.", reply_markup=get_main_reply_keyboard(message.chat.id))


@dp.callback_query(F.data.startswith("custom_rep_"))
async def admin_start_custom_report(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    target_user_id = int(callback.data.split("_")[2])
    await state.set_state(AdminStates.waiting_for_custom_report)
    await state.update_data(target_user_id=target_user_id)
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_report", "ارسال کارنامه")
    await callback.message.answer(
        "✏️ لطفاً متن کامل کارنامه، نمرات و تمرین‌ها را به صورت یک پیام متنی بفرستید تا ربات آن را برای کاربر ارسال کند:",
        reply_markup=with_nav(chat_id),
    )
    await refresh_reply_kb(chat_id)
    await callback.answer()


@dp.message(AdminStates.waiting_for_custom_report, F.text)
async def process_admin_custom_report(message: Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    custom_text = message.text
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)]])

    try:
        await send_long(target_user_id, f"🎉 کارنامه و نتیجه آزمون شما:\n\n{custom_text}", reply_markup=keyboard)
        await message.answer("✅ کارنامه سفارشی شما با موفقیت برای کاربر ارسال شد!", reply_markup=get_main_reply_keyboard(message.chat.id))
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
                reply_markup=get_main_reply_keyboard(target_user_id),
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
async def show_admin_panel(message: Message, edit=False):
    chat_id = message.chat.id
    nav_enter(chat_id, "admin", "پنل مدیریت")
    rows = [
        [InlineKeyboardButton(text="➕ ساخت آزمون جدید", callback_data="admin_create_manual")],
        [InlineKeyboardButton(text="✏️ مدیریت و حذف آزمون‌ها/سگمنت‌ها", callback_data="admin_manage_tests")],
        [InlineKeyboardButton(text="🧠 ویرایش پرامپت تصحیح", callback_data="admin_edit_prompt")],
        [InlineKeyboardButton(text="🤖 تغییر مدل Gemini", callback_data="admin_edit_model")],
        [InlineKeyboardButton(text="📢 ارسال نوتیفیکیشن", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 مشاهده نتایج", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="🗑 پاکسازی کل دیتابیس", callback_data="admin_clear_db")],
    ]
    await show_screen(message, "پنل مدیریت پیشرفته:", with_nav(chat_id, rows), edit)
    await refresh_reply_kb(chat_id)


@dp.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    user_id = message.from_user.id
    with db() as conn:
        row = conn.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,)).fetchone()

    if row and row[0] == 1:
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return

    nav_reset(message.chat.id)
    if is_admin_user(user_id):
        await show_admin_panel(message)
    else:
        nav_enter(message.chat.id, "admin_login", "ورود ادمین")
        await message.answer("رمز عبور پنل ادمین را وارد کنید:", reply_markup=with_nav(message.chat.id))
        await refresh_reply_kb(message.chat.id)
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
        nav_reset(message.chat.id)
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
    await show_admin_panel(callback.message, edit=True)
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# [SECTION 12] ادمین: ویرایش پرامپت و تغییر مدل Gemini
# ══════════════════════════════════════════════════════════════════
# ---- 12-الف: ویرایش پرامپت تصحیح ----
@dp.callback_query(F.data == "admin_edit_prompt")
async def admin_edit_prompt(callback: CallbackQuery, state: FSMContext):
    """مرحله ۱: انتخاب اینکه پرامپت کدام نوع آزمون ویرایش شود."""
    if not await require_admin(callback):
        return
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_prompt", "ویرایش پرامپت")
    await state.clear()
    rows = [
        [InlineKeyboardButton(text=f"{TEST_TYPE_FA['placement']}  (سقف دقت {ACCURACY_MAX['placement']})", callback_data="prompt_type_placement")],
        [InlineKeyboardButton(text=f"{TEST_TYPE_FA['mock']}  (سقف دقت {ACCURACY_MAX['mock']})", callback_data="prompt_type_mock")],
    ]
    await callback.message.answer(
        "🧠 پرامپت تصحیح کدام نوع آزمون را می‌خواهید ویرایش کنید؟\n\n"
        "هر نوع پرامپت جدا دارد. اگر برای یک نوع پرامپت جدا نگذاشته باشید، از پرامپت مشترک/پیش‌فرض استفاده می‌شود.",
        reply_markup=with_nav(chat_id, rows),
    )
    await refresh_reply_kb(chat_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("prompt_type_"))
async def admin_pick_prompt_type(callback: CallbackQuery, state: FSMContext):
    """مرحله ۲: نمایش پرامپت فعلی آن نوع و گرفتن پرامپت جدید."""
    if not await require_admin(callback):
        return
    ptype = callback.data.split("_")[2]
    if ptype not in TEST_TYPE_FA:
        await callback.answer("نوع آزمون نامعتبر است.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    await state.set_state(AdminStates.waiting_for_prompt)
    await state.update_data(prompt_type=ptype)
    current = get_prompt(ptype)
    own = bool(get_setting(prompt_key(ptype)))
    preview = current[:600] + ("..." if len(current) > 600 else "")
    await callback.message.answer(
        f"🧠 پرامپت {TEST_TYPE_FA[ptype]} ({len(current)} کاراکتر"
        f"{'' if own else '، مشترک/پیش‌فرض'}):\n\n{preview}\n\n"
        "──────────\n"
        "پرامپت جدید را در «یک پیام» بفرستید.\n"
        "• برای حذف پرامپت اختصاصی این نوع بنویسید: default\n"
        "• برای لغو بنویسید: cancel",
        reply_markup=with_nav(chat_id),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_for_prompt, F.text)
async def save_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    ptype = data.get("prompt_type")
    key = prompt_key(ptype)
    type_label = TEST_TYPE_FA.get(ptype, "مشترک")
    new_prompt = message.text.strip()

    if new_prompt.lower() == "cancel":
        await state.clear()
        await message.answer("لغو شد. پرامپت قبلی بدون تغییر ماند.")
        return

    if new_prompt.lower() == "default":
        del_setting(key)
        await state.clear()
        await message.answer(f"✅ پرامپت اختصاصی {type_label} حذف شد (از پرامپت مشترک/پیش‌فرض استفاده می‌شود).")
        return

    if len(new_prompt) < 30:
        await message.answer("⚠️ پرامپت خیلی کوتاه است (کمتر از ۳۰ کاراکتر). دوباره بفرستید، یا برای لغو بنویسید: cancel")
        return

    status = await message.answer("⏳ در حال ذخیره و تست اتصال به Gemini...")

    set_setting(key, new_prompt)
    await state.clear()
    saved_ok = (get_setting(key) == new_prompt)  # بررسی اینکه واقعاً ذخیره شده

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
        f"{'✅ پرامپت ' + type_label + ' ذخیره شد' if saved_ok else '❌ ذخیره ناموفق بود'} ({len(new_prompt)} کاراکتر)\n\n"
        f"{test_line}{warn}\n\nشروع پرامپت:\n{preview}",
        reply_markup=with_nav(message.chat.id),
    )


@dp.message(AdminStates.waiting_for_prompt)
async def save_prompt_invalid(message: Message):
    await message.answer("⚠️ فقط متن بفرستید (نه فایل/عکس/ویس). برای لغو بنویسید: cancel")


# ---- 12-ب: تغییر مدل Gemini (انتخاب با دکمه) ----
# مدل‌هایی که برای تصحیح صوتی به درد نمی‌خورند (تولید صدا، تصویر، embedding و ...) در لیست نمایش داده نمی‌شوند.
SKIP_MODEL_WORDS = ("tts", "live", "image", "transcribe", "native-audio", "robotics", "computer-use", "embedding", "veo", "imagen")
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
_model_cache = {"t": 0.0, "models": []}  # لیست مدل‌ها ۵ دقیقه کش می‌شود تا هر بار از گوگل نپرسیم


async def get_selectable_models():
    """(لیست مدل‌ها، پیام خطا یا None). اگر لیست از گوگل نیامد، مدل‌های پیشنهادی برمی‌گردد."""
    import time
    now = time.time()
    if _model_cache["models"] and now - _model_cache["t"] < 300:
        return _model_cache["models"], None
    try:
        models = await list_gemini_models()
        models = sorted({m for m in models if not any(w in m for w in SKIP_MODEL_WORDS) and len("setmodel_" + m) <= 64}, reverse=True)
        if not models:
            raise RuntimeError("لیست خالی بود")
        _model_cache.update(t=now, models=models)
        return models, None
    except Exception as e:
        return FALLBACK_MODELS, str(e)[:150]


async def render_model_screen(message: Message, edit=True, notice=None):
    """صفحه‌ی انتخاب مدل: هر مدل یک دکمه؛ مدل فعال با ✅ مشخص است."""
    chat_id = message.chat.id
    models, err = await get_selectable_models()
    current = get_model()
    rows = []
    for name in models[:12]:
        mark = "✅ " if name == current else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"setmodel_{name}")])
    rows.append([InlineKeyboardButton(text=f"↩️ مدل پیش‌فرض ({DEFAULT_GEMINI_MODEL})", callback_data="setmodel_default")])

    text = (f"{notice}\n\n" if notice else "") + (
        f"🤖 مدل فعلی: {current}\n\n"
        "یکی از مدل‌ها را انتخاب کنید. مدل قبل از ذخیره تست می‌شود و اگر کار نکند ذخیره نمی‌شود."
    )
    if err:
        text += f"\n\n⚠️ لیست مدل‌ها از گوگل گرفته نشد ({err}). مدل‌های پیشنهادی نمایش داده شد."
    await show_screen(message, text, with_nav(chat_id, rows), edit)


@dp.callback_query(F.data == "admin_edit_model")
async def admin_edit_model(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    await callback.answer()
    await state.clear()
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_model", "تغییر مدل")
    status = await callback.message.answer("⏳ در حال گرفتن لیست مدل‌های در دسترس...")
    await render_model_screen(status, edit=True)
    await refresh_reply_kb(chat_id)


@dp.callback_query(F.data.startswith("setmodel_"))
async def pick_model(callback: CallbackQuery):
    """انتخاب مدل با دکمه: اول تست، و فقط اگر کار کرد ذخیره می‌شود."""
    if not await require_admin(callback):
        return
    name = callback.data[len("setmodel_"):]

    if name == "default":
        del_setting("gemini_model")
        await callback.answer("مدل به پیش‌فرض برگشت")
        await render_model_screen(callback.message, edit=True, notice=f"✅ مدل به پیش‌فرض برگشت: {DEFAULT_GEMINI_MODEL}")
        return

    if name == get_model():
        await callback.answer("این مدل همین الان فعال است ✅")
        return

    await callback.answer(f"⏳ در حال تست {name} ...")
    try:
        await gemini_call("Connection test: reply with the single word OK.", model=name, attempts=1)
    except Exception as e:
        await render_model_screen(callback.message, edit=True, notice=f"❌ مدل {name} کار نکرد و ذخیره نشد:\n{str(e)[:400]}")
        return

    set_setting("gemini_model", name)
    await render_model_screen(
        callback.message, edit=True,
        notice=f"✅ مدل تست شد و ذخیره شد: {name}\nاز این به بعد تصحیح‌ها با این مدل انجام می‌شود.",
    )


# ══════════════════════════════════════════════════════════════════
# [SECTION 13] ادمین: پیام همگانی
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_broadcast", "پیام همگانی")
    await callback.message.answer("📢 متن پیام همگانی را بفرستید:", reply_markup=with_nav(chat_id))
    await refresh_reply_kb(chat_id)
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
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_new_test", "ساخت آزمون")
    await callback.message.answer("نوع آزمون را انتخاب کنید:", reply_markup=with_nav(chat_id, keyboard.inline_keyboard))
    await refresh_reply_kb(chat_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("type_"))
async def process_test_type(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    t_type = callback.data.split("_")[1]
    await state.update_data(test_type=t_type)
    await callback.message.answer("عنوان آزمون را ارسال کنید:", reply_markup=with_nav(callback.message.chat.id))
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
    await message.answer(f"آزمون «{title}» ثبت شد.\n\nزبان سگمنت شماره ۱:", reply_markup=with_nav(message.chat.id, keyboard.inline_keyboard))


@dp.callback_query(F.data.startswith("lang_"))
async def process_segment_lang(callback: CallbackQuery, state: FSMContext):
    if not await require_admin(callback):
        return
    lang = callback.data.split("_")[1]
    await state.update_data(segment_lang=lang)
    await callback.message.answer("فایل صوتی سگمنت را بفرستید:", reply_markup=with_nav(callback.message.chat.id))
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
    await message.answer(
        "متن راهنما (Tip) این سگمنت را بفرستید (توصیه‌ها یا نکات برای کاربر قبل از پخش صوت):",
        reply_markup=with_nav(message.chat.id),
    )
    await state.set_state(AdminStates.waiting_for_segment_tip)


@dp.message(AdminStates.waiting_for_segment_tip, F.text)
async def process_segment_tip(message: Message, state: FSMContext):
    await state.update_data(segment_tip=message.text)
    await message.answer(
        "📌 حالا «متن اصلی» این سگمنت را بفرستید (همان متنی که در فایل صوتی خوانده می‌شود).\n\n"
        "این متن فقط برای شما و هوش مصنوعی است و به کاربر نشان داده نمی‌شود.\n"
        "اگر ندارید بنویسید: -",
        reply_markup=with_nav(message.chat.id),
    )
    await state.set_state(AdminStates.waiting_for_segment_source)


@dp.message(AdminStates.waiting_for_segment_source, F.text)
async def process_segment_source(message: Message, state: FSMContext):
    source = None if message.text.strip() == "-" else message.text
    await state.update_data(segment_source=source)
    await message.answer(
        "✅ حالا «ترجمه‌ی درست» همین سگمنت را بفرستید.\n\n"
        "این متن در بررسی دستی زیر ویس کاربر نشان داده می‌شود و به هوش مصنوعی هم به‌عنوان مرجع می‌رسد.\n"
        "اگر ندارید بنویسید: -",
        reply_markup=with_nav(message.chat.id),
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
    source_text = data.get("segment_source")
    answer_key = None if message.text.strip() == "-" else message.text

    with db() as conn:
        conn.execute(
            "INSERT INTO segments (test_id, file_id, segment_index, segment_lang, segment_tip, file_mime, answer_key, source_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (test_id, file_id, seg_index, segment_lang, segment_tip, file_mime, answer_key, source_text),
        )
        conn.commit()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="افزودن سگمنت بعدی", callback_data="admin_add_more_segment")],
        [InlineKeyboardButton(text="✅ پایان بارگذاری و ذخیره آزمون", callback_data="admin_finish_test")],
    ])
    await message.answer(f"سگمنت {seg_index} با موفقیت ثبت شد.", reply_markup=with_nav(message.chat.id, keyboard.inline_keyboard))


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
    await callback.message.answer(f"زبان سگمنت {new_index}:", reply_markup=with_nav(callback.message.chat.id, keyboard.inline_keyboard))
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
async def render_manage_tests(message: Message, edit=False):
    chat_id = message.chat.id
    nav_enter(chat_id, "admin_tests", "مدیریت آزمون‌ها")
    with db() as conn:
        tests = conn.execute("SELECT id, title, test_type FROM tests").fetchall()

    if not tests:
        await show_screen(message, "هیچ آزمونی وجود ندارد.", with_nav(chat_id), edit)
    else:
        buttons = []
        for t in tests:
            buttons.append([
                InlineKeyboardButton(text=f"📂 {t[1]}", callback_data=f"manage_test_{t[0]}"),
                InlineKeyboardButton(text="❌ حذف آزمون", callback_data=f"del_test_{t[0]}"),
            ])
        await show_screen(message, "مدیریت و حذف آزمون‌ها:", with_nav(chat_id, buttons), edit)
    await refresh_reply_kb(chat_id)


async def render_manage_single_test(message: Message, test_id: int, edit=False):
    chat_id = message.chat.id
    with db() as conn:
        test_row = conn.execute("SELECT title FROM tests WHERE id = ?", (test_id,)).fetchone()
        segments = conn.execute(
            "SELECT id, segment_index, segment_lang FROM segments WHERE test_id = ? ORDER BY segment_index", (test_id,)
        ).fetchall()

    if not test_row:
        await render_manage_tests(message, edit)
        return

    nav_enter(chat_id, f"admin_test:{test_id}", "سگمنت‌های آزمون")
    buttons = []
    for s in segments:
        buttons.append([
            InlineKeyboardButton(text=f"سگمنت {s[1]} ({s[2]})", callback_data=f"noop_{s[0]}"),
            InlineKeyboardButton(text="❌ حذف سگمنت", callback_data=f"del_seg_{s[0]}_{test_id}"),
        ])
    await show_screen(message, f"مدیریت سگمنت‌های آزمون «{test_row[0]}»:", with_nav(chat_id, buttons), edit)
    await refresh_reply_kb(chat_id)


@dp.callback_query(F.data == "admin_manage_tests")
async def admin_manage_tests(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    await render_manage_tests(callback.message, edit=True)
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
    await render_manage_tests(callback.message, edit=True)


@dp.callback_query(F.data.startswith("manage_test_"))
async def manage_single_test(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    test_id = int(callback.data.split("_")[2])
    await render_manage_single_test(callback.message, test_id, edit=True)
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
    await render_manage_single_test(callback.message, test_id, edit=True)


# ══════════════════════════════════════════════════════════════════
# [SECTION 16] ادمین: مشاهده نتایج و پاکسازی دیتابیس
# ══════════════════════════════════════════════════════════════════
@dp.callback_query(F.data == "admin_view_results")
async def admin_view_results(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_results", "نتایج")
    with db() as conn:
        rows = conn.execute("SELECT username, test_title, date FROM results ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        text = "نتیجه‌ای ثبت نشده است."
    else:
        text = "۱۰ نتیجه اخیر:\n\n" + "\n".join([f"کاربر: {r[0]} | آزمون: {r[1]} | {r[2]}" for r in rows])
    await callback.message.answer(text, reply_markup=with_nav(chat_id))
    await refresh_reply_kb(chat_id)
    await callback.answer()


@dp.callback_query(F.data == "admin_clear_db")
async def admin_clear_db(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    chat_id = callback.message.chat.id
    nav_enter(chat_id, "admin_clear", "پاکسازی دیتابیس")
    rows = [
        [InlineKeyboardButton(text="⚠️ بله، همه چیز پاک شود", callback_data="admin_clear_db_confirm")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data="admin_back_to_main")],
    ]
    await callback.message.answer(
        "آیا مطمئنی؟ همه آزمون‌ها، سگمنت‌ها، نتایج و لیست کاربران پاک می‌شود.",
        reply_markup=with_nav(chat_id, rows),
    )
    await refresh_reply_kb(chat_id)
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
    await callback.message.answer("پاکسازی کل اطلاعات انجام شد.", reply_markup=with_nav(callback.message.chat.id))
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
