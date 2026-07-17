"""
AI-слой для карточки: превращает сырые данные отчёта в CardData через OpenAI.

Задача модели — сделать «семантические» решения:
1. Выбрать заголовок (1-2 короткие строки, в стиле Кинопоиска).
2. Выбрать hero-число (обычно суммарный охват).
3. Сформулировать подзаголовок.
4. Выбрать какую площадку выделить как highlight.
5. Аккуратно сгруппировать/подписать площадки (обрезать длинные названия).
6. Сформулировать footer.

Fallback: если OpenAI недоступен или вернул мусор — собираем карточку
без ИИ по простой детерминированной логике (deterministic_compose).
"""

import json
import logging
from typing import Optional

from src.card.kinopoisk_card import CardData, CardRow

logger = logging.getLogger(__name__)


# --- Форматирование чисел --------------------------------------------------


def _format_number(n: Optional[int]) -> str:
    """142327 → '142 327' (неразрывный пробел не используем — SVG рендерит хорошо)."""
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", " ")


def _plural(n: int, one: str, few: str, many: str) -> str:
    """1 публикация / 2 публикации / 5 публикаций."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return few
    return many


def _pubs_word(n: int) -> str:
    """Возвращает '14 публикаций' с правильным падежом."""
    return f"{n} {_plural(n, 'публикация', 'публикации', 'публикаций')}"


# --- Агрегация по площадкам ------------------------------------------------


# Человекочитаемые названия платформ
PLATFORM_NAMES = {
    "vk":        "ВКонтакте",
    "telegram":  "Telegram",
    "instagram": "Instagram",
    "youtube":   "YouTube",
    "tiktok":    "TikTok",
    "twitter":   "X (Twitter)",
    "unknown":   "Прочее",
}


def _clean_channel_name(name: str, platform: str) -> str:
    """
    Приводит название канала к виду 'ВКонтакте — «Твои мужики»'.
    Если name — это ссылка (без имени), оставляем только платформу.
    """
    name = (name or "").strip()
    platform_label = PLATFORM_NAMES.get(platform, platform.capitalize())

    # name пустой или это ссылка → просто платформа
    if not name or name.startswith(("http://", "https://")):
        return platform_label

    # уже с платформой в начале — не дублируем
    if name.lower().startswith(platform_label.lower()):
        return name

    # красиво оборачиваем в ёлочки, если ещё не в кавычках
    if not (name.startswith(("«", '"', "«")) or name.startswith("@")):
        name = f"«{name}»"

    return f"{platform_label} — {name}"


def _aggregate_by_channel(posts_data: list[dict], max_rows: int = 5) -> list[dict]:
    """
    Группирует посты ПО КАНАЛУ (не по платформе): один канал = одна строка.

    Возвращает список: [{platform, channel, name, reach, count}, ...],
    отсортированный по reach убыв. Если каналов больше max_rows —
    остальные схлопываем в строку "Другие площадки".
    """
    # ключ = (platform, channel_name_normalized)
    agg: dict[tuple[str, str], dict] = {}
    for post in posts_data:
        platform = post.get("platform") or "unknown"
        raw_name = post.get("name") or ""
        stats = post.get("stats") or {}
        views = int(stats.get("views") or 0)

        # Ключ группировки: если у поста нет осмысленного имени (только url),
        # группируем по платформе. Иначе — по паре (platform, name).
        if not raw_name or raw_name.startswith(("http://", "https://")):
            key = (platform, "")
            display_name = _clean_channel_name("", platform)
        else:
            key = (platform, raw_name.lower())
            display_name = _clean_channel_name(raw_name, platform)

        entry = agg.setdefault(key, {
            "platform": platform,
            "channel": raw_name,
            "name": display_name,
            "reach": 0,
            "count": 0,
        })
        entry["reach"] += views
        entry["count"] += 1

    # Сортируем по reach убыв.
    all_rows = sorted(agg.values(), key=lambda x: x["reach"], reverse=True)

    if len(all_rows) <= max_rows:
        return all_rows

    # Иначе оставляем топ-N, остальное сворачиваем в "Другие площадки"
    top = all_rows[:max_rows - 1]
    rest = all_rows[max_rows - 1:]
    others_reach = sum(r["reach"] for r in rest)
    others_count = sum(r["count"] for r in rest)
    top.append({
        "platform": "other",
        "channel": "",
        "name": f"Другие площадки ({len(rest)})",
        "reach": others_reach,
        "count": others_count,
    })
    return top


# Обратная совместимость (если где-то ещё вызывается по старому имени)
def _aggregate_by_platform(posts_data: list[dict]) -> list[dict]:
    return _aggregate_by_channel(posts_data)


# --- Fallback без ИИ -------------------------------------------------------


def deterministic_compose(
    project_name: str,
    posts_data: list[dict],
    total_reach: int,
    total_posts: int,
) -> CardData:
    """
    Собирает CardData из данных отчёта без ИИ.
    Fallback, если OpenAI недоступен или упал.
    """
    agg = _aggregate_by_channel(posts_data)

    rows: list[CardRow] = []
    for i, e in enumerate(agg):
        rows.append(CardRow(
            name=e["name"],
            tag=_pubs_word(e["count"]),
            reach=_format_number(e["reach"]),
            highlight=(i == 0),   # первая (топовая) — с оранжевой засечкой
        ))

    # Заголовок — по проекту
    if project_name:
        title_lines = _split_title(project_name)
    else:
        title_lines = ["Итоги", "проекта"]

    return CardData(
        kicker=(project_name or "ОТЧЁТ").upper(),
        title_lines=title_lines,
        hero=_format_number(total_reach),
        subtitle=f"просмотров · {_pubs_word(total_posts)}",
        rows=rows,
        footer="Отчёт по проекту · охват фактический на момент выгрузки",
    )


def _split_title(name: str, max_len: int = 24) -> list[str]:
    """
    Разбивает название на 1-2 строки для карточки.
    Ищет удобное место разбиения по пробелам.
    """
    name = (name or "").strip()
    if not name:
        return []
    if len(name) <= max_len:
        return [name]
    # ищем пробел ближе к середине
    words = name.split()
    if len(words) == 1:
        return [name]

    mid = len(name) // 2
    best_split = None
    best_diff = 10**6
    running = 0
    for i, w in enumerate(words):
        running += len(w) + (1 if i > 0 else 0)
        diff = abs(running - mid)
        if diff < best_diff:
            best_diff = diff
            best_split = i + 1
        if running >= mid:
            break

    line1 = " ".join(words[:best_split])
    line2 = " ".join(words[best_split:])
    return [line1, line2][:2]


# --- Основной AI-слой ------------------------------------------------------


SYSTEM_PROMPT = """Ты — редактор Кинопоиска, готовишь итоговую карточку отчёта
о посевной кампании. Данные приходят в виде JSON: название проекта,
общий охват, разбивка по КАНАЛАМ (каждый канал = сообщество/страница/аккаунт
в социальной сети) с числом публикаций и охватами.

Твоя задача — сформулировать «медийный» заголовок и правильно собрать структуру.

⚠️ САМОЕ ВАЖНОЕ: НЕ ПРИДУМЫВАЙ ФАКТЫ.
- НЕ добавляй названия каналов/сообществ, которых нет в channels[].
- НЕ выдумывай тематику проекта, если она не следует из project_name.
- НЕ вписывай цитаты, мемы, аудитории и прочие детали, которых нет во входе.
- Названия каналов бери ДОСЛОВНО из channels[i].name — символ в символ.
  Даже если тебе кажется, что «ВКонтакте» стоило бы дописать —
  НЕ дописывай ничего от себя.

ПРАВИЛА:
1. Заголовок (1-2 строки) — короткий, ёмкий. Пример: "Итоги посева",
   "Результаты проекта", "Первая неделя проката" — если это следует
   из project_name. Если project_name пустой — пиши "Итоги посева".
2. Kicker — 2-4 слова капсом, обычно = project_name.upper().
   Если project_name пустой — "ОТЧЁТ".
3. Hero — общий охват в человекочитаемом виде: "142 327". Возьми total_reach
   и просто отформатируй числом через пробел.
4. Subtitle — техническая строка вроде "просмотров · 57 публикаций".
5. Разбивка (rows): один канал = одна строка. Порядок как в channels[]
   (уже отсортирован по reach убыв.). Выбери 1 самый сильный канал (первый)
   и пометь его highlight=true. Остальные — highlight=false.
6. name в rows — БЕРИ ДОСЛОВНО из channels[i].name.
7. Tag под названием — просто число публикаций с правильным падежом:
   "14 публикаций", "1 публикация", "21 публикация".
   Значение бери из channels[i].publications.
8. Число охвата (reach) — форматируй через пробел, например "72 115".
   Значение бери из channels[i].reach.
9. Footer — короткая техническая строка, например:
   "Отчёт по проекту · охват фактический на момент выгрузки".

ФОРМАТ ОТВЕТА — строго валидный JSON без комментариев:
{
  "kicker": "СТРОКА КАПСОМ",
  "title_lines": ["строка 1", "строка 2"],
  "hero": "142 327",
  "subtitle": "просмотров · 57 публикаций",
  "rows": [
    {"name": "ВКонтакте — «Твои мужики»", "tag": "14 публикаций", "reach": "72 115", "highlight": true},
    {"name": "Telegram", "tag": "19 публикаций", "reach": "19 075", "highlight": false}
  ],
  "footer": "Отчёт по проекту · охват фактический на момент выгрузки"
}

Не пиши ничего кроме JSON. Не оборачивай в ```json ...```. Только сырой JSON."""


async def compose_card_ai(
    project_name: str,
    posts_data: list[dict],
    total_reach: int,
    total_posts: int,
    openai_client=None,
    model: str = "gpt-4o-mini",
) -> Optional[CardData]:
    """
    Пробует собрать CardData через OpenAI. Возвращает None если что-то пошло не так.
    """
    if openai_client is None:
        try:
            from src.analyzer.openai_analyzer import client as _c
            openai_client = _c
        except Exception:
            logger.warning("compose_card_ai: no OpenAI client available")
            return None

    # Подготовка данных для модели
    agg = _aggregate_by_channel(posts_data)
    user_input = {
        "project_name": project_name or "",
        "total_reach": total_reach,
        "total_posts": total_posts,
        "channels": [
            {"name": e["name"], "publications": e["count"], "reach": e["reach"]}
            for e in agg
        ],
    }

    try:
        resp = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_input, ensure_ascii=False)},
            ],
            temperature=0.6,
            timeout=30,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content)
    except Exception as e:
        logger.warning(f"compose_card_ai failed: {e}")
        return None

    # Валидация и построение CardData
    try:
        rows_raw = parsed.get("rows") or []
        rows = [
            CardRow(
                name=str(r.get("name", "")),
                tag=r.get("tag"),
                reach=str(r.get("reach", "")),
                highlight=bool(r.get("highlight", False)),
            )
            for r in rows_raw
        ]
        return CardData(
            kicker=str(parsed.get("kicker", "")),
            title_lines=list(parsed.get("title_lines") or [])[:2],
            hero=str(parsed.get("hero", "")),
            subtitle=str(parsed.get("subtitle", "")),
            rows=rows,
            footer=str(parsed.get("footer", "")),
        )
    except Exception as e:
        logger.warning(f"compose_card_ai: bad JSON structure: {e}")
        return None


async def compose_card(
    project_name: str,
    posts_data: list[dict],
    total_reach: int,
    total_posts: int,
) -> CardData:
    """
    Главная точка входа. Пробует ИИ, при неудаче собирает детерминированно.
    Никогда не падает — всегда возвращает валидный CardData.
    """
    try:
        card = await compose_card_ai(project_name, posts_data, total_reach, total_posts)
        if card is not None:
            return card
    except Exception as e:
        logger.warning(f"compose_card: AI path failed, falling back: {e}")

    return deterministic_compose(project_name, posts_data, total_reach, total_posts)


# --- Свободный текст → CardData через ИИ -----------------------------------

FREETEXT_SYSTEM_PROMPT = """Ты — редактор карточки для брендового отчёта Кинопоиска.
Пользователь присылает СВОБОДНЫЙ ТЕКСТ: может быть кусок отчёта, таблица,
разрозненные факты, ссылки, комментарии. Твоя задача — извлечь из этого
структуру карточки и вернуть JSON.

⚠️ САМОЕ ВАЖНОЕ: НЕ ПРИДУМЫВАЙ ФАКТЫ.
- НЕ добавляй числа, которых нет в тексте.
- НЕ выдумывай названия каналов/сообществ.
- Если в тексте нет каких-то данных — оставляй поле пустым или значение null.
- Если пользователь дал очень мало данных — верни карточку с тем что есть.

ЧТО ИЩЕМ В ТЕКСТЕ:
1. Название проекта / тему → в kicker (капсом).
2. Общее число (охват / просмотры / показы / вовлечение) — самое большое
   значимое число проекта → в hero.
3. Заголовок 1-2 строки — короткая формулировка того что произошло:
   «Волна мемов в фанатских сообществах», «Итоги посева», «Первая неделя проката».
4. Подзаголовок — что за число в hero: «просмотров», «показов», «вовлечений».
   Плюс общее число публикаций если есть: «просмотров · 57 публикаций».
5. Разбивка по каналам/площадкам — искать пары «название → число».
   Часто идут списком, таблицей, «в ВК получили X просмотров», «Telegram — Y»,
   «канал А: N постов, M охват» и т.п. Одна пара = одна строка (row).
   name — как в тексте (не переводи, не сокращай, не добавляй кавычки если их нет).
   tag — если есть число публикаций/постов → «14 публикаций», «21 публикация»
          с правильным падежом; иначе null.
   reach — число справа, форматируй с пробелами: «72 115».
   highlight — TRUE для самой сильной строки (максимальный reach) или для той,
               которую пользователь явно выделил в тексте ключевыми словами
               («главная», «ключевая», «топ», «выиграла», выделено жирным).
6. Footer — короткая техническая строка. Пример: «Отчёт по проекту · охват
   фактический на момент выгрузки».

ПРАВИЛА ФОРМАТА:
- Числа в reach и hero — с пробелами через каждые 3 цифры: «142 327», «1 200 000».
- title_lines: 1 или 2 элемента строкой, каждая до ~30 символов.
- kicker: капсом, до ~30 символов.
- rows: до 5 штук. Если в тексте больше — оставь топ-5 по reach, остальное игнорируй.
- Если что-то не понял или данных нет — не выдумывай, поставь null или "".

ФОРМАТ ОТВЕТА — строго валидный JSON:
{
  "kicker": "АНИМЕ НА КИНОПОИСКЕ",
  "title_lines": ["Волна мемов", "в фанатских сообществах"],
  "hero": "142 327",
  "subtitle": "просмотров · 57 публикаций",
  "rows": [
    {"name": "ВКонтакте — «Твои мужики»", "tag": "14 публикаций", "reach": "72 115", "highlight": true},
    {"name": "X (Twitter)", "tag": "21 публикация", "reach": "42 439", "highlight": false}
  ],
  "footer": "Отчёт по проекту · охват фактический на момент выгрузки"
}

Не пиши ничего кроме JSON. Не оборачивай в ```json ...```. Только сырой JSON."""


async def compose_card_from_text(
    text: str,
    openai_client=None,
    model: str = "gpt-4o-mini",
) -> Optional[CardData]:
    """
    Разбирает свободный текст пользователя и превращает в CardData через OpenAI.
    Возвращает None, если OpenAI недоступен или вернул мусор.

    Логи детальны — можно понять что пошло не так, если карточка получилась странной.
    """
    text = (text or "").strip()
    if not text:
        logger.info("compose_card_from_text: empty input")
        return None

    if openai_client is None:
        try:
            from src.analyzer.openai_analyzer import client as _c
            openai_client = _c
        except Exception:
            logger.warning("compose_card_from_text: no OpenAI client available")
            return None

    try:
        resp = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": FREETEXT_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            timeout=45,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content)
        logger.info(f"compose_card_from_text: got JSON ({len(content)} chars)")
    except Exception as e:
        logger.warning(f"compose_card_from_text failed: {e}")
        return None

    try:
        rows_raw = parsed.get("rows") or []
        rows: list[CardRow] = []
        for r in rows_raw:
            name = str(r.get("name", ""))
            reach = str(r.get("reach", ""))
            if not (name or reach):
                continue

            # Плюрализация «публикация/публикации/публикаций» — не доверяем модели,
            # исправляем самостоятельно, если тэг похож на "X публик..."
            tag = r.get("tag") or None
            if tag and isinstance(tag, str):
                tag = _fix_publications_plural(tag)

            rows.append(CardRow(
                name=name,
                tag=tag,
                reach=reach,
                highlight=bool(r.get("highlight", False)),
            ))

        return CardData(
            kicker=str(parsed.get("kicker", "") or ""),
            title_lines=[
                s for s in (parsed.get("title_lines") or [])
                if s
            ][:2],
            hero=str(parsed.get("hero", "") or ""),
            subtitle=str(parsed.get("subtitle", "") or ""),
            rows=rows,
            footer=str(parsed.get("footer", "") or ""),
        )
    except Exception as e:
        logger.warning(f"compose_card_from_text: bad JSON structure: {e}")
        return None


def _fix_publications_plural(tag: str) -> str:
    """
    Если tag имеет вид «N публик...», приводит к правильному падежу.
    Иначе возвращает как есть.
    """
    import re
    m = re.match(r"^\s*(\d+)\s+публик[а-яё]*\b(.*)$", tag, flags=re.IGNORECASE)
    if not m:
        return tag
    n = int(m.group(1))
    return _pubs_word(n) + m.group(2)


# --- Правки существующей карточки через ИИ --------------------------------

REVISE_SYSTEM_PROMPT = """Ты — редактор карточки отчёта Кинопоиска. Пользователь
показал тебе превью карточки (в виде JSON) и хочет что-то поправить свободным
текстом. Твоя задача — применить правки и вернуть обновлённую структуру карточки.

⚠️ ГЛАВНЫЕ ПРАВИЛА:
1. Если какое-то поле НЕ упомянуто в правках — оставь его как было в текущей
   структуре. Не переделывай то, что пользователь не просил менять.
2. Числа применяй буквально. Если пользователь пишет «145к», «145 тыс», «145 000» —
   всё это = 145 000. Форматирование чисел — через пробел каждые 3 разряда: «145 000».
3. Если пользователь просит убрать канал (например, «убери ВК Клипы») — удали
   соответствующий элемент из rows.
4. Если пользователь просит добавить канал — добавь новый элемент в rows.
   Если пользователь не указал охват для нового канала — поставь reach="0".
5. highlight=true может быть только у ОДНОЙ строки одновременно.
   Если пользователь явно выделяет другую строку — переключи highlight на неё,
   у остальных сделай false.
6. Плюрализация тэгов «X публикаций»: 1 → «1 публикация», 2/3/4 → «2 публикации»,
   21 → «21 публикация», 5-20 → «X публикаций».
7. Если пользователь прислал ПОЛНОСТЬЮ НОВЫЙ набор данных (новый проект,
   всё новое) — верни новую структуру целиком, не пытайся мержить со старой.
8. НЕ ПРИДУМЫВАЙ факты, которых нет ни в текущей структуре, ни в правках.

⚠️ ЕСЛИ ПРАВКА НЕПОНЯТНА — верни JSON вида {"error": "коротко чего не понял"}.
НЕ возвращай молча старую структуру: это будет выглядеть будто ты применил правку,
хотя не применил.

ФОРМАТ ОТВЕТА — строго валидный JSON без комментариев, одна из двух форм:

Успех:
{
  "kicker": "...",
  "title_lines": ["...", "..."],
  "hero": "...",
  "subtitle": "...",
  "rows": [
    {"name": "...", "tag": "...", "reach": "...", "highlight": true},
    {"name": "...", "tag": "...", "reach": "...", "highlight": false}
  ],
  "footer": "..."
}

Ошибка (правка непонятна):
{"error": "не понял, какую цифру ты хочешь заменить"}

Не пиши ничего кроме JSON. Не оборачивай в ```json ...```."""


async def revise_card_from_text(
    current: CardData,
    edits_text: str,
    openai_client=None,
    model: str = "gpt-4o-mini",
) -> Optional[CardData]:
    """
    Принимает текущую карточку и свободный текст правок. Возвращает новую
    CardData с применёнными изменениями или None, если модель:
      - не смогла разобрать правку,
      - вернула JSON вида {"error": "..."},
      - ответила чем-то, что не парсится.

    В случае None вызывающий код должен показать пользователю сообщение
    «не понял правку», ОСТАВИВ текущую карточку в state.
    """
    edits_text = (edits_text or "").strip()
    if not edits_text:
        logger.info("revise_card_from_text: empty edits")
        return None

    if openai_client is None:
        try:
            from src.analyzer.openai_analyzer import client as _c
            openai_client = _c
        except Exception:
            logger.warning("revise_card_from_text: no OpenAI client available")
            return None

    # Сериализуем текущую карточку в JSON — как модель её потом и вернёт.
    from dataclasses import asdict
    current_json = json.dumps(asdict(current), ensure_ascii=False)

    user_message = (
        f"ТЕКУЩАЯ СТРУКТУРА (JSON):\n{current_json}\n\n"
        f"ПРАВКИ ОТ ПОЛЬЗОВАТЕЛЯ:\n{edits_text}"
    )

    try:
        resp = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": REVISE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
            timeout=45,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content)
        logger.info(f"revise_card_from_text: got JSON ({len(content)} chars)")
    except Exception as e:
        logger.warning(f"revise_card_from_text failed: {e}")
        return None

    # Модель отдала {"error": "..."} — правка непонятна.
    if isinstance(parsed, dict) and parsed.get("error"):
        logger.info(f"revise_card_from_text: model returned error: {parsed['error']}")
        return None

    try:
        rows_raw = parsed.get("rows") or []
        rows: list[CardRow] = []
        for r in rows_raw:
            name = str(r.get("name", ""))
            reach = str(r.get("reach", ""))
            if not (name or reach):
                continue

            tag = r.get("tag") or None
            if tag and isinstance(tag, str):
                tag = _fix_publications_plural(tag)

            rows.append(CardRow(
                name=name,
                tag=tag,
                reach=reach,
                highlight=bool(r.get("highlight", False)),
            ))

        return CardData(
            kicker=str(parsed.get("kicker", "") or ""),
            title_lines=[
                s for s in (parsed.get("title_lines") or [])
                if s
            ][:2],
            hero=str(parsed.get("hero", "") or ""),
            subtitle=str(parsed.get("subtitle", "") or ""),
            rows=rows,
            footer=str(parsed.get("footer", "") or ""),
        )
    except Exception as e:
        logger.warning(f"revise_card_from_text: bad JSON structure: {e}")
        return None


def build_picture_data_block(
    project_name: str,
    posts_data: list[dict],
    total_reach: int,
) -> str:
    """
    Формирует компактный текстовый блок с данными для команды /picture.
    Пользователь копирует этот блок и отправляет в /picture — получает карточку.

    Формат специально нейтрально-читабельный: одинаково удобен и человеку,
    и ИИ-парсеру внутри compose_card_from_text.
    """
    lines: list[str] = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Данные для карточки (/picture)")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if project_name:
        lines.append(f"Проект: {project_name}")

    total_posts = len([p for p in posts_data if p])
    lines.append(f"Итог: {_format_number(total_reach)} просмотров · {_pubs_word(total_posts)}")
    lines.append("")

    agg = _aggregate_by_channel(posts_data)
    if agg:
        lines.append("По каналам:")
        for row in agg:
            lines.append(
                f"• {row['name']} — {_pubs_word(row['count'])}, {_format_number(row['reach'])}"
            )

    lines.append("")
    lines.append("Скопируй сообщение → отправь в /picture → получишь карточку.")
    return "\n".join(lines)


def format_preview(card: CardData) -> str:
    """
    Отдаёт человекочитаемое превью содержимого карточки — чтобы показать
    пользователю ДО рендера. Формат — обычный текст (без Markdown), безопасный
    для отправки в Telegram.
    """
    lines: list[str] = []
    if card.kicker:
        lines.append(f"Плашка: {card.kicker}")
    if card.title_lines:
        lines.append(f"Заголовок: {' / '.join(card.title_lines)}")
    if card.hero:
        subtitle = f" ({card.subtitle})" if card.subtitle else ""
        lines.append(f"Главное число: {card.hero}{subtitle}")
    if card.rows:
        lines.append("")
        lines.append("Разбивка:")
        for i, r in enumerate(card.rows, 1):
            star = " ★" if r.highlight else ""
            tag_str = f" ({r.tag})" if r.tag else ""
            lines.append(f"  {i}. {r.name}{tag_str} — {r.reach}{star}")
    if card.footer:
        lines.append("")
        lines.append(f"Подпись снизу: {card.footer}")
    if not lines:
        return "Не удалось выделить структуру из текста."
    return "\n".join(lines)
