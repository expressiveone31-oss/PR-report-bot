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


def _aggregate_by_platform(posts_data: list[dict]) -> list[dict]:
    """
    Группирует посты по платформам, считает total views и кол-во публикаций.
    Возвращает список: [{platform, name, reach, count}, ...] отсортированный по reach.
    """
    agg: dict[str, dict] = {}
    for post in posts_data:
        platform = post.get("platform") or "unknown"
        stats = post.get("stats") or {}
        views = stats.get("views") or 0

        entry = agg.setdefault(platform, {"platform": platform, "reach": 0, "count": 0})
        entry["reach"] += int(views or 0)
        entry["count"] += 1

    # человекочитаемое имя + сортировка по reach
    result = []
    for platform, e in agg.items():
        e["name"] = PLATFORM_NAMES.get(platform, platform.capitalize())
        result.append(e)
    result.sort(key=lambda x: x["reach"], reverse=True)
    return result


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
    agg = _aggregate_by_platform(posts_data)

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
общий охват, разбивка по площадкам с числом публикаций и охватами.

Твоя задача — сформулировать «медийный» заголовок и правильно собрать структуру.

⚠️ САМОЕ ВАЖНОЕ: НЕ ПРИДУМЫВАЙ ФАКТЫ.
- НЕ добавляй названия каналов/сообществ, которые не переданы в данных.
- НЕ выдумывай тематику проекта, если она не следует из project_name.
- НЕ вписывай цитаты, мемы, аудитории и прочие детали, которых нет во входе.
- Если данных мало — заголовок должен быть общим и нейтральным.

ПРАВИЛА:
1. Заголовок (1-2 строки) — короткий, ёмкий. Пример: "Итоги посева",
   "Результаты проекта", "Первая неделя проката" — если это следует
   из project_name. Если project_name пустой — пиши "Итоги посева".
2. Kicker — 2-4 слова капсом, обычно = project_name.upper().
   Если project_name пустой — "ОТЧЁТ".
3. Hero — общий охват в человекочитаемом виде: "142 327". Возьми total_reach
   и просто отформатируй числом через пробел.
4. Subtitle — техническая строка вроде "просмотров · 57 публикаций".
5. Разбивка (rows): выбери 1 самую сильную площадку (максимальный reach)
   и пометь её highlight=true. Остальные — highlight=false.
6. Названия площадок в rows — БЕРИ ТОЧНО из входа (поле platforms[i].name).
   НЕ дополняй никакими «Твои мужики», «@channel» и т.п.
7. Tag под названием — просто число публикаций с правильным падежом:
   "14 публикаций", "1 публикация", "21 публикация".
8. Число охвата (reach) — форматируй как в hero, через пробел: "72 115".
9. Footer — короткая техническая строка, например:
   "Отчёт по проекту · охват фактический на момент выгрузки".

ФОРМАТ ОТВЕТА — строго валидный JSON без комментариев:
{
  "kicker": "СТРОКА КАПСОМ",
  "title_lines": ["строка 1", "строка 2"],
  "hero": "142 327",
  "subtitle": "просмотров · 57 публикаций",
  "rows": [
    {"name": "ВКонтакте", "tag": "14 публикаций", "reach": "72 115", "highlight": true},
    {"name": "X (Twitter)", "tag": "21 публикация", "reach": "42 439", "highlight": false}
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
    agg = _aggregate_by_platform(posts_data)
    user_input = {
        "project_name": project_name or "",
        "total_reach": total_reach,
        "total_posts": total_posts,
        "platforms": [
            {"name": e["name"], "count": e["count"], "reach": e["reach"]}
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
