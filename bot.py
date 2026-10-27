import os
import asyncio
import logging
import sqlite3
import zipfile
import shutil
import re
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from google import genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = "8834529401:AAFEVB8Xyzob8PVkN-V2Qn0MfZ41NscWkAw"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_PASSWORD = "2377451"
CHANNEL_USERNAME = "@YourChannelUsername"
CHANNEL_LINK = "https://t.me/YourChannelUsername"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

app = Flask(__name__)

@app.route("/")
def health_check():
    return "Bot is running alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, failed_attempts INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS tests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, test_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, file_id TEXT, answer_key TEXT, segment_index INTEGER, segment_lang TEXT, speaker_info TEXT, FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, test_title TEXT, analysis TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

init_db()

class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_test_title = State()
    waiting_for_segment_lang = State()
    waiting_for_segment_audio = State()
    waiting_for_segment_key = State()
    waiting_for_zip_file = State()
    waiting_for_zip_test_type = State()
    editing_answer_key = State()
    editing_audio = State()
    editing_test_title = State()

class UserTestStates(StatesGroup):
    taking_test = State()

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (message.from_user.id,))
    admin_row = cursor.fetchone()
    conn.close()
    
    if admin_row and admin_row[0] == 1:
        await message.answer("❌ دسترسی شما به دلیل ورود ناموفق متوالی به پنل مدیریت مسدود شده است.")
        return

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, test_type FROM tests")
    tests = cursor.fetchall()
    conn.close()

    if not tests:
        await message.answer("سلام! به ربات تعیین سطح و آزمون‌های ماک خوش آمدید.\n\nدر حال حاضر هیچ آزمونی فعال نشده است.")
        return

    keyboard_buttons = []
    for t_id, t_title, t_type in tests:
        type_label = "🎯 تعیین سطح" if t_type == "placement" else "📝 ماک کامل"
        keyboard_buttons.append([InlineKeyboardButton(text=f"{type_label}: {t_title}", callback_data=f"start_test_{t_id}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("سلام! به بخش آزمون‌های ترجمه شفاهی خوش آمدید.\n\nلطفاً یکی از آزمون‌های زیر را انتخاب کنید:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("start_test_"))
async def user_start_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,))
    test_row = cursor.fetchone()
    
    cursor.execute("SELECT id, file_id, answer_key, segment_lang, speaker_info FROM segments WHERE test_id = ? ORDER BY segment_index ASC", (test_id,))
    segments = cursor.fetchall()
    conn.close()

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
        session_results=[]
    )

    first_seg = segments[0]
    lang_dir = "انگلیسی به فارسی 🇬🇧 ➡️ 🇮🇷" if first_seg[3] == "en" else "فارسی به انگلیسی 🇮🇷 ➡️ 🇬🇧"
    
    tip_text = (
        f"💡 **نکته و توصیه آزمون:**\n"
        f"• حتماً کاغذ و قلم همراه داشته باشید تا نکات و اعداد کلیدی را یادداشت کنید.\n"
        f"• نیازی نیست تک‌تک کلمات را موبه‌مو ترجمه کنید؛ انتقال مفهوم دقیق، اعداد و اسامی اهمیت اصلی را دارد.\n\n"
        f"🎧 **سگمنت ۱ از {len(segments)}** ({lang_dir})\n"
        f"گوينده: {first_seg[4] if first_seg[4] else 'گوینده اصلی'}\n\n"
        f"به فایل صوتی زیر گوش دهید و ترجمه خود را به صورت ویس ارسال کنید:"
    )
    await callback.message.answer(tip_text)
    
    try:
        await callback.message.answer_voice(voice=first_seg[1])
    except:
        try:
            await callback.message.answer_audio(audio=first_seg[1])
        except:
            await callback.message.answer_document(document=first_seg[1])
            
    await callback.answer()

@dp.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (message.from_user.id,))
    row = cursor.fetchone()
    
    if row and row[0] == 1:
        conn.close()
        await message.answer("❌ دسترسی شما به دلیل ورود ناموفق مسدود شده است.")
        return
        
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ? AND is_blocked = 0", (message.from_user.id,))
    is_admin = cursor.fetchone()
    conn.close()

    if is_admin:
        await show_admin_panel(message)
    else:
        await message.answer("رمز عبور پنل ادمین را وارد کنید:")
        await state.set_state(AdminStates.waiting_for_password)

@dp.message(AdminStates.waiting_for_password)
async def process_admin_password(message: Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT failed_attempts FROM admins WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    failed = row[0] if row else 0

    if message.text == ADMIN_PASSWORD:
        cursor.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked) VALUES (?, 0, 0)", (user_id,))
        conn.commit()
        conn.close()
        
        await state.clear()
        await message.answer("ورود با موفقیت انجام شد!")
        await show_admin_panel(message)
    else:
        failed += 1
        if failed >= 3:
            cursor.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked) VALUES (?, ?, 1)", (user_id, failed))
            conn.commit()
            conn.close()
            await state.clear()
            await message.answer("❌ رمز عبور ۳ بار به اشتباه وارد شد. شما بلاک شدید و دیگر نمی‌توانید از خدمات ربات استفاده کنید.")
        else:
            cursor.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked) VALUES (?, ?, 0)", (user_id, failed))
            conn.commit()
            conn.close()
            remaining = 3 - failed
            await message.answer(f"رمز عبور اشتباه است. {remaining} فرصت دیگر باقی مانده است.")

async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ساخت آزمون دستی (گام به گام)", callback_data="admin_create_manual")],
        [InlineKeyboardButton(text="📥 آپلود آزمون با فایل زیپ (.zip)", callback_data="admin_upload_zip")],
        [InlineKeyboardButton(text="✏️ مدیریت، ویرایش آزمون‌ها و سگمنت‌ها", callback_data="admin_manage_tests")],
        [InlineKeyboardButton(text="مشاهده نتایج کاربران", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="پاکسازی کل اطلاعات", callback_data="admin_clear_db")]
    ])
    await message.answer("پنل مدیریت پیشرفته\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_create_manual")
async def admin_create_manual(callback: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="تعیین سطح", callback_data="type_placement"), InlineKeyboardButton(text="ماک کامل", callback_data="type_mock")]
    ])
    await callback.message.answer("نوع آزمون را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("type_"))
async def process_test_type(callback: CallbackQuery, state: FSMContext):
    t_type = callback.data.split("_")[1]
    await state.update_data(test_type=t_type, segment_index=1)
    await callback.message.answer("عنوان آزمون را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_test_title)
    await callback.answer()

@dp.callback_query(F.data == "admin_upload_zip")
async def admin_upload_zip(
