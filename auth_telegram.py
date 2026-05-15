"""
Запусти этот скрипт ОДИН РАЗ для авторизации Telegram-аккаунта.
После этого файл session/userbot.session будет создан и бот
сможет парсить комментарии автоматически.

Запуск:
  python3 auth_telegram.py
"""

import asyncio
import os
from pyrogram import Client
from src.config import PYROGRAM_API_ID, PYROGRAM_API_HASH

SESSION_PATH = os.path.join("session", "userbot")


async def main():
    os.makedirs("session", exist_ok=True)
    print("Авторизация Telegram-аккаунта для парсинга комментариев.")
    print("Введи номер телефона в формате +79991234567\n")

    async with Client(
        name=SESSION_PATH,
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
    ) as app:
        me = await app.get_me()
        print(f"\nУспешно! Авторизован как: {me.first_name} (@{me.username})")
        print(f"Файл сессии сохранён: {SESSION_PATH}.session")
        print("\nТеперь можно запускать бота — python3 main.py")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
