import os
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from google import genai
from aiohttp import web

# تنظیم لاگ‌ها
logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = "8834529401:AAFEVB8Xyzob8PVkN-V2Qn0MfZ41NscWkAw"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_PASSWORD = "2377451"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- دیتابیس SQLite ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    # جدول نتایج
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            analysis TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # جدول ادمین‌ها
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)
    # جدول سوالات (فایل صوتی + کلید سوال)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voice_file_id TEXT,
            answer_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# FSM وضعیت‌های ادمین
class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_question_voice = State()
    waiting_for_answer_key = State()

# کلید ادمین ثبت‌شده در حالت موقت FSM
class QuestionData:
    voice_file_id = None

# --- دستور /start ---
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    # دریافت آخرین سوال فعال از دیتابیس
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT voice_file_id, answer_key FROM questions ORDER BY id DESC LIMIT 1")
    active_question = cursor.fetchone()
    conn.close()

    if active_question:
        voice_file_id, answer_key = active_question
        await message.answer("🎧 **تست تعیین سطح انگلیسی**\n\nلطفاً ویس زیر را گوش دهید و ترجمه/تکرار آن را در قالب یک ویس ارسال کنید:")
        await message.answer_voice(voice=voice_file_id)
    else:
        await message.answer(
            "سلام! 👋 به ربات تعیین سطح انگلیسی خوش آمدید.\n\n"
            "لطفاً یک ویس (Voice) به زبان انگلیسی بفرستید تا آن را از نظر تلفظ، گرامر، مکث‌ها و سطح CEFR تحلیل کنم."
        )

# --- پنل ادمین و احراز هویت ---
@dp.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (message.from_user.id,))
    is_admin = cursor.fetchone()
    conn.close()

    if is_admin:
        await show_admin_panel(message)
    else:
        await message.answer("🔒 لطفاً رمز عبور پنل ادمین را وارد کنید:")
        await state.set_state(AdminStates.waiting_for_password)

@dp.message(AdminStates.waiting_for_password)
async def process_admin_password(message: Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (message.from_user.id,))
        conn.commit()
        conn.close()
        
        await state.clear()
        await message.answer("✅ ورود با موفقیت انجام شد!")
        await show_admin_panel(message)
    else:
        await message.answer("❌ رمز عبور اشتباه است. دوباره تلاش کنید یا با /start خارج شوید.")

async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن سوال جدید (ویس + کلید)", callback_data="admin_add_question")],
        [InlineKeyboardButton(text="📊 مشاهده آخرین نتایج کاربران", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="🗑 پاکسازی تاریخچه نتایج", callback_data="admin_clear_db")]
    ])
    await message.answer("⚙️ **پنل مدیریت ربات**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=keyboard)

# --- افزودن سوال جدید توسط ادمین ---
@dp.callback_query(F.data == "admin_add_question")
async def admin_add_question_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🎙 لطفاً **فایل صوتی سوال** را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_question_voice)
    await callback.answer()

@dp.message(AdminStates.waiting_for_question_voice, F.voice)
async def process_question_voice(message: Message, state: FSMContext):
    await state.update_data(voice_file_id=message.voice.file_id)
    await message.answer("✅ ویس سوال دریافت شد.\n\n📝 حالا **متن کلید/پاسخ صحیح** این سوال را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_answer_key)

@dp.message(AdminStates.waiting_for_answer_key, F.text)
async def process_answer_key(message: Message, state: FSMContext):
    user_data = await state.get_data()
    voice_file_id = user_data.get("voice_file_id")
    answer_key = message.text

    # ذخیره سوال جدید در دیتابیس
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO questions (voice_file_id, answer_key) VALUES (?, ?)",
        (voice_file_id, answer_key)
    )
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(f"🎉 **سوال جدید با موفقیت ذخیره و فعال شد!**\n\n🔑 **کلید ثبت‌شده:**\n{answer_key}")
    await show_admin_panel(message)

# --- مشاهده و پاکسازی نتایج ---
@dp.callback_query(F.data == "admin_view_results")
async def admin_view_results(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, analysis, date FROM results ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await callback.message.edit_text("📭 هیچ نتیجه‌ای تاکنون ثبت نشده است.")
        return

    text = "📊 **۵ تحلیل اخیر کاربران:**\n\n"
    for row in rows:
        uname = row[0] or "بدون نام"
        text += f"👤 کاربر: {uname}\n🕒 زمان: {row[2]}\n{row[1][:200]}...\n-------------------\n"
    
    await callback.message.edit_text(text)

@dp.callback_query(F.data == "admin_clear_db")
async def admin_clear_db(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM results")
    conn.commit()
    conn.close()
    await callback.message.answer("🗑 تمام تاریخچه نتایج پاک شد.")
    await callback.answer()

# --- دریافت ویس کاربر و تحلیل بر اساس کلید سوال ---
@dp.message(F.voice)
async def handle_voice(message: Message, state: FSMContext):
    # چک کردن اینکه در حالت FSM ادمین نباشد
    current_state = await state.get_state()
    if current_state in [AdminStates.waiting_for_question_voice.state, AdminStates.waiting_for_answer_key.state]:
        return

    status_msg = await message.answer("📥 در حال دریافت و تحلیل فایل صوتی شما...")
    file_path = f"voice_{message.from_user.id}.ogg"
    
    try:
        if not GEMINI_API_KEY:
            await status_msg.edit_text("❌ کلید GEMINI_API_KEY روی سرور تنظیم نشده است.")
            return

        # دریافت کلید آخرین سوال فعال
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT answer_key FROM questions ORDER BY id DESC LIMIT 1")
        active_question = cursor.fetchone()
        conn.close()

        answer_key = active_question[0] if active_question else "پاسخ مرجع خاصی تعریف نشده است؛ صوت کاربر را به‌صورت عمومی تحلیل کن."

        # تنظیم پرامپت پویا همراه با کلید سوال
        system_prompt = f"""
تو یک استاد و ارزیابی‌کننده حرفه‌ای زبان انگلیسی هستی.
کاربر یک فایل صوتی به عنوان پاسخ فرستاده است.

🎯 **متن کلید/پاسخ صحیح مرجع:**
"{answer_key}"

صوت ارسال‌شده کاربر را بشنو و آن را با **متن کلید مرجع** مقایسه کرده و گزارش تحلیلی کاملی به زبان فارسی شامل موارد زیر ارائه بده:
1. 📝 **متن تشخیص داده شده (Transcription):** دقیقاً چه کلماتی توسط کاربر بیان شده است.
2. 🎯 **میزان تطابق با پاسخ صحیح (Accuracy & Relevance):** چقدر پاسخ کاربر با کلید مرجع مطابقت دارد؟
3. 🗣 **ارزیابی تلفظ و روانی (Pronunciation & Fluency):** بررسی مکث‌ها (Hesitation/Um/Uh)، روانی کلام و تلفظ صحیح کلمات.
4. 🛠 **ارزیابی گرامری و واژگان (Grammar & Vocabulary):** ایرادات گرامری و تفاوت‌های ساختاری با کلید مرجع.
5. 📊 **تخمین سطح CEFR:** (مثلاً A2, B1, B2 یا C1).
6. 💡 **پیشنهاد برای بهبود:** نکات کلیدی برای اصلاح و بهبود.

پاسخ را بسیار شکیل، مرتب و با ایموجی‌های مناسب قالب‌بندی کن.
"""

        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, file_path)
        
        audio_file = ai_client.files.upload(file=file_path)
        
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[system_prompt, audio_file]
        )
        
        analysis_result = response.text
        await status_msg.edit_text(analysis_result)
        ai_client.files.delete(name=audio_file.name)

        # ذخیره نتیجه در دیتابیس
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO results (user_id, username, analysis) VALUES (?, ?, ?)",
            (message.from_user.id, message.from_user.username, analysis_result)
        )
        conn.commit()
        conn.close()

    except Exception as e:
        await status_msg.edit_text(f"❌ خطایی در تحلیل رخ داد:\n{str(e)}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# وب‌سرویس برای زنده نگه داشتن روی Render
async def handle_health_check(request):
    return web.Response(text="Bot and Admin Panel are running alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    logging.info("Starting bot polling with Question Upload feature...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
