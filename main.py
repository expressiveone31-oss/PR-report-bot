"""
Telegram-бот для генерации акцентов отчётов Digital PR.
Диалоговый режим — без МП, только ссылки и несколько вопросов.
"""

import asyncio
import logging
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)
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


class UpdateStates(StatesGroup):
    waiting_xlsx = State()   # ждём xlsx для обновления охватов


class PictureStates(StatesGroup):
    """FSM для команды /picture — генерация карточки из свободного текста."""
    waiting_text    = State()   # шаг 1: ждём текст с данными
    waiting_confirm = State()   # шаг 2: показали превью, ждём подтверждения


def _detect_mp_type(content: str) -> str:
    """
    Определяет тип МП: 'target' или 'posev'.
    Таргет — есть колонки CPM, % отказа, CPC, нет ссылок на публикации.
    Посев — есть ссылки на публикации t.me/vk.com/instagram.com.
    """
    content_lower = content.lower()
    # Признаки таргет-МП
    target_signals = sum([
        "cpm" in content_lower,
        "% отказа" in content_lower or "отказ" in content_lower,
        "cpc" in content_lower,
        "показы" in content_lower,
        "переходы" in content_lower,
    ])
    # Признаки посевного МП
    posev_signals = sum([
        "t.me/" in content_lower,
        "vk.com/wall" in content_lower or "vk.ru/wall" in content_lower,
        "instagram.com/p/" in content_lower or "instagram.com/reel/" in content_lower,
        "ссылка на публикацию" in content_lower,
        "охват (факт)" in content_lower,
    ])
    return "target" if target_signals >= 3 and target_signals > posev_signals else "posev"


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





@dp.message(Command("testcomments"))
async def cmd_test_comments(message: Message, state: FSMContext) -> None:
    """Временная команда для тестирования telegram92 API с разными паузами."""
    import time
    from src.platforms.telegram_comments import get_post_comments
    test_urls = [
        "https://t.me/movierls/20410",
        "https://t.me/nmshhub/56444",
        "https://t.me/topor/50101",
    ]
    for i, url in enumerate(test_urls):
        if i > 0:
            pause = 10  # 10 секунд между запросами для теста
            await message.answer(f"Пауза {pause} сек перед следующим запросом...", parse_mode=None)
            await asyncio.sleep(pause)
        t0 = time.time()
        result = await get_post_comments(url, limit=3)
        elapsed = time.time() - t0
        if result.error:
            await message.answer(f"❌ ({elapsed:.1f}s) {url}\nОшибка: {result.error}", parse_mode=None)
        else:
            lines = [f"✓ ({elapsed:.1f}s) {url}\nПолучено: {len(result.top_comments)}, всего: {result.total_count}"]
            for j, c in enumerate(result.top_comments, 1):
                lines.append(f"{j}. {c[:100]}")
            await message.answer("\n".join(lines), parse_mode=None)


@dp.message(Command("update"))
async def cmd_update(message: Message, state: FSMContext) -> None:
    """Режим обновления охватов в xlsx."""
    await state.clear()
    await message.answer(
        "Скинь *Excel-файл (.xlsx)* с медиапланом или *ссылку* на него.\n\n"
        "Что понимаю по ссылке:\n"
        "• Google Sheets — нужен открытый доступ по ссылке (Читатель)\n"
        "• Яндекс.Диск — публичная ссылка на файл\n"
        "• Прямая ссылка на .xlsx или .csv\n\n"
        "Я пройдусь по ссылкам на публикации через API и проставлю актуальные охваты прямо в таблицу.\n\n"
        "Верну обновлённый файл с пометкой *\\_updated* в имени.",
        parse_mode="Markdown",
    )
    await state.set_state(UpdateStates.waiting_xlsx)


async def _process_update_bytes(message: Message, state: FSMContext,
                                 raw_bytes: bytes, fname: str) -> None:
    """
    Общая логика обновления охватов: принимает готовые bytes + предполагаемое имя,
    возвращает пользователю обновлённый файл.
    """
    # Если CSV — конвертируем в xlsx чтобы вернуть xlsx
    if fname.lower().endswith(".csv"):
        try:
            import openpyxl
            import csv, io as _io
            wb = openpyxl.Workbook()
            ws = wb.active
            reader = csv.reader(_io.StringIO(raw_bytes.decode("utf-8-sig")))
            for row in reader:
                ws.append(row)
            buf = _io.BytesIO()
            wb.save(buf)
            raw_bytes = buf.getvalue()
            fname = fname.replace(".csv", ".xlsx")
        except Exception as e:
            await message.answer(f"Не удалось конвертировать CSV: {e}", parse_mode=None)
            return

    try:
        from src.updater.xlsx_updater import update_xlsx
        updated_bytes, stats = await update_xlsx(raw_bytes)
    except Exception as e:
        logger.error(f"xlsx update error: {e}", exc_info=True)
        await message.answer(f"Ошибка при обновлении: {e}", parse_mode=None)
        return

    # Формируем имя файла с _updated
    base = os.path.splitext(fname)[0]
    # Убираем лишние пробелы и лишние точки
    base = base.strip() or "mediaplan"
    out_fname = f"{base}_updated.xlsx"

    await state.clear()
    await message.answer(
        f"Готово!\n\n"
        f"🟡 Жёлтый ({stats['updated']}) — охват обновлён, пост существует\n"
        f"🌸 Розовый ({stats['deleted']}) — пост удалён, проставлен последний известный охват\n"
        f"🔴 Красный ({stats['errors']}) — нет данных (API не ответил или платформа недоступна)",
        parse_mode=None,
    )
    from aiogram.types import BufferedInputFile
    await message.answer_document(
        BufferedInputFile(updated_bytes, filename=out_fname),
    )


@dp.message(UpdateStates.waiting_xlsx, F.document)
async def got_xlsx_for_update(message: Message, state: FSMContext) -> None:
    doc = message.document
    fname = doc.file_name or ""
    if not (fname.lower().endswith(".xlsx") or fname.lower().endswith(".csv")):
        await message.answer("Нужен файл .xlsx или .csv. Попробуй ещё раз.", parse_mode=None)
        return

    await message.answer(
        "Читаю файл и иду за данными через API. Это может занять несколько минут...",
        parse_mode=None,
    )

    file = await bot.get_file(doc.file_id)
    file_bytes_io = await bot.download_file(file.file_path)
    raw_bytes = file_bytes_io.read()

    await _process_update_bytes(message, state, raw_bytes, fname)


@dp.message(UpdateStates.waiting_xlsx, F.text)
async def got_url_for_update(message: Message, state: FSMContext) -> None:
    """Обработка ссылки на МП (Google Sheets, Яндекс.Диск, прямая xlsx/csv)."""
    from src.parsers.url_downloader import is_supported_url, download_from_url

    text = (message.text or "").strip()
    if not text:
        await message.answer(
            "Скинь файл .xlsx / .csv или ссылку на медиаплан.",
            parse_mode=None,
        )
        return

    # Если это не ссылка — сообщаем и ждём дальше
    if not text.startswith(("http://", "https://")):
        await message.answer(
            "Жду файл .xlsx / .csv или ссылку.\n"
            "Ссылка должна начинаться с http:// или https://\n\n"
            "Или напиши /update чтобы начать заново.",
            parse_mode=None,
        )
        return

    if not is_supported_url(text):
        await message.answer(
            "Не понимаю эту ссылку. Поддерживаю:\n"
            "• Google Sheets\n"
            "• Яндекс.Диск\n"
            "• Прямую ссылку на .xlsx или .csv",
            parse_mode=None,
        )
        return

    await message.answer("Скачиваю файл по ссылке...", parse_mode=None)

    result = await download_from_url(text)
    if result.error:
        await message.answer(f"Не удалось скачать файл: {result.error}", parse_mode=None)
        return

    if not result.file_bytes:
        await message.answer("Скачал пустой файл. Проверь ссылку.", parse_mode=None)
        return

    fname = result.filename or "mediaplan.xlsx"
    logger.info(
        f"Downloaded from {result.source}: {len(result.file_bytes)} bytes, fname={fname}"
    )

    await message.answer(
        f"Файл получен ({len(result.file_bytes) // 1024} KB). "
        "Иду за данными через API. Это может занять несколько минут...",
        parse_mode=None,
    )

    await _process_update_bytes(message, state, result.file_bytes, fname)


@dp.message(UpdateStates.waiting_xlsx)
async def update_wrong_file(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Жду файл .xlsx / .csv или ссылку на медиаплан. "
        "Или напиши /update чтобы начать заново.",
        parse_mode=None,
    )


@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветствие + меню. Никакого автозапуска — пользователь сам выбирает команду."""
    await state.clear()
    await message.answer(
        "Привет! Помогу подготовить аналитику по прошедшему проекту.\n\n"
        "*Собрать отчёт* — /sumup\n"
        "*Обновить охваты в таблице* — /update\n"
        "*Собрать карточку из текста* — /picture\n\n"
        "Поддерживаю: VK, Telegram, Instagram, YouTube, TikTok, Twitter/X"
    )


@dp.message(Command("sumup"))
async def cmd_sumup(message: Message, state: FSMContext) -> None:
    """Запуск сбора отчёта — принимает CSV/Excel медиаплана или список ссылок."""
    await state.clear()
    await message.answer(
        "Ок, собираем отчёт.\n\n"
        "Скинь *CSV или Excel медиаплан*, либо напиши ссылки на посты вручную.",
        parse_mode="Markdown",
    )
    await state.set_state(ReportStates.waiting_csv_or_links)


# ШАГ 0а — получили CSV медиаплана
@dp.message(ReportStates.waiting_csv_or_links, F.document)
async def got_csv_mediaplan(message: Message, state: FSMContext) -> None:
    doc = message.document
    fname = doc.file_name.lower()
    if not (fname.endswith(".csv") or fname.endswith(".xlsx")):
        await message.answer("Нужен файл в формате CSV или Excel (.xlsx). Попробуй снова или напиши ссылки вручную.", parse_mode=None)
        return

    await message.answer("Читаю медиаплан...", parse_mode=None)

    file = await bot.get_file(doc.file_id)
    file_bytes_io = await bot.download_file(file.file_path)
    raw_bytes = file_bytes_io.read()

    # Название проекта из имени файла
    project_name = os.path.splitext(doc.file_name)[0].strip()

    # Конвертируем xlsx → csv если нужно
    if fname.endswith(".xlsx"):
        try:
            from src.parsers.xlsx_to_csv import xlsx_bytes_to_csv
            content, sheet_name = xlsx_bytes_to_csv(raw_bytes)
            logger.info(f"xlsx converted: sheet='{sheet_name}', len={len(content)}")
        except Exception as e:
            logger.error(f"xlsx conversion error: {e}", exc_info=True)
            await message.answer(f"Не удалось прочитать Excel-файл: {e}", parse_mode=None)
            return
    else:
        content = raw_bytes.decode("utf-8-sig")

    # Автодетекция типа МП: таргет или посев
    mp_type = _detect_mp_type(content)
    logger.info(f"MP type detected: {mp_type}")

    if mp_type == "target":
        # Таргет/перфоманс МП — без API-запросов
        from src.parsers.target_mp import parse_target_mp
        from src.analyzer.target_analyzer import analyze_target_campaign

        try:
            target_mp = parse_target_mp(content, project_name=project_name)
        except Exception as e:
            logger.error(f"Target MP parse error: {e}", exc_info=True)
            await message.answer(f"Не удалось разобрать таргет-МП: {e}", parse_mode=None)
            return

        if not target_mp.channel_rows:
            await message.answer(
                "Не нашёл фактических данных в МП — похоже это прогнозный план.\n\n"
                "Скинь МП с заполненными колонками «Факт».",
                parse_mode=None,
            )
            return

        summary_lines = [
            f"Проект: {project_name}",
            f"Тип: таргет/перфоманс кампания",
            f"Каналов: {len(target_mp.channel_rows)}",
            "",
            "Каналы:",
        ]
        for r in target_mp.channel_rows:
            summary_lines.append(f"• {r.channel}" + (f" / {r.target[:50]}" if r.target else ""))

        await message.answer("\n".join(summary_lines), parse_mode=None)
        await message.answer("Формирую отчёт...", parse_mode=None)

        try:
            result = await analyze_target_campaign(target_mp)
        except Exception as e:
            logger.error(f"Target analysis error: {e}", exc_info=True)
            await message.answer("Ошибка при анализе. Попробуй ещё раз (/sumup).", parse_mode=None)
            return

        await state.clear()
        for chunk in send_long(result):
            await message.answer(chunk, parse_mode=None)
        return

    # Посевной МП — с API-запросами
    try:
        from src.parsers.universal_mp import parse_mediaplan_auto
        mp, schema = await parse_mediaplan_auto(content)
    except Exception as e:
        logger.error(f"MP parse error: {e}", exc_info=True)
        await message.answer(
            f"Не удалось разобрать МП: {e}\n\nПопробуй скинуть ссылки вручную — напиши /sumup",
            parse_mode=None,
        )
        return

    if not mp.paid_posts:
        await message.answer(
            "Не нашёл paid-постов в МП. Проверь файл или скинь ссылки вручную (/sumup).",
            parse_mode=None,
        )
        return

    paid_count = len(mp.paid_posts)
    organic_count = len(mp.organic_posts)
    total_plan = mp.total_planned_reach
    total_budget = mp.total_budget

    summary_lines = [
        f"Проект: {project_name}",
        f"Paid-постов: {paid_count}  |  Органика: {organic_count} постов",
        f"Плановый охват: {total_plan:,}",
        f"Бюджет размещений: {total_budget:,.0f} руб.",
        "",
        "Paid-посты из МП:",
    ]
    for p in mp.paid_posts:
        summary_lines.append(f"• {p.name} — план {p.planned_reach:,} — {p.post_url}")

    await message.answer("\n".join(summary_lines), parse_mode=None)
    await message.answer("Иду за данными через API — это займёт до минуты...", parse_mode=None)

    try:
        result = await process_mediaplan(mp, project_name=project_name)
    except Exception as e:
        logger.error(f"Processing error: {e}", exc_info=True)
        await message.answer("Ошибка при сборе данных. Попробуй ещё раз (/sumup).", parse_mode=None)
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
    await message.answer("Сброшено. Напиши /sumup чтобы начать сбор отчёта заново.")


# ============================================================================
# КАРТОЧКА — /picture
# ============================================================================

def _picture_confirm_kb() -> InlineKeyboardMarkup:
    """Inline-клавиатура для превью карточки: подтвердить / отменить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сгенерить", callback_data="picture:confirm"),
            InlineKeyboardButton(text="✖️ Отмена", callback_data="picture:cancel"),
        ],
    ])


@dp.message(Command("picture"))
async def cmd_picture(message: Message, state: FSMContext) -> None:
    """Запуск режима генерации брендовой карточки из свободного текста."""
    await state.clear()
    await message.answer(
        "Ок, соберём карточку.\n\n"
        "Скинь текст с данными: цифры, названия каналов/площадок, "
        "заголовок проекта. Формат — любой. Можно кусок отчёта, "
        "таблицу, буллиты — разберусь.\n\n"
        "Пример:\n"
        "Аниме на Кинопоиске — 142 327 просмотров, 57 публикаций.\n"
        "ВКонтакте «Твои мужики» — 14 постов, 72 115.\n"
        "X (Twitter) — 21 пост, 42 439.\n"
        "Telegram — 19 постов, 19 075.\n\n"
        "Отменить — /cancel",
    )
    await state.set_state(PictureStates.waiting_text)


@dp.message(PictureStates.waiting_text, F.text)
async def got_picture_text(message: Message, state: FSMContext) -> None:
    """
    Получили текст. Прогоняем через ИИ, показываем превью с кнопками
    подтвердить / отменить.
    """
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        # если пришла команда — не считаем это данными
        await message.answer("Скинь текст с данными для карточки, или /cancel чтобы выйти.")
        return

    await message.answer("Разбираю текст, готовлю превью...")

    from src.card import compose_card_from_text, format_preview
    try:
        card = await compose_card_from_text(text)
    except Exception as e:
        logger.error(f"compose_card_from_text failed: {e}", exc_info=True)
        card = None

    if card is None:
        await message.answer(
            "Не удалось разобрать текст. Попробуй сформулировать иначе — "
            "укажи хотя бы общее число, заголовок и разбивку по каналам."
        )
        return

    preview = format_preview(card)

    # Сохраняем разобранный card в state и просим подтверждение
    # CardData — dataclass, сериализуем в dict для FSM хранилища
    from dataclasses import asdict
    await state.update_data(card=asdict(card))
    await state.set_state(PictureStates.waiting_confirm)

    await message.answer(
        "Вот что понял:\n\n" + preview + "\n\nСгенерить карточку?",
        reply_markup=_picture_confirm_kb(),
    )


@dp.callback_query(PictureStates.waiting_confirm, F.data == "picture:cancel")
async def picture_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if cb.message:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.message.answer("Отменено. Напиши /picture чтобы начать заново.")
    await cb.answer()


@dp.callback_query(PictureStates.waiting_confirm, F.data == "picture:confirm")
async def picture_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    """Пользователь подтвердил превью — рендерим и отправляем карточку."""
    data = await state.get_data()
    card_dict = data.get("card")
    if not card_dict:
        await cb.answer("Данные потеряны, попробуй /picture заново", show_alert=True)
        await state.clear()
        return

    # Ретайпим CardData
    from src.card import CardData, CardRow, render_card
    try:
        rows = [CardRow(**r) for r in (card_dict.get("rows") or [])]
        card = CardData(
            kicker=card_dict.get("kicker", ""),
            title_lines=list(card_dict.get("title_lines") or []),
            hero=card_dict.get("hero", ""),
            subtitle=card_dict.get("subtitle", ""),
            rows=rows,
            footer=card_dict.get("footer", ""),
            breakdown_label=card_dict.get("breakdown_label") or "РАЗБИВКА ПО ПЛОЩАДКАМ",
            reach_label=card_dict.get("reach_label") or "ОХВАТ",
        )
    except Exception as e:
        logger.error(f"picture_confirm: failed to restore CardData: {e}", exc_info=True)
        await cb.answer("Данные испорчены, попробуй /picture заново", show_alert=True)
        await state.clear()
        return

    if cb.message:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.message.answer("Рендерю карточку...")

    try:
        png_bytes = render_card(card, scale=1.5)
    except Exception as e:
        logger.error(f"picture_confirm: render_card failed: {e}", exc_info=True)
        if cb.message:
            await cb.message.answer(
                f"Не удалось нарисовать карточку: {type(e).__name__}. "
                "Возможно на сервере не хватает библиотеки cairo — напиши админу."
            )
        await state.clear()
        await cb.answer()
        return

    if cb.message:
        photo = BufferedInputFile(png_bytes, filename="card.png")
        await cb.message.answer_photo(photo)

    await state.clear()
    await cb.answer("Готово")


@dp.message(PictureStates.waiting_confirm)
async def picture_confirm_wrong_input(message: Message, state: FSMContext) -> None:
    """Если пользователь пишет текст вместо клика по кнопке."""
    await message.answer(
        "Нажми одну из кнопок под превью, или /cancel чтобы выйти.",
    )


@dp.message(PictureStates.waiting_text)
async def picture_text_wrong_input(message: Message, state: FSMContext) -> None:
    """Если пришло что-то не текстовое (документ, фото и т.п.)."""
    await message.answer(
        "Жду текст с данными для карточки. Или напиши /cancel.",
    )


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
            "Произошла ошибка при сборе данных. Попробуй ещё раз (/sumup) или проверь ссылки."
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
            "Напиши /sumup чтобы начать аналитику по прошедшему проекту."
        )


async def _set_bot_commands() -> None:
    """Регистрирует меню команд в Telegram (иконка «/» рядом с полем ввода)."""
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="sumup",   description="Собрать отчёт по прошедшему проекту"),
        BotCommand(command="update",  description="Обновить фактические охваты в МП"),
        BotCommand(command="picture", description="Собрать брендовую карточку из текста"),
        BotCommand(command="help",    description="Помощь / список команд"),
    ])


async def main() -> None:
    logger.info("Bot starting...")
    try:
        await _set_bot_commands()
    except Exception as e:
        logger.warning(f"Failed to set bot commands menu: {e}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
