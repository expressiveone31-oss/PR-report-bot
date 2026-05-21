"""
Telegram-бот для генерации акцентов отчётов Digital PR.
Диалоговый режим — без МП, только ссылки и несколько вопросов.
"""

import asyncio
import logging
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

from src.config import TELEGRAM_BOT_TOKEN
from src.orchestrator import process_links, process_mediaplan



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="Markdown"),
)
dp = Dispatcher(storage=MemoryStorage())


class ReportStates(StatesGroup):
    waiting_csv_or_links  = State()   # первый шаг: CSV или ссылки
    waiting_paid_links    = State()
    waiting_mediaplan_csv = State()   # опциональный CSV с планом по каждому посту
    waiting_planned_reach = State()
    waiting_budget        = State()
    waiting_organic       = State()
    waiting_project_name  = State()


def extract_links(text: str) -> list[str]:
    """Вытаскивает все http-ссылки из текста."""
    return re.findall(r'https?://\S+', text)


def parse_number(text: str) -> float | None:
    """
    Парсит число из строки — поддерживает форматы:
      275000 / 275 000 / 275 000,00 / 585 343₽ / 585 343,00 ₽
    Убирает: пробелы обычные и неразрывные, ₽, $, €, табы.
    Запятую трактует как десятичный разделитель (европейский формат).
    """
    # Убираем все символы кроме цифр, точки и запятой
    cleaned = re.sub(r'[^\d.,]', '', text)
    if not cleaned:
        return None
    # Если есть и точка и запятая — значит одна из них разделитель тысяч
    # Формат 1.234,56 → запятая десятичная
    # Формат 1,234.56 → точка десятичная
    if '.' in cleaned and ',' in cleaned:
        if cleaned.rfind('.') < cleaned.rfind(','):
            # 1.234,56 — убираем точку, заменяем запятую на точку
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            # 1,234.56 — убираем запятую
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        # Только запятая — десятичный разделитель (275 000,00 → 275000.00)
        # Но если запятых несколько — это разделитель тысяч (1,000,000)
        if cleaned.count(',') > 1:
            cleaned = cleaned.replace(',', '')
        else:
            # Одна запятая: проверяем сколько цифр после неё
            parts = cleaned.split(',')
            if len(parts[1]) <= 2:
                # 275000,00 — десятичная
                cleaned = cleaned.replace(',', '.')
            else:
                # 275,000 — разделитель тысяч
                cleaned = cleaned.replace(',', '')
    try:
        return float(cleaned)
    except ValueError:
        return None


def send_long(text: str, chunk_size: int = 4000) -> list[str]:
    """Разбивает длинный текст на части."""
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]


@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Помогу сформулировать акценты для отчёта Digital PR.\n\n"
        "Скинь *CSV-файл медиаплана* — я извлеку всё сам.\n\n"
        "Или напиши *ссылки* вручную — тогда проведу тебя по шагам.\n\n"
        "Поддерживаю: VK, Telegram, Instagram, Twitter/X"
    )
    await state.set_state(ReportStates.waiting_csv_or_links)


# ШАГ 0а — получили CSV медиаплана
@dp.message(ReportStates.waiting_csv_or_links, F.document)
async def got_csv_mediaplan(message: Message, state: FSMContext) -> None:
    doc = message.document
    if not doc.file_name.lower().endswith(".csv"):
        await message.answer("Нужен файл в формате CSV. Попробуй снова или напиши ссылки вручную.")
        return

    await message.answer("Читаю медиаплан...")

    file = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file.file_path)
    content = file_bytes.read().decode("utf-8-sig")

    # Название проекта из имени файла
    project_name = os.path.splitext(doc.file_name)[0].strip()

    try:
        from src.parsers.universal_mp import parse_mediaplan_auto
        mp, schema = await parse_mediaplan_auto(content)
    except Exception as e:
        logger.error(f"MP parse error: {e}", exc_info=True)
        await message.answer(
            f"Не удалось разобрать МП: {e}\n\nПопробуй скинуть ссылки вручную — напиши /start"
        )
        return

    if not mp.paid_posts:
        await message.answer(
            "Не нашёл paid-постов в МП. Проверь файл или скинь ссылки вручную (/start)."
        )
        return

    # Показываем что нашли — для проверки перед запуском
    paid_count = len(mp.paid_posts)
    organic_count = len(mp.organic_posts)
    total_plan = mp.total_planned_reach
    total_budget = mp.total_budget

    summary_lines = [
        f"Проект: *{project_name}*",
        f"Paid-постов: *{paid_count}*  |  Органика: *{organic_count}* постов",
        f"Плановый охват: *{total_plan:,}*",
        f"Бюджет размещений: *{total_budget:,.0f} ₽*",
        "",
        "Paid-посты из МП:",
    ]
    for p in mp.paid_posts:
        summary_lines.append(f"• {p.name} — план {p.planned_reach:,} — {p.post_url}")

    await message.answer("\n".join(summary_lines))
    await message.answer("Иду за данными через API — это займёт до минуты...")

    try:
        result = await process_mediaplan(mp, project_name=project_name)
    except Exception as e:
        logger.error(f"Processing error: {e}", exc_info=True)
        await message.answer("Ошибка при сборе данных. Попробуй ещё раз (/start).")
        return

    await state.clear()
    for chunk in send_long(result):
        await message.answer(chunk, parse_mode=None)


# ШАГ 0б — получили текст (ссылки) вместо CSV
@dp.message(ReportStates.waiting_csv_or_links)
async def got_links_instead_of_csv(message: Message, state: FSMContext) -> None:
    links = extract_links(message.text or "")
    if links:
        await state.update_data(paid_links=links)
        await message.answer(
            f"Принял *{len(links)}* ссылок.\n\n"
            "Хочешь сравнивать каждый пост с его плановым охватом? "
            "Тогда скинь CSV медиаплана.\n\n"
            "Если не нужно — напиши *нет*."
        )
        await state.set_state(ReportStates.waiting_mediaplan_csv)
    else:
        await message.answer(
            "Не нашёл ни ссылок, ни CSV-файла.\n\n"
            "Скинь CSV медиаплана или ссылки на paid-посты (начинаются с https://)."
        )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Сброшено. Напиши /start чтобы начать заново.")


# ШАГ 1 — Paid ссылки
@dp.message(ReportStates.waiting_paid_links)
async def got_paid_links(message: Message, state: FSMContext) -> None:
    links = extract_links(message.text or "")
    if not links:
        await message.answer("Не нашёл ни одной ссылки. Скинь ссылки на посты (начинаются с https://)")
        return

    await state.update_data(paid_links=links)
    await message.answer(
        f"Принял *{len(links)}* ссылок.\n\n"
        "Хочешь чтобы я сравнивал каждый пост с его плановым охватом? "
        "Тогда скинь CSV-выгрузку медиаплана.\n\n"
        "Если не нужно — напиши *нет*, и я буду сравнивать только общий план."
    )
    await state.set_state(ReportStates.waiting_mediaplan_csv)


# ШАГ 1.5 — Опциональный CSV медиаплана
@dp.message(ReportStates.waiting_mediaplan_csv, F.document)
async def got_mediaplan_csv(message: Message, state: FSMContext) -> None:
    doc = message.document
    if not doc.file_name.lower().endswith(".csv"):
        await message.answer("Нужен файл в формате CSV. Попробуй снова или напиши *нет* чтобы пропустить.")
        return

    file = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file.file_path)
    content = file_bytes.read().decode("utf-8-sig")

    # Парсим план по каждому посту из CSV
    from src.parsers.mediaplan import parse_csv
    try:
        mp = parse_csv(content)
        # Строим маппинг: ссылка на пост → плановый охват
        plan_by_url = {
            p.post_url.strip(): p.planned_reach
            for p in mp.paid_posts
            if p.post_url and p.planned_reach
        }
        await state.update_data(plan_by_url=plan_by_url)
        await message.answer(
            f"Принял медиаплан — нашёл план по *{len(plan_by_url)}* постам.\n\n"
            "Какой был *плановый охват* по проекту в целом? (число)"
        )
    except Exception as e:
        logger.error(f"CSV parse error: {e}")
        await message.answer("Не удалось разобрать CSV. Продолжим без плана по постам.\n\nКакой был *плановый охват* по проекту? (число)")

    await state.set_state(ReportStates.waiting_planned_reach)


@dp.message(ReportStates.waiting_mediaplan_csv)
async def skip_mediaplan_csv(message: Message, state: FSMContext) -> None:
    await state.update_data(plan_by_url={})
    await message.answer("Хорошо, пропускаем.\n\nКакой был *плановый охват* по проекту? (число)")
    await state.set_state(ReportStates.waiting_planned_reach)


# ШАГ 2 — Плановый охват
@dp.message(ReportStates.waiting_planned_reach)
async def got_planned_reach(message: Message, state: FSMContext) -> None:
    planned = parse_number(message.text or "")
    if planned is None:
        await message.answer("Не понял число. Напиши плановый охват цифрой, например: 275000 или 275 000")
        return
    planned = int(planned)

    await state.update_data(planned_reach=planned)
    await message.answer("Какой *бюджет на размещения* (₽)? Напиши цифрой — только стоимость постов, без менеджмента и доп. расходов.")
    await state.set_state(ReportStates.waiting_budget)


# ШАГ 3 — Бюджет
@dp.message(ReportStates.waiting_budget)
async def got_budget(message: Message, state: FSMContext) -> None:
    budget = parse_number(message.text or "")
    if budget is None:
        await message.answer("Не понял число. Напиши бюджет цифрой, например: 560343 или 560 343 ₽")
        return

    await state.update_data(budget=budget)
    await message.answer(
        "Есть *органика*?\n\n"
        "• Если да — скинь ссылки или напиши суммарный охват цифрой\n"
        "• Если нет — напиши *нет*"
    )
    await state.set_state(ReportStates.waiting_organic)


# ШАГ 4 — Органика
@dp.message(ReportStates.waiting_organic)
async def got_organic(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()

    organic_links = []
    organic_reach_manual = None

    if text.lower() in ("нет", "no", "-", "0"):
        pass
    else:
        # Пробуем распарсить как число
        parsed = parse_number(text)
        try:
            organic_reach_manual = int(parsed) if parsed is not None else None
        except (TypeError, ValueError):
            organic_reach_manual = None
        if organic_reach_manual is None:
            # Пробуем как ссылки
            organic_links = extract_links(text)
            if not organic_links:
                await message.answer(
                    "Не понял. Скинь ссылки на органические посты, суммарный охват цифрой, или напиши *нет*"
                )
                return

    await state.update_data(
        organic_links=organic_links,
        organic_reach_manual=organic_reach_manual,
    )

    # ШАГ 5 — Название проекта
    await message.answer("Как называется проект? (для отчёта)")
    await state.set_state(ReportStates.waiting_project_name)


# ШАГ 5 — Название проекта → запуск
@dp.message(ReportStates.waiting_project_name)
async def got_project_name(message: Message, state: FSMContext) -> None:
    project_name = (message.text or "").strip() or "Без названия"
    data = await state.get_data()
    await state.clear()

    paid_links = data.get("paid_links", [])
    organic_links = data.get("organic_links", [])
    organic_reach_manual = data.get("organic_reach_manual")
    planned_reach = data.get("planned_reach", 0)
    budget = data.get("budget", 0.0)
    plan_by_url = data.get("plan_by_url", {})

    organic_str = (
        f"ссылки ({len(organic_links)} шт.)" if organic_links
        else f"{organic_reach_manual:,} просмотров" if organic_reach_manual
        else "нет"
    )
    plan_str = f"по {len(plan_by_url)} постам из МП" if plan_by_url else "только общий"

    await message.answer(
        f"Проект: *{project_name}*\n"
        f"Paid постов: *{len(paid_links)}*\n"
        f"Органика: *{organic_str}*\n"
        f"План: *{plan_str}*\n\n"
        f"Иду за данными через API — это займёт до минуты..."
    )

    try:
        result, total_actual, breakdown = await process_links(
            paid_links=paid_links,
            organic_links=organic_links,
            organic_reach_manual=organic_reach_manual,
            planned_reach=planned_reach,
            budget=budget,
            project_name=project_name,
            plan_by_url=plan_by_url,
        )
    except Exception as e:
        logger.error(f"Processing error: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка при сборе данных. Попробуй ещё раз (/start) или проверь ссылки."
        )
        return

    # Сначала показываем разбивку по каждому посту — для диагностики
    paid_rows = [b for b in breakdown if not b["is_organic"]]
    organic_rows = [b for b in breakdown if b["is_organic"]]
    paid_sum = sum(b["views"] for b in paid_rows)

    lines = [f"Фактический охват по данным API: {total_actual:,}", "", "Разбивка paid по постам:"]
    for b in paid_rows:
        err = f"  ⚠️ {b['error']}" if b.get("error") else ""
        src = " [tgstat]" if b.get("tgstat_fallback") else ""
        lines.append(f"• {b['views']:,}{src} — {b['url']}{err}")
    lines.append(f"Итого paid: {paid_sum:,}")
    if organic_rows:
        lines.append("")
        lines.append("Органика:")
        for b in organic_rows:
            err = f"  ⚠️ {b['error']}" if b.get("error") else ""
            src = " [tgstat]" if b.get("tgstat_fallback") else ""
            lines.append(f"• {b['views']:,}{src} — {b['url']}{err}")

    await message.answer("\n".join(lines), parse_mode=None)

    # Затем акценты — отправляем без Markdown чтобы избежать ошибок парсинга
    for chunk in send_long(result):
        await message.answer(chunk, parse_mode=None)


# Если пишут текст вне диалога
@dp.message()
async def handle_other(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer(
            "Напиши /start чтобы начать формировать акценты для отчёта."
        )


async def main() -> None:
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
