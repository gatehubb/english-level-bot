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

# ============================================================
# تنظیمات (همه از Environment Variable خوانده می‌شوند)
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
DB_PATH = os.environ.get("DB_PATH", "bot_database.db")

CHANNEL_USERNAME = "@Free_naati"
CHANNEL_LINK = "https://t.me/Free_naati"

missing = [k for k, v in {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "GEMINI_API_KEY": GEMINI_API_KEY,
}.items() if not v]
if missing:
    raise RuntimeError(f"Environment variable(s) not set: {', '.join(missing)}")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
gemini = genai.Client(api_key=GEMINI_API_KEY)

background_tasks = set()  # نگه‌داری رفرنس تسک‌ها تا garbage collect نشوند

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

# ============================================================
# Flask (برای health check هاستینگ)
# ============================================================
app = Flask(__name__)


@app.route("/")
def health_check():
    return "Bot is running alive!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


# ============================================================
# دیتابیس
# ============================================================
def db():
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

        # مهاجرت: ستون نوع فایل صوتی
        try:
            c.execute("ALTER TABLE segments ADD COLUMN file_mime TEXT")
        except sqlite3.OperationalError:
            pass

        # مهاجرت: ستون is_admin (رفع باگ: قبلاً کسی که یک بار رمز غلط می‌زد ادمین حساب می‌شد)
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


def get_prompt():
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='grading_prompt'").fetchone()
    return row[0] if row else DEFAULT_PROMPT


# ============================================================
# State ها
# ============================================================
class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_test_title = State()
    waiting_for_segment_lang = State()
    waiting_for_segment_audio = State()
    waiting_for_segment_tip = State()
    waiting_for_broadcast_message = State()
    waiting_for_custom_report = State()
    waiting_for_prompt = State()


class UserTestStates(StatesGroup):
    taking_test = State()
    waiting_for_tip_ack = State()


def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 شروع آزمون"), KeyboardButton(text="🔙 منوی قبل")]],
        resize_keyboard=True,
    )


# ============================================================
# توابع کمکی
# ============================================================
async def download_bytes(file_id):
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    return buf.getvalue()


async def send_long(chat_id, text, reply_markup=None):
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [" "]
    for i, chunk in enumerate(chunks):
        await bot.send_message(chat_id, chunk, reply_markup=reply_markup if i == len(chunks) - 1 else None)


def is_admin_user(user_id):
    with db() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ? AND is_admin = 1 AND is_blocked = 0", (user_id,)).fetchone()
    return bool(row)


async def grade_with_gemini(test_title, test_type, segments, user_voices):
    parts = [types.Part.from_text(text=f"Test: {test_title} ({test_type}). Segments follow in order.")]
    for i, seg in enumerate(segments):
        direction = "English to Persian" if seg[2] == "en" else "Persian to English"
        src_mime = seg[4] if len(seg) > 4 and seg[4] else "audio/ogg"
        src_bytes = await download_bytes(seg[1])
        usr_bytes = await download_bytes(user_voices[i]["voice_file_id"])
        parts.append(types.Part.from_text(text=f"--- Segment {i + 1} ({direction}) --- ORIGINAL audio:"))
        parts.append(types.Part.from_bytes(data=src_bytes, mime_type=src_mime))
        parts.append(types.Part.from_text(text=f"Segment {i + 1} CANDIDATE's interpretation:"))
        parts.append(types.Part.from_bytes(data=usr_bytes, mime_type="audio/ogg"))
    parts.append(types.Part.from_text(text="Now write the full report according to the instructions."))

    resp = await gemini.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=parts,
        config=types.GenerateContentConfig(system_instruction=get_prompt(), temperature=0.2),
    )
    return resp.text


async def grade_and_notify(admin_ids, user_id, username, test_title, test_type, segments, user_voices):
    try:
        report = await grade_with_gemini(test_title, test_type, segments, user_voices)
        if not report:
            raise RuntimeError("Gemini returned an empty response")
    except Exception as err:
        logging.error(f"Gemini grading failed: {err}")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ ارسال کارنامه سفارشی", callback_data=f"custom_rep_{user_id}")
        ]])
        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ تصحیح خودکار برای @{username} ناموفق بود:\n{str(err)[:800]}\n\nخودتان با دکمه زیر کارنامه بفرستید.",
                    reply_markup=kb,
                )
            except Exception as e2:
                logging.error(f"notify admin {admin_id}: {e2}")
        return

    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_reports (user_id, username, test_title, report) VALUES (?, ?, ?, ?)",
            (user_id, username, test_title, report),
        )
        pid = cur.lastrowid
        conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تایید و ارسال به کاربر", callback_data=f"approve_{pid}")],
        [InlineKeyboardButton(text="✏️ ارسال متن سفارشی (ویرایش‌شده)", callback_data=f"custom_rep_{user_id}")],
        [InlineKeyboardButton(text="❌ رد کردن", callback_data=f"reject_{user_id}")],
    ])
    for admin_id in admin_ids:
        try:
            await send_long(
                admin_id,
                f"🤖 کارنامه پیشنهادی هوش مصنوعی\n👤 @{username} (id: {user_id})\n📚 {test_title}\n\n{report}",
                reply_markup=kb,
            )
        except Exception as err:
            logging.error(f"send report to admin {admin_id}: {err}")


# ============================================================
# بخش کاربر
# ============================================================
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
    )
    await message.answer("👇 منوی دسترسی سریع:", reply_markup=get_main_reply_keyboard())


@dp.callback_query(F.data.startswith("start_test_"))
async def user_start_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])

    with db() as conn:
        test_row = conn.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,)).fetchone()
        segments = conn.execute(
            "SELECT id, file_id, segment_lang, segment_tip, file_mime FROM segments WHERE test_id = ? ORDER BY segment_index ASC",
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
    await callback.answer()


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

    test_title = data.get("test_title")
    test_type = data.get("test_type")
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    await message.answer(
        "✅ آزمون شما به پایان رسید.\n\n"
        "جواب‌های شما به سیستم تصحیح توسط هوش مصنوعی و استاد ارسال شد و بعد از تایید تا چند ساعت آینده برای شما ارسال خواهد شد. "
        "لطفا تا آن زمان ربات را متوقف نکنید.",
        reply_markup=get_main_reply_keyboard(),
    )

    with db() as conn:
        admin_ids = [r[0] for r in conn.execute("SELECT user_id FROM admins WHERE is_admin = 1 AND is_blocked = 0").fetchall()]

    # ارسال ویس‌های کاربر به ادمین برای شنیدن مستقیم
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, f"📥 آزمون جدید از @{username} ({test_title}) دریافت شد. تصحیح هوش مصنوعی در حال انجام است...")
            for item in user_voices:
                await bot.send_voice(admin_id, voice=item["voice_file_id"], caption=f"ویس سگمنت {item['segment_index']} - @{username}")
        except Exception as err:
            logging.error(f"Failed to send voices to admin {admin_id}: {err}")

    # تصحیح در پس‌زمینه تا ربات برای بقیه کاربران قفل نشود
    task = asyncio.create_task(
        grade_and_notify(admin_ids, user_id, username, test_title, test_type, segments, user_voices)
    )
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    await state.clear()


@dp.message(UserTestStates.taking_test)
async def remind_send_voice(message: Message):
    await message.answer("🎙 لطفاً پاسخ خود را به صورت ویس (voice) ارسال کنید.")


# ============================================================
# کارنامه: تایید / سفارشی / رد
# ============================================================
@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve_report(callback: CallbackQuery):
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


# ============================================================
# بخش ادمین
# ============================================================
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


async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ساخت آزمون جدید", callback_data="admin_create_manual")],
        [InlineKeyboardButton(text="✏️ مدیریت و حذف آزمون‌ها/سگمنت‌ها", callback_data="admin_manage_tests")],
        [InlineKeyboardButton(text="🧠 ویرایش پرامپت تصحیح", callback_data="admin_edit_prompt")],
        [InlineKeyboardButton(text="📢 ارسال نوتیفیکیشن", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 مشاهده نتایج", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="🗑 پاکسازی کل دیتابیس", callback_data="admin_clear_db")],
    ])
    await message.answer("پنل مدیریت پیشرفته:", reply_markup=keyboard)


async def require_admin(callback: CallbackQuery):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return False
    return True


# ---- ویرایش پرامپت ----
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


def store_prompt(text):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('grading_prompt', ?)", (text,))
        conn.commit()


async def review_prompt(prompt_text):
    """Gemini پرامپت را بررسی می‌کند. خروجی: متنی که خط اول آن OK یا PROBLEM است."""
    instruction = (
        "You review grading prompts for a Telegram bot that sends interpreting-exam AUDIO "
        "(original audio + candidate's spoken answer) directly to an AI grader. "
        "Check the prompt: (1) it makes sense and is usable as a system instruction, "
        "(2) it tells the model to evaluate from the audio itself (delivery, hesitations, tone, accuracy), "
        "(3) it asks for a clear report format, (4) it has no contradictions, "
        "(5) it does not encourage inventing content that was not heard. "
        "Reply in Persian, plain text only, max 8 short lines. "
        "The FIRST line must be exactly OK if the prompt is usable, or exactly PROBLEM if there are real issues. "
        "After the first line: for OK give one short sentence summary; for PROBLEM list each issue and how to fix it."
    )
    resp = await gemini.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=[f"Prompt to review:\n\n{prompt_text}"],
        config=types.GenerateContentConfig(system_instruction=instruction, temperature=0.1),
    )
    return (resp.text or "").strip()


@dp.message(AdminStates.waiting_for_prompt, F.text)
async def save_prompt(message: Message, state: FSMContext):
    new_prompt = message.text.strip()

    if new_prompt.lower() == "cancel":
        await state.clear()
        await message.answer("لغو شد. پرامپت قبلی بدون تغییر ماند.")
        return

    if new_prompt.lower() == "default":
        with db() as conn:
            conn.execute("DELETE FROM settings WHERE key='grading_prompt'")
            conn.commit()
        await state.clear()
        await message.answer("✅ پرامپت به حالت پیش‌فرض برگشت.")
        return

    if len(new_prompt) < 30:
        await message.answer("⚠️ پرامپت خیلی کوتاه است (کمتر از ۳۰ کاراکتر). دوباره بفرستید، یا برای لغو بنویسید: cancel")
        return

    status = await message.answer("⏳ در حال ذخیره و تست اتصال به Gemini...")

    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('grading_prompt', ?)", (new_prompt,))
        conn.commit()
    await state.clear()

    # بررسی اینکه واقعاً ذخیره شده
    saved = get_prompt()
    saved_ok = (saved == new_prompt)

    # تست سریع Gemini با همین پرامپت (برای اطمینان از سالم بودن API key و مدل)
    try:
        await asyncio.wait_for(
            gemini.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents="Connection test: reply with the single word OK.",
                config=types.GenerateContentConfig(system_instruction=new_prompt, temperature=0.2, max_output_tokens=50),
            ),
            timeout=40,
        )
        test_line = f"✅ تست Gemini موفق بود (کلید API و مدل «{GEMINI_MODEL}» درست کار می‌کنند)."
    except Exception as e:
        test_line = f"⚠️ پرامپت ذخیره شد ولی تست Gemini خطا داد:\n{str(e)[:600]}"

    warn = ""
    if len(new_prompt) >= 3900:
        warn = "\n⚠️ پرامپت شما خیلی بلند است. تلگرام پیام‌های بالای ۴۰۹۶ کاراکتر را تکه‌تکه می‌کند و ممکن است فقط بخش اول ذخیره شده باشد. بهتر است کوتاه‌ترش کنید."

    preview = new_prompt[:300] + ("..." if len(new_prompt) > 300 else "")
    await status.edit_text(
        f"{'✅ پرامپت ذخیره شد' if saved_ok else '❌ ذخیره ناموفق بود'} ({len(new_prompt)} کاراکتر)\n\n"
        f"{test_line}{warn}\n\nشروع پرامپت:\n{preview}"
    )


@dp.message(AdminStates.waiting_for_prompt)
async def save_prompt_invalid(message: Message):
    await message.answer("⚠️ فقط متن بفرستید (نه فایل/عکس/ویس). برای لغو بنویسید: cancel")


# ---- پیام همگانی ----
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


# ---- ساخت آزمون ----
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
    data = await state.get_data()
    test_id = data.get("test_id")
    file_id = data.get("file_id")
    file_mime = data.get("file_mime")
    segment_tip = message.text
    seg_index = data.get("segment_index")
    segment_lang = data.get("segment_lang")

    with db() as conn:
        conn.execute(
            "INSERT INTO segments (test_id, file_id, segment_index, segment_lang, segment_tip, file_mime) VALUES (?, ?, ?, ?, ?, ?)",
            (test_id, file_id, seg_index, segment_lang, segment_tip, file_mime),
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


# ---- مدیریت آزمون‌ها ----
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
        segments = conn.execute("SELECT id, segment_index, segment_lang FROM segments WHERE test_id = ? ORDER BY segment_index", (test_id,)).fetchall()

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


@dp.callback_query(F.data == "admin_back_to_main")
async def admin_back_to_main(callback: CallbackQuery):
    if not await require_admin(callback):
        return
    await show_admin_panel(callback.message)
    await callback.answer()


# ---- نتایج ----
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


# ---- پاکسازی دیتابیس (با تایید) ----
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
        conn.execute("DELETE FROM segments")
        conn.execute("DELETE FROM tests")
        conn.execute("DELETE FROM users")
        conn.commit()
    await callback.message.answer("پاکسازی کل اطلاعات انجام شد.")
    await callback.answer()


# ============================================================
# اجرا
# ============================================================
async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info("Flask web server started.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
