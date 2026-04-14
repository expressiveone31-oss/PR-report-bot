import os
import pandas as pd
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from openai import OpenAI

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
openai_client = OpenAI(api_key=OPENAI_KEY)

# Функция для имитации/получения данных (пока Телеметр не настроен полностью)
def get_telemetr_stats(url):
    return {"views": 18000, "reposts": 25}

async def get_pr_accents(raw_data):
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "Ты эксперт Digital PR. Пиши кратко и сочно."},
                  {"role": "user", "content": f"Сделай акценты для отчета: {raw_data}"}]
    )
    return response.choices[0].message.content

@dp.message_handler(content_types=['document'])
async def handle_mp(message: types.Message):
    await message.answer("🔄 Читаю файл, ищу колонки...")
    file_path = "temp_mp.xlsx"
    await message.document.download(destination_file=file_path)
    
    try:
        # Читаем файл
        df = pd.read_excel(file_path)
        
        # МАГИЯ: очищаем названия колонок от пробелов и переводим в нижний регистр
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        # Ищем подходящие колонки (по ключевым словам)
        url_col = next((c for c in df.columns if 'ссылка' in c and 'публикац' in c), None)
        plan_col = next((c for c in df.columns if 'прогноз' in c or 'планируем' in c), None)

        if not url_col:
            await message.answer("❌ Не нашел колонку со ссылками на публикации. Проверь название!")
            return

        paid_summary = []
        organic_summary = []

        for _, row in df.iterrows():
            url = row[url_col]
            if pd.isna(url) or "t.me" not in str(url): continue
            
            stats = get_telemetr_stats(url)
            plan = row[plan_col] if plan_col and pd.notna(row[plan_col]) else 0
            
            if plan > 0:
                diff = ((stats['views'] - plan) / plan) * 100
                paid_summary.append(f"Пост: {url}\n- План: {plan}, Факт: {stats['views']} ({diff:+.1f}%)")
            else:
                organic_summary.append(f"Органика: {url}\n- Охват: {stats['views']}")

        full_text = "ПЛАТНЫЕ:\n" + "\n".join(paid_summary) + "\n\nОРГАНИКА:\n" + "\n".join(organic_summary)
        accents = await get_pr_accents(full_text)
        
        await message.answer(f"✅ **Готово!**\n\n{accents}")

    except Exception as e:
        await message.answer(f"❌ Ошибка в коде: {str(e)}")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
