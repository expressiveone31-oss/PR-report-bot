import os
import pandas as pd
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from openai import OpenAI

# 1. Загрузка ключей (убедись, что добавила их в Variables на Railway)
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_KEY = os.getenv("TELEMETR_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
openai_client = OpenAI(api_key=OPENAI_KEY)

def get_telemetr_stats(post_url):
    """
    Запрос к Telemetr API. 
    Если API еще не подключен или ссылка не ТГ — возвращает заглушку для теста.
    """
    if not TELEMETR_KEY or "t.me" not in str(post_url):
        return {"views": 5000, "reposts": 10} # Заглушка для теста
    
    # Пример реального запроса (раскомментировать при наличии ключа)
    # response = requests.get(f"https://api.telemetr.me/post/info?url={post_url}", 
    #                         headers={"Authorization": f"Bearer {TELEMETR_KEY}"})
    # return response.json()
    return {"views": 12500, "reposts": 45}

async def get_pr_accents(raw_data):
    """Отправляем данные в OpenAI для генерации сочных акцентов"""
    prompt = f"""
    Ты — топовый Digital PR менеджер. Твоя задача — найти 'акценты' для отчета на основе данных.
    Данные (План vs Факт и Органика):
    {raw_data}
    
    Выдели:
    1. Где произошло самое мощное перевыполнение плана (в %).
    2. Какие посты лучше всего разлетелись (репосты).
    3. Оцени вклад органики (бесплатных постов).
    Напиши это профессиональным, но живым языком, как в крутых презентациях.
    """
    
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.answer("Привет! Пришли мне Excel-медиаплан, и я вытащу из него акценты для отчета.")

@dp.message_handler(content_types=['document'])
async def handle_mp(message: types.Message):
    await message.answer("📥 Принял файл. Начинаю магию аналитики...")
    
    # Сохраняем файл
    file_path = "current_mp.xlsx"
    await message.document.download(destination_file=file_path)
    
    try:
        # Читаем Excel (пропускаем шапку, если нужно, или читаем как есть)
        df = pd.read_excel(file_path)
        
        paid_summary = []
        organic_summary = []
        
        # Перебираем строки
        for index, row in df.iterrows():
            # Названия колонок из твоего файла
            url = row.get('Ссылка на публикацию')
            plan_views = row.get('Планируемый охват')
            
            if pd.isna(url) or "t.me" not in str(url):
                continue
                
            stats = get_telemetr_stats(url)
            
            # Логика: если есть план — это платный посев, если нет — органика
            if pd.notna(plan_views) and plan_views > 0:
                diff = ((stats['views'] - plan_views) / plan_views) * 100
                paid_summary.append(f"Пост: {url}\n- План: {plan_views}, Факт: {stats['views']} ({diff:+.1f}%)")
            else:
                organic_summary.append(f"Органика: {url}\n- Охват: {stats['views']}, Репосты: {stats['reposts']}")

        # Собираем всё в один текст для нейронки
        full_report_text = "ПЛАТНЫЕ РАЗМЕЩЕНИЯ:\n" + "\n".join(paid_summary)
        full_report_text += "\n\nОРГАНИЧЕСКИЕ ПОСТЫ:\n" + "\n".join(organic_summary)
        
        # Генерируем выводы через GPT
        accents = await get_pr_accents(full_report_text)
        
        await message.answer(f"✅ **Анализ готов!**\n\n{accents}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при чтении файла: {e}")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
