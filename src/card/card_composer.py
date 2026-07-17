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
