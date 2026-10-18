import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from google import genai

# توکن تلگرام ارسال شده توسط شما
TELEGRAM_BOT_TOKEN = "8834529401:AAFEVB8Xyzob8PVkN-V2Qn0MfZ41NscWkAw"

# دریافت کلید Gemini از متغیرهای محیطی سرور
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

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
        "لطفاً یک ویس (Voice) به زبان انگلیسی بفرستید تا آن را از نظر تلفظ، گرامر، مکث‌ها و سطح CEFR تحلیل کنم."
    )

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
        
        await status_msg.edit_text(response.text)
        ai_client.files.delete(name=audio_file.name)

    except Exception as e:
        await status_msg.edit_text(f"❌ خطایی در تحلیل رخ داد:\n{str(e)}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def main():
    print("ربات روی سرور فعال شد...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
