import os
import pandas as pd
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from openai import OpenAI

# Загружаем ключи из переменных Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_KEY = os.getenv("TELEMETR_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
client = OpenAI(api_key=OPENAI_KEY)

def get_telemetr_data(post_url):
    """Запрос к API Телеметра для получения охвата и репостов"""
    # В реальном коде тут будет запрос: requests.get(f"https://api.telemetr.me/post/info?url={post_url}...")
    # Пока сделаем заглушку, имитирующую ответ API
    return {"views": 15000, "reposts": 120, "err": 5.4}

async def analyze_with_gpt(data_summary):
    """Отправка данных в OpenAI для поиска 'акцентов'"""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты эксперт Digital PR. Найди в данных аномалии и виральные успехи."},
            {"role": "user", "content": f"Сравни план и факт посевов: {data_summary}"}
        ]
    )
    return response.choices[0].message.content

@dp.message_handler(content_types=['document'])
async def handle_docs(message: types.Message):
    # Качаем файл
    file_id = message.document.file_id
    file = await bot.get_file(file_id)
    await file.download(destination_file="mp.xlsx")
    
    await message.answer("🔄 Вижу медиаплан. Начинаю обход постов в Telemetr...")

    # Читаем Excel (твой формат МП)
    df = pd.read_excel("mp.xlsx")
    
    # Логика: берем ссылки и прогноз охвата
    # (Колонки C и G из твоего примера)
    results = []
    for index, row in df.iterrows():
        url = row['Ссылка на канал'] # Позже заменим на прямую ссылку на пост
        plan_views = row['Прогноз охвата 1 поста']
        
        if pd.isna(url): continue
        
        # Идем в Телеметр
        fact = get_telemetr_data(url)
        
        diff = ((fact['views'] - plan_views) / plan_views) * 100
        results.append(f"Канал: {url}\nПлан: {plan_views}, Факт: {fact['views']} ({diff:+.1f}%)")

    # Формируем 'акценты' через GPT
    summary_text = "\n".join(results)
    accents = await analyze_with_gpt(summary_text)
    
    await message.answer(f"📊 **Результаты анализа:**\n\n{summary_text}\n\n💡 **Акценты для отчета:**\n{accents}")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
