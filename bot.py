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
    
    cursor.execute("SELECT id, file_id, answer_key, segment_lang FROM segments WHERE test_id = ? ORDER BY segment_index ASC", (test_id,))
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
    
    await callback.message.answer(f"آغاز آزمون: {test_title}\n\nسگمنت ۱ از {len(segments)} ({lang_dir}):\nبه فایل صوتی زیر گوش دهید و ترجمه آن را به صورت ویس ارسال کنید:")
    
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

# ---------- تجزیه و تحلیل دقیق و هوشمند فایل متنی و فایل‌های صوتی زیپ ----------
@dp.callback_query(F.data == "admin_upload_zip")
async def admin_upload_zip(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("لطفاً فایل زیپ (.zip) شامل اسکریپت متنی و پوشه صوت‌ها را ارسال کنید.")
    await state.set_state(AdminStates.waiting_for_zip_file)
    await callback.answer()

@dp.message(AdminStates.waiting_for_zip_file, F.document)
async def process_zip_upload(message: Message, state: FSMContext):
    if not message.document.file_name.endswith('.zip'):
        await message.answer("❌ لطفاً یک فایل با پسوند .zip ارسال کنید.")
        return

    status_msg = await message.answer("📥 در حال استخراج و تحلیل ساختار فایل متنی و صوت‌ها...")
    file_id = message.document.file_id
    file = await bot.get_file(file_id)
    
    extract_dir = f"temp_zip_{message.from_user.id}"
    os.makedirs(extract_dir, exist_ok=True)
    zip_path = os.path.join(extract_dir, "uploaded.zip")
    
    await bot.download_file(file.file_path, zip_path)

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        txt_file_path = None
        audio_files_map = {}

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

        # خواندن محتوای فایل متنی به صورت کامل
        with open(txt_file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # استخراج عنوان آزمون از داخل متن یا نام فایل
        title_match = re.search(r'Title:\s*(.+)', content)
        test_title = title_match.group(1).strip() if title_match else message.document.file_name.replace('.zip', '')
        test_type = "mock"

        # تفکیک بلوک‌های سگمنت بر اساس خطوط جداکننده یا تگ [Segment]
        blocks = content.split('--------------------------------------------------')
        dialogues_data = []

        for block in blocks:
            if '[Segment' in block:
                # شماره سگمنت
                idx_match = re.search(r'\[Segment\s+(\d+)\]', block)
                if not idx_match:
                    continue
                idx = int(idx_match.group(1))

                # عنوان گوینده (مثل RICK (ADVISOR - BRITISH ENGLISH) (ENGLISH))
                header_match = re.search(r'\[Segment\s+\d+\]\s*-\s*(.*)', block)
                speaker_info = header_match.group(1).strip() if header_match else f"Segment {idx}"

                # تشخیص زبان بر اساس تگ داخل عنوان
                lang = "en" if "(ENGLISH)" in speaker_info.upper() else "fa"

                # متن پاسخ مرجع (Answer Text)
                ans_match = re.search(r'Answer Text \(Model Interpretation\):\s*(.*?)(?=\nAudio File:|$)', block, re.DOTALL)
                answer_text = ans_match.group(1).strip() if ans_match else ""

                # نام فایل صوتی مربوطه
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
        else:
            cursor.execute("INSERT INTO tests (title, test_type) VALUES (?, ?)", (test_title, test_type))
            conn.commit()
            test_id = cursor.lastrowid

        total_segments = 0
        for item in dialogues_data:
            audio_full_path = audio_files_map.get(item["audio_filename"])
            
            # اگر نام فایل دقیقا مچ نشد، جستجوی نسبی انجام دهیم
            if not audio_full_path:
                for fname, fpath in audio_files_map.items():
                    if item["audio_filename"].lower() in fname.lower():
                        audio_full_path = fpath
                        break

            if audio_full_path and os.path.exists(audio_full_path):
                sent_msg = await message.answer_audio(audio=FSInputFile(audio_full_path), caption=f"بارگذاری سگمنت {item['index']}")
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
        
        await status_msg.edit_text(f"✅ آزمون «{test_title}» با موفقیت همراه با **{total_segments} سگمنت** و تطبیق دقیق صوت‌ها و پاسخ‌ها ذخیره شد!")
        await state.clear()
        await show_admin_panel(message)

    except Exception as e:
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        await status_msg.edit_text(f"❌ خطا در پردازش فایل زیپ:\n{str(e)}")
        await state.clear()

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

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE tests SET title = ? WHERE id = ?", (new_title, test_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        await message.answer("❌ این عنوان قبلاً ثبت شده است. نام دیگری وارد کنید:")
        return
    conn.close()

    await state.clear()
    await message.answer("✅ نام آزمون با موفقیت ویرایش شد.")
    await show_admin_panel(message)

@dp.callback_query(F.data.startswith("edit_seg_"))
async def edit_segment_options(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT segment_index, segment_lang, answer_key, test_id, speaker_info FROM segments WHERE id = ?", (seg_id,))
    seg = cursor.fetchone()
    conn.close()

    if not seg:
        await callback.answer("سگمنت مورد نظر یافت نشد.", show_alert=True)
        return

    index, lang, answer_key, test_id, speaker = seg
    await state.update_data(editing_seg_id=seg_id, test_id=test_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش متن پاسخ (Answer Key)", callback_data=f"seg_action_key_{seg_id}")],
        [InlineKeyboardButton(text="🔄 جایگزین کردن فایل صوتی", callback_data=f"seg_action_audio_{seg_id}")],
        [InlineKeyboardButton(text="🌐 تغییر زبان سگمنت (EN/FA)", callback_data=f"seg_action_lang_{seg_id}")],
        [InlineKeyboardButton(text="🗑 حذف این سگمنت", callback_data=f"seg_action_del_{seg_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"manage_test_{test_id}")]
    ])

    speaker_text = f"\n**عنوان گوینده:** {speaker}" if speaker else ""
    await callback.message.edit_text(
        f"مدیریت سگمنت شماره {index} (زبان: {lang}):{speaker_text}\n\n**متن پاسخ فعلی:**\n{answer_key}\n\nلطفاً عملیات مورد نظر را انتخاب کنید:",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("seg_action_key_"))
async def action_edit_key(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[3])
    await state.set_state(AdminStates.editing_answer_key)
    await callback.message.answer("متن جدید پاسخ (Answer Key) را برای این سگمنت ارسال کنید:")
    await callback.answer()

@dp.message(AdminStates.editing_answer_key, F.text)
async def save_edited_key(message: Message, state: FSMContext):
    data = await state.get_data()
    seg_id = data.get("editing_seg_id")
    new_key = message.text

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE segments SET answer_key = ? WHERE id = ?", (new_key, seg_id))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ متن پاسخ با موفقیت ویرایش شد.")
    await show_admin_panel(message)

@dp.callback_query(F.data.startswith("seg_action_audio_"))
async def action_edit_audio(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[3])
    await state.set_state(AdminStates.editing_audio)
    await callback.message.answer("فایل صوتی جدید (ویس یا فایل صوتی) را برای جایگزینی ارسال کنید:")
    await callback.answer()

@dp.message(AdminStates.editing_audio, F.voice | F.audio | F.document)
async def save_edited_audio(message: Message, state: FSMContext):
    data = await state.get_data()
    seg_id = data.get("editing_seg_id")
    file_id = message.voice.file_id if message.voice else (message.audio.file_id if message.audio else message.document.file_id)

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE segments SET file_id = ? WHERE id = ?", (file_id, seg_id))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ فایل صوتی سگمنت با موفقیت جایگزین شد.")
    await show_admin_panel(message)

@dp.callback_query(F.data.startswith("seg_action_lang_"))
async def action_edit_lang(callback: CallbackQuery, state: FSMContext):
    seg_id = int(callback.data.split("_")[3])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT segment_lang FROM segments WHERE id = ?", (seg_id,))
    current_lang = cursor.fetchone()[0]
    new_lang = "fa" if current_lang == "en" else "en"
    
    cursor.execute("UPDATE segments SET segment_lang = ? WHERE id = ?", (new_lang, seg_id))
    conn.commit()
    conn.close()

    await callback.answer(f"زبان سگمنت با موفقیت به {new_lang.upper()} تغییر یافت!", show_alert=True)
    callback.data = f"edit_seg_{seg_id}"
    await edit_segment_options(callback, state)

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

    await callback.answer("سگمنت با موفقیت حذف شد.", show_alert=True)
    callback.data = f"manage_test_{test_id}"
    await manage_single_test(callback, state)

@dp.callback_query(F.data == "admin_back_to_main")
async def admin_back_to_main(callback: CallbackQuery, state: FSMContext):
    await show_admin_panel(callback.message)
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
    await message.answer("متن کلید/پاسخ این سگمنت را وارد کنید:")
    await state.set_state(AdminStates.waiting_for_segment_key)

@dp.message(AdminStates.waiting_for_segment_key, F.text)
async def process_segment_key(message: Message, state: FSMContext):
    data = await state.get_data()
    test_id = data.get("test_id")
    file_id = data.get("file_id")
    answer_key = message.text
    seg_index = data.get("segment_index")
    segment_lang = data.get("segment_lang")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO segments (test_id, file_id, answer_key, segment_index, segment_lang) VALUES (?, ?, ?, ?, ?)", (test_id, file_id, answer_key, seg_index, segment_lang))
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

    text = "۵ تحلیل اخیر آزمون‌های کاربران:\n\n"
    for row in rows:
        uname = row[0] or "بدون نام"
        text += f"کاربر: {uname}\nآزمون: {row[1]}\nزمان: {row[3]}\n{row[2][:150]}...\n-------------------\n"
    
    await callback.message.edit_text(text)

@dp.callback_query(F.data == "admin_clear_db")
async def admin_clear_db(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM results")
    cursor.execute("DELETE FROM segments")
    cursor.execute("DELETE FROM tests")
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

    seg_id, file_id, answer_key, segment_lang = segments[curr_idx][:4]
    status_msg = await message.answer(f"در حال پردازش سگمنت {curr_idx + 1}...")
    file_path = f"voice_{message.from_user.id}.ogg"

    try:
        if not GEMINI_API_KEY:
            await status_msg.edit_text("کلید GEMINI_API_KEY تنظیم نشده است.")
            return

        system_prompt = f"""
تو یک سیستم ارزیابی حرفه‌ای آزمون ترجمه شفاهی NAATI CCL هستی.
اگر صوت خالی، بی‌صدا، دارای نویز شدید یا بسیار ناقص بود، پاسخ خود را با این عبارت شروع کن:
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
متن مرجع:
[متن]
ترجمه شما:
[ترجمه]
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
            next_seg = segments[next_idx]
            lang_dir = "انگلیسی به فارسی 🇬🇧 ➡️ 🇮🇷" if next_seg[3] == "en" else "فارسی به انگلیسی 🇮🇷 ➡️ 🇬🇧"
            await message.answer(f"سگمنت {curr_idx + 1} ثبت شد.\n\nسگمنت {next_idx + 1} ({lang_dir}):\nبه فایل صوتی گوش دهید:")
            try:
                await message.answer_voice(voice=next_seg[1])
            except:
                try:
                    await message.answer_audio(audio=next_seg[1])
                except:
                    await message.answer_document(document=next_seg[1])
        else:
            import re
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
                status_text = "در مسیر پیشرفت (متوسط رو به بالا)" if readiness >= 60 else "نیاز به تلاش بیشتر"
                
                placement_report = f"""
آزمون تعیین سطح سنجش آمادگی NAATI CCL 📋

نتیجه کلی ارزیابی سطح شما 📊

درصد آمادگی فعلی: {readiness}% 📈
سطح شناختی: {status_text} 🎯
زمان تقریبی مورد نیاز تا آمادگی کامل: ۴ تا ۶ هفته تمرین هدفمند ⏳

شاخص‌های چهارگانه سنجش آمادگی 📐

۱. دقت و انتقال جزئیات (Accuracy): {avg_acc}% 🎯
معیار سنجش: حفظ تمام اطلاعات کلیدی، اعداد و جزئیات بدون حذف یا اضافه کردن.

۲. تسلط و کیفیت زبان (Language & Register): {avg_lang}% 🗣
معیار سنجش: ساختار گرامری درست، تلفظ شفاف و استفاده از واژگان رسمی اداری.

۳. پایداری و روانی بیان (Delivery): {avg_del}% ⏱
معیار سنجش: سرعت ادای مناسب، عدم تپق و نداشتن مکث‌های طولانی غیرعادی.

۴. حافظه و یادداشت‌برداری (Memory & Note-taking): {avg_overall}% 📝
معیار سنجش: توانایی حفظ ساختار جملات بلند و مدیریت کلمات کلیدی.

پیش‌بینی وضعیت در آزمون اصلی 🔮
با وضعیت فعلی، نمره تخمینی شما در آزمون اصلی نشان‌دهنده آمادگی نسبی است که با تمرین هدفمند قابل ارتقا می‌باشد.

🔒 دریافت برنامه تمرینی، متریال آموزشی و نقشه راه اختصاصی

برای دریافت برنامه تمرینی شخصی‌سازی‌شده و متریال‌های ارائه‌شده، لطفاً ابتدا در کانال رسمی ما عضو شوید و سپس دکمه «بررسی عضویت» را بزنید:
"""
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="عضویت در کانال", url=CHANNEL_LINK)],
                    [InlineKeyboardButton(text="بررسی عضویت", callback_data="check_channel_membership")]
                ])
                await state.update_data(session_results=session_results)
                await message.answer(placement_report, reply_markup=keyboard)
            else:
                full_report_text = f"کارنامه نهایی آزمون ماک: {test_title}\n\n" \
                                   f"میانگین نمرات کل آزمون:\n" \
                                   f"• دقت (Accuracy): {avg_acc}%\n" \
                                   f"• زبان (Language): {avg_lang}%\n" \
                                   f"• ارائه (Delivery): {avg_del}%\n\n" \
                                   f"نمره کل نهایی (Overall): {avg_overall}%"

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="مشاهده تحلیل جزئیات هر سگمنت", callback_data="show_detailed_report")]
                ])
                await state.update_data(session_results=session_results)
                await message.answer(full_report_text, reply_markup=keyboard)
            
            await state.set_state(UserTestStates.taking_test)

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
            await callback.message.answer("عضویت شما تأیید شد!\n\nنقشه راه، متریال آموزشی و برنامه تمرینی شما:\n1. مرور واژگان تخصصی دپارتمان‌های دولتی\n2. تمرین یادداشت‌برداری نمادین\n3. استفاده از فایل‌های تمرینی کانال")
            await state.clear()
        else:
            await callback.answer("شما هنوز در کانال عضو نشده‌اید!", show_alert=True)
    except Exception as e:
        await callback.answer("خطا در بررسی عضویت. مطمئن شوید ربات ادمین کانال است.", show_alert=True)

@dp.callback_query(F.data == "show_detailed_report")
async def show_detailed_report(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    session_results = data.get("session_results", [])
    
    if not session_results:
        await callback.message.answer("اطلاعاتی برای نمایش موجود نیست.")
        await callback.answer()
        return

    await callback.message.answer("گزارش کامل تحلیل تک‌تک سگمنت‌ها:")
    for res in session_results:
        await callback.message.answer(res)
    
    await state.clear()
    await callback.answer()

async def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info("Flask web server started in background thread for Render port binding.")
    
    logging.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
