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

# --- تنظیمات دیتابیس SQLite برای ذخیره نتایج و وضعیت ادمین‌ها ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            analysis TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

init_db()

# FSM برای ورود به پنل ادمین
class AdminStates(StatesGroup):
    waiting_for_password = State()

SYSTEM_PROMPT = """
تو یک استاد و ارزیابی‌کننده حرفه‌ای زبان انگلیسی هستی.
کاربر یک ویس فرستاده که در آن جمله یا جملاتی را به انگلیسی خوانده یا ترجمه کرده است.

صوت ارسال‌شده را به دقت بشنو و گزارش تحلیلی کاملی به زبان فارسی شامل موارد زیر ارائه بده:
1. 📝 **متن تشخیص داده شده (Transcription):** دقیقاً چه کلماتی را بیان کرده است.
2. 🎯 **ارزیابی تلفظ و روانی (Pronunciation & Fluency):** آیا مکث یا تردید (Hesitation/Um/Uh) داشته؟ تلفظ کلمات چطور بود؟
3. 🛠 **ارزیابی گرامری و ساختار (Grammar & Vocabulary):** ایرادات گرامری و پیشنهاد اصلاحی.
4. 📊 **تخمین سطح CEFR:** (مثلاً A2, B1, B2 یا C1).
5. 💡 **پیشنهاد برای بهبود:** یک یا دو نکته کلیدی برای بهبود گفتار.

پاسخ را بسیار شکیل، مرتب و با ایموجی‌های مناسب قالب‌بندی کن.
"""

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "سلام! 👋 به ربات تعیین سطح انگلیسی خوش آمدید.\n\n"
        "لطفاً یک ویس (Voice) به زبان انگلیسی بفرستید تا آن را از نظر تلفظ، گرامر، مکث‌ها و سطح CEFR تحلیل کنم.\n\n"
        "🔑 برای ورود به پنل مدیریت دستور /admin را وارد کنید."
    )

# --- دستورات پنل ادمین ---
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
        [InlineKeyboardButton(text="📊 مشاهده آخرین نتایج کاربران", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="🗑 پاکسازی تاریخچه", callback_data="admin_clear_db")]
    ])
    await message.answer("⚙️ **خوش آمدید به پنل مدیریت ربات**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=keyboard)

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

# --- تحلیل ویس کاربر و ذخیره در دیتابیس ---
@dp.message(F.voice)
async def handle_voice(message: Message):
    status_msg = await message.answer("📥 در حال دریافت و تحلیل فایل صوتی شما...")
    file_path = f"voice_{message.from_user.id}.ogg"
    
    try:
        if not GEMINI_API_KEY:
            await status_msg.edit_text("❌ کلید GEMINI_API_KEY روی سرور تنظیم نشده است.")
            return

        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, file_path)
        
        audio_file = ai_client.files.upload(file=file_path)
        
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[SYSTEM_PROMPT, audio_file]
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
    logging.info("Starting bot polling with Admin Panel...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
