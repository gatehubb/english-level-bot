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
async def admin_upload_zip(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("لطفاً فایل زیپ (.zip) شامل اسکریپت متنی و پوشه صوت‌ها را ارسال کنید.")
    await state.set_state(AdminStates.waiting_for_zip_file)
    await callback.answer()

@dp.message(AdminStates.waiting_for_zip_file, F.document)
async def process_zip_upload_file(message: Message, state: FSMContext):
    if not message.document.file_name.endswith('.zip'):
        await message.answer("❌ لطفاً یک فایل با پسوند .zip ارسال کنید.")
        return

    file_id = message.document.file_id
    file = await bot.get_file(file_id)
    extract_dir = f"temp_zip_{message.from_user.id}"
    os.makedirs(extract_dir, exist_ok=True)
    zip_path = os.path.join(extract_dir, "uploaded.zip")
    await bot.download_file(file.file_path, zip_path)

    await state.update_data(extract_dir=extract_dir, zip_filename=message.document.file_name)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 آزمون تعیین سطح", callback_data="ziptype_placement"),
         InlineKeyboardButton(text="📝 ماک کامل", callback_data="ziptype_mock")]
    ])
    await message.answer("این فایل زیپ مربوط به چه نوع آزمونی است؟", reply_markup=keyboard)
    await state.set_state(AdminStates.waiting_for_zip_test_type)

@dp.callback_query(AdminStates.waiting_for_zip_test_type, F.data.startswith("ziptype_"))
async def process_zip_test_type(callback: CallbackQuery, state: FSMContext):
    test_type = callback.data.split("_")[1]
    data = await state.get_data()
    extract_dir = data.get("extract_dir")
    zip_filename = data.get("zip_filename")

    status_msg = await callback.message.edit_text("📥 در حال استخراج و تحلیل ساختار فایل متنی و صوت‌ها...")

    try:
        with zipfile.ZipFile(os.path.join(extract_dir, "uploaded.zip"), 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        txt_file_path = None
        audio_files_map = {}

        # اسکن دقیق تمام فایل‌های استخراج شده
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.endswith('.txt') and not txt_file_path:
                    txt_file_path = os.path.join(root, file)
                elif file.endswith(('.mp3', '.ogg', '.wav')):
                    audio_files_map[file] = os.path.join(root, file)

        if not txt_file_path:
            await status_msg.edit_text("❌ هیچ فایل متنی (.txt) ای داخل فایل زیپ پیدا نشد.")
            shutil.rmtree(extract_dir)
            await state.clear()
            return

        with open(txt_file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        title_match = re.search(r'Title:\s*(.+)', content)
        test_title = title_match.group(1).strip() if title_match else zip_filename.replace('.zip', '')

        blocks = content.split('--------------------------------------------------')
        dialogues_data = []

        for block in blocks:
            if '[Segment' in block:
                idx_match = re.search(r'\[Segment\s+(\d+)\]', block)
                if not idx_match:
                    continue
                idx = int(idx_match.group(1))

                header_match = re.search(r'\[Segment\s+\d+\]\s*-\s*(.*)', block)
                speaker_info = header_match.group(1).strip() if header_match else f"Segment {idx}"

                lang = "en" if "(ENGLISH)" in speaker_info.upper() else "fa"

                ans_match = re.search(r'Answer Text \(Model Interpretation\):\s*(.*?)(?=\nAudio File:|$)', block, re.DOTALL)
                answer_text = ans_match.group(1).strip() if ans_match else ""

                audio_match = re.search(r'Audio File:\s*([^\s]+)', block)
                audio_filename = audio_match.group(1).strip() if audio_match else ""

                if audio_filename and answer_text:
                    dialogues_data.append({
                        "index": idx,
                        "lang": lang,
                        "speaker_info": speaker_info,
                        "answer_text": answer_text,
                        "audio_filename": audio_filename
                    })

        if not dialogues_data:
            await status_msg.edit_text("❌ ساختار فایل متنی شناسایی نشد یا هیچ سگمنتی استخراج نشد.")
            shutil.rmtree(extract_dir)
            await state.clear()
            return

        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tests WHERE title = ?", (test_title,))
        existing_test = cursor.fetchone()
        
        if existing_test:
            test_id = existing_test[0]
            cursor.execute("DELETE FROM segments WHERE test_id = ?", (test_id,))
            cursor.execute("UPDATE tests SET test_type = ? WHERE id = ?", (test_type, test_id))
        else:
            cursor.execute("INSERT INTO tests (title, test_type) VALUES (?, ?)", (test_title, test_type))
            conn.commit()
            test_id = cursor.lastrowid

        total_segments = 0
        for item in dialogues_data:
            audio_full_path = audio_files_map.get(item["audio_filename"])
            if not audio_full_path:
                for fname, fpath in audio_files_map.items():
                    if item["audio_filename"].lower() in fname.lower():
                        audio_full_path = fpath
                        break

            if audio_full_path and os.path.exists(audio_full_path):
                sent_msg = await callback.message.answer_audio(audio=FSInputFile(audio_full_path), caption=f"بارگذاری سگمنت {item['index']}")
                tg_file_id = sent_msg.audio.file_id
                await sent_msg.delete()

                cursor.execute(
                    "INSERT INTO segments (test_id, file_id, answer_key, segment_index, segment_lang, speaker_info) VALUES (?, ?, ?, ?, ?, ?)",
                    (test_id, tg_file_id, item["answer_text"], item["index"], item["lang"], item["speaker_info"])
                )
                conn.commit()
                total_segments += 1

        conn.close()
        shutil.rmtree(extract_dir)
        
        type_label_txt = "تعیین سطح" if test_type == "placement" else "ماک کامل"
        await status_msg.edit_text(f"✅ آزمون «{test_title}» به عنوان **{type_label_txt}** همراه با **{total_segments} سگمنت** با موفقیت ذخیره شد!")
        await state.clear()
        await show_admin_panel(callback.message)

    except Exception as e:
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        await status_msg.edit_text(f"❌ خطا در پردازش فایل زیپ:\n{str(e)}")
        await state.clear()
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
    await callback.message.edit_text("لطفاً آزمون مورد نظر جهت ویرایش یا مدیریت را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("manage_test_"))
async def manage_single_test(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title, test_type FROM tests WHERE id = ?", (test_id,))
    test_row = cursor.fetchone()
    test_title, test_type = test_row

    cursor.execute("SELECT id, segment_index, segment_lang, speaker_info, answer_key FROM segments WHERE test_id = ? ORDER BY segment_index ASC", (test_id,))
    segments = cursor.fetchall()
    conn.close()

    await state.update_data(managing_test_id=test_id)

    buttons = [
        [InlineKeyboardButton(text="✏️ ویرایش نام این آزمون (ماک)", callback_data=f"edit_test_title_{test_id}")]
    ]
    for seg_id, index, lang, speaker, key in segments:
        label = speaker if speaker else f"Segment {index}"
        short_label = (label[:35] + '...') if len(label) > 35 else label
        buttons.append([InlineKeyboardButton(text=f"سگمنت {index}: {short_label}", callback_data=f"edit_seg_{seg_id}")])

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به لیست آزمون‌ها", callback_data="admin_manage_tests")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(f"مدیریت آزمون: **{test_title}** ({test_type})\n\nسگمنت مورد نظر یا ویرایش نام را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_test_title_"))
async def action_edit_test_title(callback: CallbackQuery, state: FSMContext):
    test_id = int(callback.data.split("_")[3])
    await state.update_data(editing_test_id=test_id)
    await state.set_state(AdminStates.editing_test_title)
    await callback.message.answer("عنوان جدید آزمون (ماک) را ارسال کنید:")
    await callback.answer()

@dp.message(AdminStates.editing_test_title, F.text)
async def save_edited_test_title(message: Message, state: FSMContext):
    data = await state.get_data()
    test_id = data.get("editing_test_id")
    new_title = message.text

    conn = sqlite3.connect("bot_
