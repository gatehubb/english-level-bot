import os
import asyncio
import logging
import sqlite3
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
    # جدول ذخیره کاربران برای ارسال نوتیفیکیشن همگانی
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS tests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, test_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, file_id TEXT, answer_key TEXT, segment_index INTEGER, segment_lang TEXT, speaker_info TEXT, segment_tip TEXT, FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, test_title TEXT, analysis TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

init_db()

class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_test_title = State()
    waiting_for_segment_lang = State()
    waiting_for_segment_audio = State()
    waiting_for_segment_tip = State()
    waiting_for_segment_key = State()
    waiting_for_broadcast_message = State() # استیت جدید برای ارسال پیام همگانی
    editing_answer_key = State()
    editing_audio = State()
    editing_tip = State()
    editing_test_title = State()

class UserTestStates(StatesGroup):
    taking_test = State()
    waiting_for_tip_ack = State()

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    user_id = message.from_user.id
    username = message.from_user.username or ""
    
    # ذخیره خودکار کاربر در دیتابیس جهت ارسال نوتیفیکیشن‌های بعدی
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    
    cursor.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,))
    admin_row = cursor.fetchone()
    conn.commit()
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
    
    cursor.execute("SELECT id, file_id, answer_key, segment_lang, speaker_info, segment_tip FROM segments WHERE test_id = ? ORDER BY segment_index ASC", (test_id,))
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

    await show_segment_tip(callback.message, state)
    await callback.answer()

async def show_segment_tip(message: Message, state: FSMContext):
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    seg = segments[curr_idx]
    
    tip_text = seg[5] if seg[5] else "• حتماً کاغذ و قلم همراه داشته باشید تا نکات و اعداد کلیدی را یادداشت کنید.\n• انتقال دقیق مفهوم، اعداد و اسامی اهمیت بالایی دارد."
    
    await state.set_state(UserTestStates.waiting_for_tip_ack)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، متوجه شدم و آماده‌ام", callback_data="ack_tip_and_play")]
    ])
    
    await message.answer(f"💡 **راهنمای سگمنت {curr_idx + 1} از {len(segments)}:**\n\n{tip_text}", reply_markup=keyboard)

@dp.callback_query(UserTestStates.waiting_for_tip_ack, F.data == "ack_tip_and_play")
async def ack_tip_and_play(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserTestStates.taking_test)
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    seg = segments[curr_idx]
    
    lang_dir = "انگلیسی به فارسی 🇬🇧 ➡️ 🇮🇷" if seg[3] == "en" else "فارسی به انگلیسی 🇮🇷 ➡️ 🇬🇧"
    
    await callback.message.answer(f"🎧 **سگمنت {curr_idx + 1}** ({lang_dir})\nگوينده: {seg[4] if seg[4] else 'گوینده اصلی'}\n\nبه فایل صوتی زیر گوش دهید و ترجمه خود را به صورت ویس ارسال کنید:")
    
    try:
        await callback.message.answer_voice(voice=seg[1])
    except:
        try:
            await callback.message.answer_audio(audio=seg[1])
        except:
            await callback.message.answer_document(document=seg[1])
            
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
            await message.answer("❌ رمز عبور ۳ بار به اشتباه وارد شد. شما بلاک شدید.")
        else:
            cursor.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked) VALUES (?, ?, 0)", (user_id, failed))
            conn.commit()
            conn.close()
            remaining = 3 - failed
            await message.answer(f"رمز عبور اشتباه است. {remaining} فرصت دیگر باقی مانده است.")

async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ساخت آزمون جدید (گام به گام)", callback_data="admin_create_manual")],
        [InlineKeyboardButton(text="✏️ مدیریت، ویرایش آزمون‌ها و سگمنت‌ها", callback_data="admin_manage_tests")],
        [InlineKeyboardButton(text="📢 ارسال نوتیفیکیشن همگانی", callback_data="admin_broadcast")], # دکمه جدید نوتیفیکیشن
        [InlineKeyboardButton(text="مشاهده نتایج کاربران", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="پاکسازی کل اطلاعات", callback_data="admin_clear_db")]
    ])
    await message.answer("پنل مدیریت پیشرفته\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=keyboard)

# شروع فرآیند ارسال نوتیفیکیشن همگانی
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.message.answer("📢 لطفاً متن پیامی که می‌خواهید به عنوان نوتیفیکیشن یا آپدیت به تمامی کاربران ارسال شود را بفرستید:")
    await callback.answer()

# دریافت متن پیام و ارسال به همه کاربران
@dp.message(AdminStates.waiting_for_broadcast_message, F.text)
async def process_broadcast_message(message: Message, state: FSMContext):
    broadcast_text = message.text
    await state.clear()

    status_msg = await message.answer("⏳ در حال ارسال نوتیفیکیشن به تمامی کاربران...")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    success_count = 0
    fail_count = 0

    for user in users:
        u_id = user[0]
        try:
            await bot.send_message(chat_id=u_id, text=f"🔔 **اطلاعیه جدید / آپدیت ربات:**\n\n{broadcast_text}")
            success_count += 1
            await asyncio.sleep(0.05) # جلوگیری از ریت لیمیت تلگرام
        except Exception:
            fail_count += 1

    await status_msg.edit_text(f"✅ ارسال نوتیفیکیشن همگانی به اتمام رسید!\n\n• ارسال موفق: {success_count}\n• ناموفق (کاربر ربات را بلاک کرده): {fail_count}")
    await show_admin_panel(message)

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

@dp.message(AdminStates.waiting_for_test_title, F.text)
async def process_test_title(message: Message, state: FSMContext):
    title = message.text
    data = await state.get_data()
    seg_index = data.get("segment_index")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO tests (title, test_type) VALUES (?, ?)", (title, data.get("test_type")))
        conn.commit()
        test_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        await message.answer("این عنوان قبلاً ثبت شده است. نام دیگری وارد کنید:")
        return
    conn.close()

    await state.update_data(test_id=test_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="انگلیسی 🇬🇧", callback_data="lang_en"), InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await message.answer(f"آزمون «{title}» ایجاد شد.\n\nزبان سگمنت شماره {seg_index} را انتخاب کنید:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def process_segment_lang(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await state.update_data(segment_lang=lang)
    lang_name = "انگلیسی 🇬🇧" if lang == "en" else "فارسی 🇮🇷"
    await callback.message.answer(f"زبان انتخاب شده: {lang_name}\n\nحالا فایل صوتی این سگمنت را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_segment_audio)
    await callback.answer()

@dp.message(AdminStates.waiting_for_segment_audio, F.voice | F.audio | F.document)
async def process_segment_audio(message: Message, state: FSMContext):
    file_id = message.voice.file_id if message.voice else (message.audio.file_id if message.audio else message.document.file_id)
    await state.update_data(file_id=file_id)
    await message.answer("متن راهنما و توصیه‌های این سگمنت را وارد کنید (به کاربر قبل از پخش صوت نمایش داده می‌شود):")
    await state.set_state(AdminStates.waiting_for_segment_tip)

@dp.message(AdminStates.waiting_for_segment_tip, F.text)
async def process_segment_tip(message: Message, state: FSMContext):
    await state.update_data(segment_tip=message.text)
    await message.answer("متن کلید/پاسخ (Answer Key) این سگمنت را وارد کنید:")
    await state.set_state(AdminStates.waiting_for_segment_key)

@dp.message(AdminStates.waiting_for_segment_key, F.text)
async def process_segment_key(message: Message, state: FSMContext):
    data = await state.get_data()
    test_id = data.get("test_id")
    file_id = data.get("file_id")
    segment_tip = data.get("segment_tip")
    answer_key = message.text
    seg_index = data.get("segment_index")
    segment_lang = data.get("segment_lang")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO segments (test_id, file_id, answer_key, segment_index, segment_lang, segment_tip) VALUES (?, ?, ?, ?, ?, ?)", (test_id, file_id, answer_key, seg_index, segment_lang, segment_tip))
    conn.commit()
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="افزودن سگمنت بعدی", callback_data="admin_add_more_segment")],
        [InlineKeyboardButton(text="پایان بارگذاری", callback_data="admin_finish_test")]
    ])
    await message.answer(f"سگمنت شماره {seg_index} ثبت شد. مایلید چه کاری انجام دهید؟", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_add_more_segment")
async def admin_add_more_segment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_index = data.get("segment_index") + 1
    await state.update_data(segment_index=new_index)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="انگلیسی 🇬🇧", callback_data="lang_en"), InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await callback.message.answer(f"زبان سگمنت شماره {new_index} را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_finish_test")
async def admin_finish_test(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("بارگذاری آزمون با موفقیت به اتمام رسید!")
    await show_admin_panel(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "admin_manage_tests")
async def admin_manage_tests(callback: CallbackQuery, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, test_type FROM tests")
    tests = cursor.fetchall()
    conn.close()

    if not tests:
        await callback.message.edit_text("هیچ آزمونی برای مدیریت وجود ندارد.")
        await show_admin_panel(callback.message)
        await callback.answer()
        return

    buttons = []
    for t_id, t_title, t_type in tests:
        buttons.append([InlineKeyboardButton(text=f"📂 {t_title} ({t_type})", callback_data=f"manage_test_{t_id}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin_back_to_main")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("لطفاً آزمون مورد نظر جهت ویرایش را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("manage_test_"))
async def manage_single_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,))
    test_title, test_type = cursor.fetchone()

    cursor.execute("SELECT id, segment_index, segment_lang, answer_key FROM segments WHERE test_id = ? ORDER BY segment_index ASC", (test_id,))
    segments = cursor.fetchall()
    conn.close()

    buttons = [
        [InlineKeyboardButton(text="✏️ ویرایش نام آزمون", callback_data=f"edit_test_title_{test_id}")]
    ]
    for seg_id, index, lang, key in segments:
        buttons.append([InlineKeyboardButton(text=f"سگمنت {index} ({lang})", callback_data=f"edit_seg_{seg_id}")])

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_manage_tests")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(f"مدیریت آزمون: **{test_title}** ({test_type})\n\nسگمنت مورد نظر را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_seg_"))
async def edit_segment_options(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT segment_index, segment_lang, answer_key, test_id, segment_tip FROM segments WHERE id = ?", (seg_id,))
    seg = cursor.fetchone()
    conn.close()

    index, lang, answer_key, test_id, tip = seg
    await state.update_data(editing_seg_id=seg_id, test_id=test_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش متن پاسخ", callback_data=f"seg_action_key_{seg_id}")],
        [InlineKeyboardButton(text="💡 ویرایش متن راهنما (Tip)", callback_data=f"seg_action_tip_{seg_id}")],
        [InlineKeyboardButton(text="🗑 حذف سگمنت", callback_data=f"seg_action_del_{seg_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"manage_test_{test_id}")]
    ])

    await callback.message.edit_text(
        f"مدیریت سگمنت {index}:\n\n**راهنما:** {tip}\n\n**پاسخ:** {answer_key}",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("seg_action_key_"))
async def action_edit_key(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.editing_answer_key)
    await callback.message.answer("متن جدید پاسخ را ارسال کنید:")
    await callback.answer()

@dp.message(AdminStates.editing_answer_key, F.text)
async def save_edited_key(message: Message, state: FSMContext):
    data = await state.get_data()
    seg_id = data.get("editing_seg_id")
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE segments SET answer_key = ? WHERE id = ?", (message.text, seg_id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ پاسخ با موفقیت ویرایش شد.")
    await show_admin_panel(message)

@dp.callback_query(F.data.startswith("seg_action_tip_"))
async def action_edit_tip(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.editing_tip)
    await callback.message.answer("متن جدید راهنما (Tip) را ارسال کنید:")
    await callback.answer()

@dp.message(AdminStates.editing_tip, F.text)
async def save_edited_tip(message: Message, state: FSMContext):
    data = await state.get_data()
    seg_id = data.get("editing_seg_id")
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE segments SET segment_tip = ? WHERE id = ?", (message.text, seg_id))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ راهنمای سگمنت با موفقیت ویرایش شد.")
    await show_admin_panel(message)

@dp.callback_query(F.data.startswith("seg_action_del_"))
async def action_delete_segment(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[3])
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT test_id FROM segments WHERE id = ?", (seg_id,))
    test_id = cursor.fetchone()[0]
    cursor.execute("DELETE FROM segments WHERE id = ?", (seg_id,))
    conn.commit()
    conn.close()
    await callback.answer("سگمنت حذف شد.", show_alert=True)
    callback.data = f"manage_test_{test_id}"
    await manage_single_test(callback, state)

@dp.callback_query(F.data == "admin_back_to_main")
async def admin_back_to_main(callback: CallbackQuery, state: FSMContext):
    await show_admin_panel(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "admin_view_results")
async def admin_view_results(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, test_title, analysis, date FROM results ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text("هیچ نتیجه‌ای ثبت نشده است.")
        return
    text = "۵ تحلیل اخیر آزمون‌ها:\n\n"
    for row in rows:
        text += f"کاربر: {row[0] or 'بدون نام'}\nآزمون: {row[1]}\nزمان: {row[3]}\n{row[2][:150]}...\n-------------------\n"
    await callback.message.edit_text(text)

@dp.callback_query(F.data == "admin_clear_db")
async def admin_clear_db(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM results")
    cursor.execute("DELETE FROM segments")
    cursor.execute("DELETE FROM tests")
    cursor.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    await callback.message.answer("تمام اطلاعات پاک شد.")
    await callback.answer()

@dp.message(UserTestStates.taking_test, F.voice)
async def handle_user_voice_test(message: Message, state: FSMContext):
    conn_chk = sqlite3.connect("bot_database.db")
    cur_chk = conn_chk.cursor()
    cur_chk.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (message.from_user.id,))
    block_row = cur_chk.fetchone()
    conn_chk.close()
    if block_row and block_row[0] == 1:
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return

    data = await state.get_data()
    test_title = data.get("test_title")
    test_type = data.get("test_type")
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    session_results = data.get("session_results")

    seg_tuple = segments[curr_idx]
    answer_key, segment_lang = seg_tuple[2], seg_tuple[3]

    status_msg = await message.answer(f"در حال پردازش سگمنت {curr_idx + 1}...")
    file_path = f"voice_{message.from_user.id}.ogg"

    try:
        if not GEMINI_API_KEY:
            await status_msg.edit_text("کلید GEMINI_API_KEY تنظیم نشده است.")
            return

        system_prompt = f"""
تو یک سیستم ارزیابی حرفه‌ای آزمون ترجمه شفاهی NAATI CCL هستی.
اگر صوت خالی، بی‌صدا یا دارای نویز شدید بود، با این عبارت شروع کن:
`INVALID_AUDIO: [دلیل مختصر]`

کلید مرجع:
"{answer_key}"

نوع آزمون: {test_type}
شماره سگمنت: {curr_idx + 1}
زبان فایل صوتی مرجع: {segment_lang}
"""

        if test_type == "placement":
            system_prompt += """
ارزیابی خود را انجام بده و خروجی را دقیقاً با این فرمت ارسال کن:
نتیجه ارزیابی سگمنت 
متن مرجع: [متن]
ترجمه شما: [ترجمه]
ارزیابی عملکرد (Assessment Criteria)
دقت (Accuracy): [توضیح]
کیفیت زبان (Language): [توضیح]
کیفیت ارائه (Delivery): [توضیح]
نمرات شما:
- دقت (Accuracy): [عدد]%
- زبان (Language): [عدد]%
- ارائه (Delivery): [عدد]%
- نمره کل (Overall): [عدد]%
"""
        else:
            system_prompt += """
خروجی را دقیقاً با این فرمت ارسال کن:
نتیجه ارزیابی سگمنت {curr_idx + 1}
متن مرجع: {answer_key}
ترجمه شما: [ترجمه]
ارزیابی عملکرد (Assessment Criteria)
دقت (Accuracy): [توضیح]
کیفیت زبان (Language): [توضیح]
کیفیت ارائه (Delivery): [توضیح]
نسخه درست و بهبود یافته: [نسخه]
نمرات شما:
- دقت (Accuracy): [عدد]%
- زبان (Language): [عدد]%
- ارائه (Delivery): [عدد]%
- نمره کل (Overall): [عدد]%
"""

        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, file_path)
        
        audio_file = ai_client.files.upload(file=file_path)
        
        response = None
        for attempt in range(3):
            try:
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[system_prompt, audio_file]
                )
                break
            except Exception as api_err:
                if attempt == 2:
                    raise api_err
                await asyncio.sleep(3)

        analysis_result = response.text.strip()
        ai_client.files.delete(name=audio_file.name)

        if analysis_result.startswith("INVALID_AUDIO:"):
            error_details = analysis_result.replace("INVALID_AUDIO:", "").strip()
            await status_msg.edit_text(f"اخطار در بررسی فایل صوتی:\n\n{error_details}\n\nلطفاً همین سگمنت را دوباره ارسال کنید.")
            return

        await status_msg.delete()
        session_results.append(analysis_result)

        next_idx = curr_idx + 1
        if next_idx < len(segments):
            await state.update_data(current_index=next_idx, session_results=session_results)
            await show_segment_tip(message, state)
        else:
            acc_list, lang_list, del_list, overall_list = [], [], [], []
            
            for res in session_results:
                acc_match = re.search(r"دقت\s*\(Accuracy\):\s*(\d+)%", res)
                lang_match = re.search(r"زبان\s*\(Language\):\s*(\d+)%", res)
                del_match = re.search(r"ارائه\s*\(Delivery\):\s*(\d+)%", res)
                ov_match = re.search(r"نمره کل\s*\(Overall\):\s*(\d+)%", res)
                
                if acc_match: acc_list.append(int(acc_match.group(1)))
                if lang_match: lang_list.append(int(lang_match.group(1)))
                if del_match: del_list.append(int(del_match.group(1)))
                if ov_match: overall_list.append(int(ov_match.group(1)))

            avg_acc = sum(acc_list) // len(acc_list) if acc_list else 0
            avg_lang = sum(lang_list) // len(lang_list) if lang_list else 0
            avg_del = sum(del_list) // len(del_list) if del_list else 0
            avg_overall = sum(overall_list) // len(overall_list) if overall_list else 0

            if test_type == "placement":
                readiness = avg_overall
                status_text = "در مسیر پیشرفت" if readiness >= 60 else "نیاز به تلاش بیشتر"
                
                placement_report = f"""
آزمون تعیین سطح سنجش آمادگی NAATI CCL 📋

نتیجه کلی ارزیابی سطح شما 📊
درصد آمادگی فعلی: {readiness}% 📈
سطح: {status_text} 🎯

۱. دقت (Accuracy): {avg_acc}% 🎯
۲. زبان (Language): {avg_lang}% 🗣
۳. ارائه (Delivery): {avg_del}% ⏱
۴. نمره کل (Overall): {avg_overall}% 📝

🔒 برای دریافت برنامه تمرینی، در کانال رسمی ما عضو شوید:
"""
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)],
                    [InlineKeyboardButton(text="بررسی عضویت", callback_data="check_channel_membership")]
                ])
                await state.update_data(session_results=session_results)
                await message.answer(placement_report, reply_markup=keyboard)
            else:
                full_report_text = f"کارنامه نهایی آزمون ماک: {test_title}\n\nدقت: {avg_acc}%\nزبان: {avg_lang}%\nارائه: {avg_del}%\nنمره کل: {avg_overall}%"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="مشاهده تحلیل جزئیات", callback_data="show_detailed_report")]
                ])
                await state.update_data(session_results=session_results)
                await message.answer(full_report_text, reply_markup=keyboard)

    except Exception as e:
        await status_msg.edit_text(f"خطا در تحلیل:\n{str(e)}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@dp.callback_query(F.data == "check_channel_membership")
async def check_channel_membership(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            await callback.message.answer("عضویت شما تأیید شد!")
            await state.clear()
        else:
            await callback.answer("شما هنوز در کانال عضو نشده‌اید!", show_alert=True)
    except Exception as e:
        await callback.answer("خطا در بررسی عضویت.", show_alert=True)

@dp.callback_query(F.data == "show_detailed_report")
async def show_detailed_report(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    session_results = data.get("session_results", [])
    for res in session_results:
        await callback.message.answer(res)
    await state.clear()
    await callback.answer()

async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info("Flask web server started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
