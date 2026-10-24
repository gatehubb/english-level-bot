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

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = "8834529401:AAFEVB8Xyzob8PVkN-V2Qn0MfZ41NscWkAw"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_PASSWORD = "2377451"

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
    cursor.execute("CREATE TABLE IF NOT EXISTS mocks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, mock_id INTEGER, file_id TEXT, answer_key TEXT, segment_index INTEGER, FOREIGN KEY(mock_id) REFERENCES mocks(id) ON DELETE CASCADE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, mock_title TEXT, analysis TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

init_db()

class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_mock_title = State()
    waiting_for_segment_audio = State()
    waiting_for_segment_key = State()
    waiting_for_rename_mock_new_title = State()

class UserTestStates(StatesGroup):
    taking_test = State()

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM mocks")
    mocks = cursor.fetchall()
    conn.close()

    if not mocks:
        await message.answer("سلام! 👋 به ربات تعیین سطح خوش آمدید.\n\nدر حال حاضر هیچ آزمون ماکی توسط مدیریت فعال نشده است.")
        return

    keyboard_buttons = []
    for m_id, m_title in mocks:
        keyboard_buttons.append([InlineKeyboardButton(text=f"📝 ماک: {m_title}", callback_data=f"start_mock_{m_id}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("👋 سلام! به بخش آزمون‌های ماک تعیین سطح انگلیسی خوش آمدید.\n\nلطفاً یکی از آزمون‌های زیر را انتخاب کنید:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("start_mock_"))
async def user_start_mock(callback: CallbackQuery, state: FSMContext):
    mock_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM mocks WHERE id = ?", (mock_id,))
    mock_row = cursor.fetchone()
    
    cursor.execute("SELECT id, file_id, answer_key FROM segments WHERE mock_id = ? ORDER BY segment_index ASC", (mock_id,))
    segments = cursor.fetchall()
    conn.close()

    if not segments:
        await callback.message.answer("⚠️ این آزمون هنوز سگمنتی ندارد.")
        await callback.answer()
        return

    # ذخیره لیست کل سگمنت‌ها در FSM برای مدیریت آزمون پیوسته
    await state.set_state(UserTestStates.taking_test)
    await state.update_data(
        mock_id=mock_id,
        mock_title=mock_row[0],
        segments=segments,
        current_index=0,
        session_results=[]
    )

    first_seg = segments[0]
    await callback.message.answer(f"🚀 **آغاز آزمون ماک: {mock_row[0]}**\n\n🎧 **سگمنت شماره ۱ از {len(segments)}:**\nبه فایل صوتی زیر گوش دهید و ترجمه آن را به صورت ویس ارسال کنید:")
    
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
        await message.answer("❌ رمز عبور اشتباه است. دوباره تلاش کنید.")

async def show_admin_panel(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ساخت ماک جدید و افزودن سگمنت‌ها", callback_data="admin_create_mock")],
        [InlineKeyboardButton(text="⚙️ تغییر نام ماک‌ها (Rename)", callback_data="admin_list_mocks")],
        [InlineKeyboardButton(text="📊 مشاهده نتایج کاربران", callback_data="admin_view_results")],
        [InlineKeyboardButton(text="🗑 پاکسازی کل اطلاعات", callback_data="admin_clear_db")]
    ])
    await message.answer("⚙️ **پنل مدیریت پیشرفته ماک‌ها**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_create_mock")
async def admin_create_mock(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 لطفاً **عنوان (نام) ماک جدید** را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_mock_title)
    await callback.answer()

@dp.message(AdminStates.waiting_for_mock_title, F.text)
async def process_mock_title(message: Message, state: FSMContext):
    mock_title = message.text
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO mocks (title) VALUES (?)", (mock_title,))
        conn.commit()
        mock_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        await message.answer("❌ این نام ماک قبلاً ثبت شده است. نام دیگری وارد کنید:")
        return
    conn.close()

    await state.update_data(mock_id=mock_id, mock_title=mock_title, segment_index=1)
    await message.answer(f"✅ ماک **«{mock_title}»** ایجاد شد.\n\n🎙 لطفاً **فایل صوتی سگمنت شماره ۱** را ارسال یا آپلود کنید:")
    await state.set_state(AdminStates.waiting_for_segment_audio)

@dp.message(AdminStates.waiting_for_segment_audio, F.voice | F.audio | F.document)
async def process_segment_audio(message: Message, state: FSMContext):
    file_id = None
    if message.voice:
        file_id = message.voice.file_id
    elif message.audio:
        file_id = message.audio.file_id
    elif message.document:
        file_id = message.document.file_id

    if not file_id:
        await message.answer("❌ لطفاً یک فایل صوتی معتبر ارسال کنید.")
        return

    await state.update_data(file_id=file_id)
    await message.answer("✅ فایل صوتی با موفقیت دریافت شد.\n\n📝 حالا **متن کلید/پاسخ این سگمنت** را وارد کنید (شامل متن انگلیسی و ترجمه فارسی آن):")
    await state.set_state(AdminStates.waiting_for_segment_key)

@dp.message(AdminStates.waiting_for_segment_key, F.text)
async def process_segment_key(message: Message, state: FSMContext):
    data = await state.get_data()
    mock_id = data.get("mock_id")
    file_id = data.get("file_id")
    answer_key = message.text
    seg_index = data.get("segment_index")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO segments (mock_id, file_id, answer_key, segment_index) VALUES (?, ?, ?, ?)",
        (mock_id, file_id, answer_key, seg_index)
    )
    conn.commit()
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ برو به سگمنت بعدی", callback_data="admin_add_more_segment")],
        [InlineKeyboardButton(text="🏁 پایان بارگذاری", callback_data="admin_finish_mock")]
    ])
    await message.answer(f"✅ سگمنت شماره {seg_index} با موفقیت ثبت شد. مایلید چه کاری انجام دهید؟", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_add_more_segment")
async def admin_add_more_segment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_index = data.get("segment_index") + 1
    await state.update_data(segment_index=new_index)
    await callback.message.answer(f"🎙 لطفاً **فایل صوتی سگمنت شماره {new_index}** را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_segment_audio)
    await callback.answer()

@dp.callback_query(F.data == "admin_finish_mock")
async def admin_finish_mock(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🎉 بارگذاری ماک با موفقیت به اتمام رسید!")
    await show_admin_panel(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "admin_list_mocks")
async def admin_list_mocks(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM mocks")
    mocks = cursor.fetchall()
    conn.close()

    if not mocks:
        await callback.message.edit_text("📭 هیچ ماکی ثبت نشده است.")
        return

    buttons = []
    for m_id, m_title in mocks:
        buttons.append([InlineKeyboardButton(text=f"✏️ تغییر نام: {m_title}", callback_data=f"rename_mock_{m_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back_home")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("⚙️ برای ویرایش نام هر ماک، آن را انتخاب کنید:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("rename_mock_"))
async def rename_mock_start(callback: CallbackQuery, state: FSMContext):
    mock_id = int(callback.data.split("_")[2])
    await state.update_data(rename_mock_id=mock_id)
    await callback.message.answer("📝 لطفاً **نام جدید** این ماک را ارسال کنید:")
    await state.set_state(AdminStates.waiting_for_rename_mock_new_title)
    await callback.answer()

@dp.message(AdminStates.waiting_for_rename_mock_new_title, F.text)
async def process_rename_mock(message: Message, state: FSMContext):
    data = await state.get_data()
    mock_id = data.get("rename_mock_id")
    new_title = message.text

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE mocks SET title = ? WHERE id = ?", (new_title, mock_id))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(f"✅ نام ماک به **«{new_title}»** تغییر یافت.")
    await show_admin_panel(message)

@dp.callback_query(F.data == "admin_back_home")
async def admin_back_home(callback: CallbackQuery):
    await show_admin_panel(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "admin_view_results")
async def admin_view_results(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, mock_title, analysis, date FROM results ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await callback.message.edit_text("📭 هیچ نتیجه‌ای تاکنون ثبت نشده است.")
        return

    text = "📊 **۵ تحلیل اخیر آزمون‌های کاربران:**\n\n"
    for row in rows:
        uname = row[0] or "بدون نام"
        text += f"👤 کاربر: {uname}\n📚 ماک: {row[1]}\n🕒 زمان: {row[3]}\n{row[2][:150]}...\n-------------------\n"
    
    await callback.message.edit_text(text)

@dp.callback_query(F.data == "admin_clear_db")
async def admin_clear_db(callback: CallbackQuery):
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM results")
    cursor.execute("DELETE FROM segments")
    cursor.execute("DELETE FROM mocks")
    conn.commit()
    conn.close()
    await callback.message.answer("🗑 تمام اطلاعات پاک شد.")
    await callback.answer()

@dp.message(UserTestStates.taking_test, F.voice)
async def handle_user_voice_test(message: Message, state: FSMContext):
    data = await state.get_data()
    mock_title = data.get("mock_title")
    segments = data.get("segments")
    curr_idx = data.get("current_index")
    session_results = data.get("session_results")

    seg_id, file_id, answer_key = segments[curr_idx]
    status_msg = await message.answer(f"📥 در حال پردازش سگمنت {curr_idx + 1}...")
    file_path = f"voice_{message.from_user.id}.ogg"

    try:
        if not GEMINI_API_KEY:
            await status_msg.edit_text("❌ کلید GEMINI_API_KEY تنظیم نشده است.")
            return

        system_prompt = f"""
تو یک سیستم ارزیابی حرفه‌ای آزمون ترجمه شفاهی NAATI CCL هستی.

اگر صوت خالی، بی‌صدا، دارای نویز شدید یا بسیار ناقص بود، پاسخ خود را دقیقاً با این عبارت شروع کن:
`INVALID_AUDIO: [دلیل مختصر به فارسی]`

اگر صوت معتبر است، ارزیابی خود را برای سگمنت شماره {curr_idx + 1} از آزمون ماک "{mock_title}" با کلید مرجع زیر انجام بده:

🎯 **کلید مرجع:**
"{answer_key}"

خروجی را دقیقاً با این فرمت و بدون استفاده از ستاره دور سرتیترها ارسال کن:

نتیجه ارزیابی سگمنت {curr_idx + 1}

متن مرجع:
{answer_key}

ترجمه شما:
[متن پیاده‌شده از گفتار کاربر]

ارزیابی عملکرد (Assessment Criteria)

دقت (Accuracy):
[توضیح کوتاه]

کیفیت زبان (Language):
[توضیح کوتاه]

کیفیت ارائه (Delivery):
[توضیح کوتاه]

نسخه درست و بهبود یافته:
[نسخه بهینه ترجمه]

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
                    model="gemini-3.8-flash",
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
            await status_msg.edit_text(f"⚠️ **اخطار در بررسی فایل صوتی (سگمنت {curr_idx + 1}):**\n\n{error_details}\n\nلطفاً همین سگمنت را دوباره با ویس ارسال کنید.")
            return

        # حذف پیام وضعیت و ذخیره نتیجه این سگمنت
        await status_msg.delete()
        session_results.append(analysis_result)

        # رفتن به سگمنت بعدی یا پایان آزمون
        next_idx = curr_idx + 1
        if next_idx < len(segments):
            await state.update_data(current_index=next_idx, session_results=session_results)
            next_seg = segments[next_idx]
            await message.answer(f"✅ سگمنت {curr_idx + 1} ثبت شد.\n\n🎧 **سگمنت شماره {next_idx + 1} از طول آزمون:**\nبه فایل صوتی زیر گوش دهید:")
            try:
                await message.answer_voice(voice=next_seg[1])
            except:
                try:
                    await message.answer_audio(audio=next_seg[1])
                except:
                    await message.answer_document(document=next_seg[1])
        else:
            # پایان آزمون و محاسبه میانگین نمرات کل
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

            # ذخیره کارنامه کلی در پایگاه داده
            full_report_text = f"🎉 **کارنامه نهایی آزمون ماک: {mock_title}**\n\n" \
                               f"📈 **میانگین نمرات کل آزمون:**\n" \
                               f"• دقت (Accuracy): {avg_acc}%\n" \
                               f"• زبان (Language): {avg_lang}%\n" \
                               f"• ارائه (Delivery): {avg_del}%\n\n" \
                               f"🏆 **نمره کل نهایی (Overall): {avg_overall}%**"

            conn = sqlite3.connect("bot_database.db")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO results (user_id, username, mock_title, analysis) VALUES (?, ?, ?, ?)",
                (message.from_user.id, message.from_user.username, mock_title, full_report_text + "\n\n" + "\n---\n".join(session_results))
            )
            conn.commit()
            conn.close()

            # دکمه شیشه‌ای برای مشاهده جزئیات کل سگمنت‌ها
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 مشاهده تحلیل جزئیات هر سگمنت", callback_data="show_detailed_report")]
            ])

            await state.update_data(session_results=session_results) # نگهداری برای نمایش جزئیات
            await message.answer(full_report_text, reply_markup=keyboard)
            await state.set_state(UserTestStates.taking_test) # حفظ حالت برای دکمه

    except Exception as e:
        await status_msg.edit_text(f"❌ خطا در تحلیل:\n{str(e)}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@dp.callback_query(F.data == "show_detailed_report")
async def show_detailed_report(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    session_results = data.get("session_results", [])
    
    if not session_results:
        await callback.message.answer("📭 اطلاعاتی برای نمایش موجود نیست.")
        await callback.answer()
        return

    await callback.message.answer("📋 **گزارش کامل تحلیل تک‌تک سگمنت‌ها:**")
    for res in session_results:
        await callback.message.answer(res)
    
    await state.clear()
    await callback.answer()

async def handle_health_check(request):
    return web.Response(text="Bot is running alive!")

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
    logging.info("Starting Multi-Mock continuous exam bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
