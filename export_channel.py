"""
Экспорт постов канала @ohmykinopoisk с сентября 2025 по сегодня.

Выгружает:
  - Текст поста (подводка)
  - Дата публикации
  - Просмотры
  - Реакции (общее кол-во + разбивка по типам)
  - Комментарии (кол-во)
  - Пересылки
  - Ссылка на пост

Результат сохраняется в export_ohmykinopoisk.csv

Запуск:
  python3 export_channel.py

Безопасный режим: пауза 2-4 сек между запросами.
При FloodWait — автоматически ждёт сколько скажет Telegram.
"""

import asyncio
import csv
import random
import os
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import FloodWait
from src.config import PYROGRAM_API_ID, PYROGRAM_API_HASH

CHANNEL = "ohmykinopoisk"
DATE_FROM = datetime(2025, 9, 1)
OUTPUT_FILE = "export_ohmykinopoisk.csv"
SESSION_PATH = os.path.join("session", "userbot")

# Пауза между запросами (секунды) — безопасный диапазон
DELAY_MIN = 2.0
DELAY_MAX = 4.0


def format_reactions(message) -> tuple[int, str]:
    """Возвращает (total_count, breakdown) для реакций."""
    if not message.reactions:
        return 0, ""
    total = 0
    parts = []
    for r in message.reactions.reactions:
        total += r.count
        emoji = r.emoji if hasattr(r, 'emoji') and r.emoji else "?"
        parts.append(f"{emoji}:{r.count}")
    return total, " ".join(parts)


async def main():
    print(f"Начинаю экспорт канала @{CHANNEL}")
    print(f"Период: с {DATE_FROM.strftime('%d.%m.%Y')} по сегодня")
    print(f"Результат будет сохранён в: {OUTPUT_FILE}\n")

    posts = []

    async with Client(
        name=SESSION_PATH,
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
    ) as app:
        print("Авторизация успешна, начинаю сбор постов...\n")

        count = 0
        skipped = 0

        async for message in app.get_chat_history(CHANNEL):
            # Пропускаем служебные сообщения без текста
            if not message.text and not message.caption:
                skipped += 1
                continue

            # Останавливаемся если дошли до сентября 2025
            msg_date = message.date.replace(tzinfo=None) if message.date.tzinfo else message.date
            if msg_date < DATE_FROM:
                print(f"\nДошли до {message.date.strftime('%d.%m.%Y')} — останавливаюсь.")
                break

            text = (message.text or message.caption or "").strip()
            views = message.views or 0
            forwards = message.forwards or 0

            reactions_total, reactions_breakdown = format_reactions(message)

            # Кол-во комментариев — если есть дискуссия
            comments_count = 0
            try:
                if hasattr(message, 'replies') and message.replies:
                    comments_count = message.replies.replies or 0
            except Exception:
                pass

            post_url = f"https://t.me/{CHANNEL}/{message.id}"
            date_str = message.date.strftime("%d.%m.%Y %H:%M")

            posts.append({
                "date": date_str,
                "post_url": post_url,
                "views": views,
                "reactions_total": reactions_total,
                "reactions_breakdown": reactions_breakdown,
                "comments": comments_count,
                "forwards": forwards,
                "text": text,
            })

            count += 1
            if count % 10 == 0:
                print(f"  Собрано: {count} постов... (последний: {date_str})")

            # Безопасная пауза
            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    if not posts:
        print("Постов не найдено.")
        return

    # Сохраняем в CSV
    fieldnames = ["date", "post_url", "views", "reactions_total",
                  "reactions_breakdown", "comments", "forwards", "text"]

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(posts)

    print(f"\nГотово! Экспортировано {len(posts)} постов.")
    print(f"Файл сохранён: {OUTPUT_FILE}")

    # Краткая статистика
    total_views = sum(p["views"] for p in posts)
    total_reactions = sum(p["reactions_total"] for p in posts)
    avg_views = total_views // len(posts) if posts else 0
    print(f"\nКраткая статистика:")
    print(f"  Постов: {len(posts)}")
    print(f"  Суммарные просмотры: {total_views:,}")
    print(f"  Среднее просмотров на пост: {avg_views:,}")
    print(f"  Суммарные реакции: {total_reactions:,}")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
