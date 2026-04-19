import os
import pandas as pd
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from openai import OpenAI

# Ключи из переменных Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
openai_client = OpenAI(api_key=OPENAI_KEY)

# Заглушка для статистики (имитируем успешный посев)
def get_stats_placeholder():
    return {"views": 18500, "reposts": 42}

async def generate_accents(data_text):
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты эксперт Digital PR. Твоя задача — выделить самые крутые успехи кампании для отчета."},
                {"role": "user", "content": f"Сделай сочные акценты на основе этих данных (сравни план и факт): {data_text}"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Ошибка OpenAI: {str(e)}"

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.answer("🚀 Бот-аналитик PR-отчетов запущен! Присылай Excel с медиапланом по Гаю Ричи или любому другому проекту.")

@dp.message_handler(content_types=['document'])
async def handle_file(message: types.Message):
    await message.answer("🔍 Вижу файл. Начинаю поиск ссылок и анализ...")
    
    file_name = "report_temp.xlsx"
    await message.document.download(destination_file=file_name)
    
    try:
        df = pd.read_excel(file_name)
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        # Ищем колонку со ссылками и планом
        url_col = next((c for c in df.columns if 'ссылка' in c), None)
        plan_col = next((c for c in df.columns if 'прогноз' in c or 'план' in c), None)

        if not url_col:
            await message.answer("❌ В таблице нет колонки со словом 'Ссылка'.")
            return

        summary = []
        for _, row in df.iterrows():
            url = str(row[url_col])
            if "t.me" not in url: continue
            
            stats = get_stats_placeholder()
            plan = row[plan_col] if plan_col and pd.notna(row[plan_col]) else 0
            
            if plan > 0:
                diff = ((stats['views'] - plan) / plan) * 100
                summary.append(f"• {url}: План {plan}, Факт {stats['views']} ({diff:+.1f}%)")
            else:
                summary.append(f"• Органика {url}: Охват {stats['views']}")

        full_text = "\n".join(summary)
        accents = await generate_accents(full_text)
        
        await message.answer(f"📊 **РЕЗУЛЬТАТЫ:**\n\n{full_text}\n\n💡 **АКЦЕНТЫ ДЛЯ СЛАЙДА:**\n{accents}")

    except Exception as e:
        await message.answer(f"❌ Ошибка разбора: {str(e)}")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
