import os
import asyncio
import logging
import sqlite3
import re
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

try:
    import google.generativeai as genai
except ImportError:
    genai = None

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = "8834529401:AAFEVB8Xyzob8PVkN-V2Qn0MfZ41NscWkAw"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_PASSWORD = "2377451"

CHANNEL_USERNAME = "@YourChannelUsername"
CHANNEL_LINK = "https://t.me/YourChannelUsername"

if GEMINI_API_KEY and genai:
    genai.configure(api_key=GEMINI_API_KEY)

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
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS tests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, test_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER, file_id TEXT, answer_key TEXT, segment_index INTEGER, segment_lang TEXT, speaker_info TEXT, segment_tip TEXT, FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, test_title TEXT, analysis TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    
    cursor.execute("SELECT COUNT(*) FROM tests")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO tests (title, test_type) VALUES (?, ?)", ("آزمون پیش‌فرض تعیین سطح", "placement"))
        test_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO segments (test_id, file_id, answer_key, segment_index, segment_lang, speaker_info, segment_tip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (test_id, "sample_file_id", "پاسخ نمونه", 1, "en", "Giver 1", "راهنمای تست")
        )
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
    waiting_for_broadcast_message = State()

class UserTestStates(StatesGroup):
    taking_test = State()
    waiting_for_tip_ack = State()

# ⌨️ دکمه‌های ثابت پایین صفحه (کیبورد اصلی)
def get_main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 شروع آزمون"), KeyboardButton(text="🔙 منوی قبل")]
        ],
        resize_keyboard=True
    )

@dp.message(F.text.in_(["/start", "🚀 شروع آزمون", "🔙 منوی قبل"]))
async def cmd_start_or_menu(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or ""
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,))
    admin_row = cursor.fetchone()
    conn.commit()
    conn.close()
    
    if admin_row and admin_row[0] == 1:
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, test_type FROM tests")
    tests = cursor.fetchall()
    conn.close()

    if not tests:
        await message.answer("سلام! در حال حاضر هیچ آزمونی فعال نشده است.", reply_markup=get_main_reply_keyboard())
        return

    keyboard_buttons = []
    for t_id, t_title, t_type in tests:
        type_label = "🎯 تعیین سطح" if t_type == "placement" else "📝 ماک کامل"
        keyboard_buttons.append([InlineKeyboardButton(text=f"{type_label}: {t_title}", callback_data=f"start_test_{t_id}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("سلام! به بخش آزمون‌های ترجمه شفاهی خوش آمدید.\n\nلطفاً یکی از آزمون‌های زیر را از لیست انتخاب کنید:", reply_markup=keyboard)
    # اطمینان از ماندن دکمه‌های ثابت پایین صفحه
    await message.answer("👇 از منوی زیر نیز می‌توانید استفاده کنید:", reply_markup=get_main_reply_keyboard())

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
        user_voices=[]
    )

    await show_segment_tip(callback.message, state)
    await callback.answer()

async def show_segment_tip(message: Message, state: FSMContext):
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    seg = segments[curr_idx]
    
    tip_text = seg[5] if seg[5] else "• حتماً کاغذ و قلم همراه داشته باشید تا نکات و اعداد کلیدی را یادداشت کنید."
    
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
    user_id = message.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked FROM admins WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row and row[0] == 1:
        conn.close()
        await message.answer("❌ دسترسی شما مسدود شده است.")
        return
        
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ? AND is_blocked = 0", (user_id,))
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
            await message.answer("❌ رمز عبور ۳ بار به اشتباه وارد شد.")
        else:
            cursor.execute("INSERT OR REPLACE INTO admins (user_id, failed_attempts, is_blocked) VALUES (?, ?, 0)", (user_id, failed))
            conn.commit()
            conn.close()
            await message.answer("رمز عبور اشتباه است.")

async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ساخت آزمون جدید (گام به گام)", callback_data="admin_create_manual")],
        [InlineKeyboardButton(text="✏️ مدیریت آزمون‌ها", callback_data="admin_manage_tests")],
        [InlineKeyboardButton(text="📢 ارسال نوتیفیکیشن", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="مشاهده نتایج", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="پاکسازی اطلاعات", callback_data="admin_clear_db")]
    ])
    await message.answer("پنل مدیریت پیشرفته:", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.message.answer("📢 متن پیام همگانی را بفرستید:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_message, F.text)
async def process_broadcast_message(message: Message, state: FSMContext):
    broadcast_text = message.text
    await state.clear()
    status_msg = await message.answer("⏳ در حال ارسال...")
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    success_count = 0
    for user in users:
        try:
            await bot.send_message(chat_id=user[0], text=f"🔔 **اطلاعیه جدید:**\n\n{broadcast_text}")
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await status_msg.edit_text(f"✅ ارسال به {success_count} کاربر انجام شد.")
    await show_admin_panel(message)

@dp.callback_query(F.data == "admin_create_manual")
async def admin_create_manual(callback: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="تعیین سطح", callback_data="type_placement"), InlineKeyboardButton(text="ماک کامل", callback_data="type_mock")]
    ])
    await callback.message.answer("نوع آزمون:", reply_markup=keyboard)
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
        await message.answer("این عنوان تکراری است:")
        return
    conn.close()

    await state.update_data(test_id=test_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="انگلیسی 🇬🇧", callback_data="lang_en"), InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await message.answer(f"زبان سگمنت {seg_index}:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def process_segment_lang(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await state.update_data(segment_lang=lang)
    await callback.message.answer("فایل صوتی سگمنت را بفرستید:")
    await state.set_state(AdminStates.waiting_for_segment_audio)
    await callback.answer()

@dp.message(AdminStates.waiting_for_segment_audio, F.voice | F.audio | F.document)
async def process_segment_audio(message: Message, state: FSMContext):
    file_id = message.voice.file_id if message.voice else (message.audio.file_id if message.audio else message.document.file_id)
    await state.update_data(file_id=file_id)
    await message.answer("متن راهنما (Tip) سگمنت را بفرستید:")
    await state.set_state(AdminStates.waiting_for_segment_tip)

@dp.message(AdminStates.waiting_for_segment_tip, F.text)
async def process_segment_tip(message: Message, state: FSMContext):
    await state.update_data(segment_tip=message.text)
    await message.answer("متن کلید/پاسخ (Answer Key) را وارد کنید:")
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
    await message.answer(f"سگمنت {seg_index} ثبت شد.", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_add_more_segment")
async def admin_add_more_segment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_index = data.get("segment_index") + 1
    await state.update_data(segment_index=new_index)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="انگلیسی 🇬🇧", callback_data="lang_en"), InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await callback.message.answer(f"زبان سگمنت {new_index}:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "admin_finish_test")
async def admin_finish_test(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("آزمون ذخیره شد!")
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
        await callback.message.edit_text("آزمونی نیست.")
        await show_admin_panel(callback.message)
        await callback.answer()
        return
    buttons = [[InlineKeyboardButton(text=f"📂 {t[1]} ({t[2]})", callback_data=f"manage_test_{t[0]}")] for t in tests]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back_to_main")])
    await callback.message.edit_text("انتخاب آزمون:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@dp.callback_query(F.data.startswith("manage_test_"))
async def manage_single_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,))
    test_row = cursor.fetchone()
    cursor.execute("SELECT id, segment_index, segment_lang FROM segments WHERE test_id = ?", (test_id,))
    segments = cursor.fetchall()
    conn.close()
    buttons = [[InlineKeyboardButton(text=f"سگمنت {s[1]} ({s[2]})", callback_data=f"edit_seg_{s[0]}")] for s in segments]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_manage_tests")])
    await callback.message.edit_text(f"مدیریت {test_row[0]}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_seg_"))
async def edit_segment_options(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT segment_index, answer_key, segment_tip FROM segments WHERE id = ?", (seg_id,))
    seg = cursor.fetchone()
    conn.close()
    await callback.message.edit_text(f"سگمنت {seg[0]}\nراهنما: {seg[2]}\nپاسخ: {seg[1]}")
    await callback.answer()

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
        await callback.message.edit_text("نتیجه‌ای نیست.")
        return
    text = "۵ نتیجه اخیر:\n\n" + "\n".join([f"ک: {r[0]} | آزمون: {r[1]}" for r in rows])
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
    await callback.message.answer("پاکسازی انجام شد.")
    await callback.answer()

@dp.message(UserTestStates.taking_test, F.voice)
async def handle_user_voice_test(message: Message, state: FSMContext):
    data = await state.get_data()
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    user_voices = data.get("user_voices", [])

    user_voices.append({
        "segment_index": curr_idx + 1,
        "voice_file_id": message.voice.file_id
    })

    next_idx = curr_idx + 1
    if next_idx < len(segments):
        await state.update_data(current_index=next_idx, user_voices=user_voices)
        await show_segment_tip(message, state)
    else:
        status_msg = await message.answer("⏳ آزمون به پایان رسید. هوش مصنوعی در حال تحلیل دقیق ویس‌ها است...")
        
        test_title = data.get("test_title")
        test_type = data.get("test_type")
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name

        session_results = []

        for idx, item in enumerate(user_voices):
            seg_tuple = segments[idx]
            answer_key, segment_lang = seg_tuple[2], seg_tuple[3]
            
            file_path = f"v_{user_id}_{idx}.ogg"
            audio_file = None
            try:
                file = await bot.get_file(item["voice_file_id"])
                await bot.download_file(file.file_path, file_path)
                
                audio_file = genai.upload_file(file_path=file_path)
                while True:
                    file_status = genai.get_file(audio_file.name)
                    if file_status.state.name == "ACTIVE":
                        break
                    elif file_status.state.name == "FAILED":
                        raise Exception("خطا در پردازش صوت.")
                    await asyncio.sleep(2)

                system_prompt = f"""
تو یک سیستم ارزیابی حرفه‌ای آزمون ترجمه شفاهی NAATI CCL هستی.
کلید مرجع: "{answer_key}"
نوع آزمون: {test_type}
شماره سگمنت: {idx + 1}
زبان مرجع: {segment_lang}

ترجمه صدای کاربر را با کلید مرجع مقایسه کن و خروجی را دقیقاً با این فرمت ارسال کن:
سگمنت {idx + 1}
- دقت (Accuracy): [عدد]%
- زبان (Language): [عدد]%
- ارائه (Delivery): [عدد]%
- نمره کل (Overall): [عدد]%
توضیحات کوتاه: [متن]
"""
                model = genai.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content([system_prompt, audio_file])
                
                if response and response.text:
                    session_results.append(response.text.strip())
                else:
                    session_results.append(f"سگمنت {idx + 1}:\n- دقت (Accuracy): 60%\n- زبان (Language): 60%\n- ارائه (Delivery): 60%\n- نمره کل (Overall): 60%\nتوضیحات کوتاه: ارزیابی پیش‌فرض.")
                    
            except Exception as e:
                session_results.append(f"سگمنت {idx + 1}:\n- دقت (Accuracy): 50%\n- زبان (Language): 50%\n- ارائه (Delivery): 50%\n- نمره کل (Overall): 50%\nتوضیحات کوتاه: خطا ({str(e)})")
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if audio_file:
                    try:
                        genai.delete_file(audio_file.name)
                    except:
                        pass

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
            if avg_overall < 50:
                advice = "تفسیر و مشاوره: با توجه به نمرات کسب‌شده، هزینه‌های آزمون و آپدیت‌های جدید که نشان می‌دهد مصحح‌ها سخت‌گیری بیشتری دارند، اصلاً به شما پیشنهاد نمی‌کنیم که در این مقطع به سراغ آزمون اصلی بروید. شما نیاز به تمرین بیشتری دارید."
            elif avg_overall < 75:
                advice = "تفسیر و مشاوره: با توجه به نمرات به دست آمده، عملکرد شما نیاز به رفع اشکال دقیق‌تر توسط یک استاد دارد و توصیه می‌شود با سرعت به سراغ آزمون اصلی نروید."
            else:
                advice = "تفسیر و مشاوره: عملکرد شما عالی بود! با توجه به این نمرات، می‌توانید با کمی تمرینِ هدفمند به سراغ آزمون اصلی بروید؛ با این حال، پیشنهاد می‌کنیم حتماً از راهنمایی‌های یک استاد هم بهره‌مند شوید."
        else:
            advice = "ارزیابی آزمون ماک کامل انجام شد."

        report_summary = f"""
📋 **درخواست بررسی کارنامه جدید**
👤 کاربر: @{username} (شناسه: `{user_id}`)
📚 آزمون: {test_title} ({test_type})

📊 **میانگین نمرات:**
• دقت (Accuracy): {avg_acc}%
• زبان (Language): {avg_lang}%
• ارائه (Delivery): {avg_del}%
• نمره کل (Overall): {avg_overall}%

{advice}
"""

        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins WHERE is_blocked = 0")
        active_admins = cursor.fetchall()
        conn.close()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تایید و ارسال به کاربر", callback_data=f"approve_{user_id}")],
            [InlineKeyboardButton(text="❌ رد کردن", callback_data=f"reject_{user_id}")]
        ])

        for admin in active_admins:
            admin_id = admin[0]
            try:
                await bot.send_message(chat_id=admin_id, text=report_summary, reply_markup=keyboard)
                for item in user_voices:
                    await bot.send_voice(chat_id=admin_id, voice=item["voice_file_id"], caption=f"ویس سگمنت {item['segment_index']} - کاربر @{username}")
            except Exception as err:
                logging.error(f"Failed to send report to admin {admin_id}: {err}")

        await status_msg.edit_text("✅ آزمون به پایان رسید و کارنامه برای ادمین ارسال شد.", reply_markup=get_main_reply_keyboard())
        await state.clear()

@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve_report(callback: CallbackQuery, state: FSMContext):
    target_user_id = int(callback.data.split("_")[1])
    report = callback.message.text.replace("📋 درخواست بررسی کارنامه جدید", "📋 **کارنامه نهایی آزمون شما**")
    
    try:
        await bot.send_message(chat_id=target_user_id, text=f"🎉 **کارنامه و نتیجه آزمون شما توسط ادمین تایید و ارسال شد:**\n\n{report}", reply_markup=get_main_reply_keyboard())
        await bot.send_message(chat_id=target_user_id, text="🔒 برای دریافت برنامه تمرینی، لطفاً در کانال ما عضو شوید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="بررسی عضویت", callback_data="check_channel_membership")]
        ]))
        await callback.message.edit_text(callback.message.text + "\n\n✅ **وضعیت: تایید و ارسال شد به کاربر**")
    except Exception as e:
        await callback.answer(f"خطا: {e}", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject_report(callback: CallbackQuery, state: FSMContext):
    target_user_id = int(callback.data.split("_")[1])
    try:
        await bot.send_message(chat_id=target_user_id, text="❌ متأسفانه کارنامه آزمون شما توسط ادمین تایید نشد.", reply_markup=get_main_reply_keyboard())
        await callback.message.edit_text(callback.message.text + "\n\n❌ **وضعیت: رد شد**")
    except Exception as e:
        await callback.answer(f"خطا: {e}", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data == "check_channel_membership")
async def check_channel_membership(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            await callback.message.answer("عضویت شما تأیید شد!", reply_markup=get_main_reply_keyboard())
            await state.clear()
        else:
            await callback.answer("شما هنوز در کانال عضو نشده‌اید!", show_alert=True)
    except Exception as e:
        await callback.answer(f"خطا در بررسی عضویت: {e}", show_alert=True)

async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info("Flask web server started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
