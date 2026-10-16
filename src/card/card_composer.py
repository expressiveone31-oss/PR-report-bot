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
import re
from typing import Optional

from src.card.kinopoisk_card import CardData, CardRow
from src.card.yandex_music_cover import YandexMusicCoverData, YandexMusicMetric

logger = logging.getLogger(__name__)

CAMPAIGN_CARD_HEADER = "ДАННЫЕ ДЛЯ КАРТОЧКИ: ИТОГИ ПОСЕВА"

# Фасад карточки (и Кинопоиск, и Яндекс Музыка) — всегда эти блоки:
# 1) Итоги посева — суммарные просмотры (hero)
# 2) Органика — число постов + просмотры
# 3) Выполнение paid-плана — %
# 4) Итоговый CPV
# 5) Самая охватная публикация — канал + просмотры


# --- Форматирование чисел --------------------------------------------------


def _format_number(n: Optional[int]) -> str:
    """142327 → '142 327' (неразрывный пробел не используем — SVG рендерит хорошо)."""
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", " ")


def _format_pct(value: float) -> str:
    """150.0 → '150%'."""
    if value is None:
        return ""
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))}%"
    return f"{value:.1f}".replace(".", ",") + "%"


def _format_cpv(value: float) -> str:
    """0.47 → '0,47 ₽'."""
    if value is None or value <= 0:
        return ""
    return f"{value:.2f}".replace(".", ",") + " ₽"


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


def _post_views(post: dict) -> int:
    stats = post.get("stats") or {}
    views = stats.get("views")
    if views is None:
        views = post.get("views")
    try:
        return int(views or 0)
    except (TypeError, ValueError):
        return 0


def _post_channel_label(post: dict) -> str:
    name = str(post.get("name") or "").strip()
    if name and not name.startswith(("http://", "https://")):
        return name
    platform = post.get("platform") or "unknown"
    return PLATFORM_NAMES.get(platform, platform.capitalize())


def _find_top_post(posts_data: list[dict]) -> Optional[tuple[str, int]]:
    """Самая охватная публикация: (канал, просмотры)."""
    best_name = ""
    best_views = 0
    for post in posts_data or []:
        views = _post_views(post)
        if views > best_views:
            best_views = views
            best_name = _post_channel_label(post)
    if best_views <= 0:
        return None
    return best_name, best_views


def compute_facade_metrics(
    posts_data: list[dict],
    total_reach: int = 0,
    placement_budget: float = 0.0,
    planned_reach: int = 0,
) -> dict:
    """
    Считает единый набор показателей для фасада карточки.
    Совпадает по смыслу с report_v2.calculate_metrics (actual_cpv = budget / total).
    """
    posts = posts_data or []
    paid = [p for p in posts if not p.get("is_organic")]
    organic = [p for p in posts if p.get("is_organic")]
    paid_actual = sum(_post_views(p) for p in paid)
    organic_actual = sum(_post_views(p) for p in organic)
    total_actual = total_reach or (paid_actual + organic_actual)
    paid_plan = int(planned_reach or sum(int(p.get("planned_reach") or 0) for p in paid))
    completion_pct = (paid_actual / paid_plan * 100) if paid_plan else 0.0
    actual_cpv = (placement_budget / total_actual) if placement_budget and total_actual else 0.0
    top = _find_top_post(posts)
    return {
        "total_actual": int(total_actual),
        "organic_actual": int(organic_actual),
        "organic_posts": len(organic),
        "paid_actual": int(paid_actual),
        "paid_plan": int(paid_plan),
        "paid_completion_pct": float(completion_pct),
        "actual_cpv": float(actual_cpv),
        "top_channel": top[0] if top else "",
        "top_views": top[1] if top else 0,
    }


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

ЕСЛИ В ТЕКСТЕ ЕСТЬ ИТОГИ КАМПАНИИ / ФАСАДНЫЕ ПОКАЗАТЕЛИ:
На фасад ВСЕГДА выноси в таком порядке (пропускай только то, чего нет в тексте):
1) hero — суммарные просмотры («Итоги посева»);
2) row «ОРГАНИКА» — органические просмотры, в tag — число публикаций;
3) row «PAID-ПЛАН» — выполнение paid-плана в процентах;
4) row «CPV» — итоговый CPV;
5) row с highlight=true — самая охватная публикация (канал + охват).
Не подменяй этот фасад топом площадок. Используй breakdown_label
«КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ» и reach_label «РЕЗУЛЬТАТ».

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
  "footer": "Отчёт по проекту · охват фактический на момент выгрузки",
  "breakdown_label": "РАЗБИВКА ПО ПЛОЩАДКАМ",
  "reach_label": "ОХВАТ"
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

    campaign_card = _campaign_card_from_block(text)
    if campaign_card is not None:
        return campaign_card

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
            breakdown_label=str(parsed.get("breakdown_label", "") or "РАЗБИВКА ПО ПЛОЩАДКАМ"),
            reach_label=str(parsed.get("reach_label", "") or "ОХВАТ"),
        )
    except Exception as e:
        logger.warning(f"compose_card_from_text: bad JSON structure: {e}")
        return None


def _fix_publications_plural(tag: str) -> str:
    """
    Если tag имеет вид «N публик...», приводит к правильному падежу.
    Иначе возвращает как есть.
    """
    m = re.match(r"^\s*(\d+)\s+публик[а-яё]*\b(.*)$", tag, flags=re.IGNORECASE)
    if not m:
        return tag
    n = int(m.group(1))
    return _pubs_word(n) + m.group(2)


def _parse_campaign_values(text: str) -> Optional[dict[str, str]]:
    """Разбирает блок CAMPAIGN_CARD_HEADER в словарь полей."""
    if not text.startswith(CAMPAIGN_CARD_HEADER):
        return None
    values: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key == "Хайлайт":
            # Старый формат: «канал | охват | подпись»
            parts = [part.strip() for part in value.split("|")]
            if len(parts) >= 2 and not values.get("Топ-публикация"):
                values["Топ-публикация"] = parts[0]
                values["Топ-охват"] = parts[1]
            continue
        # Совместимость со старым ключом «Перевыполнение paid-плана»
        if key == "Перевыполнение paid-плана" and "Выполнение paid-плана" not in values:
            values["Выполнение paid-плана"] = value
            continue
        values[key] = value
    if not values.get("Общий охват"):
        return None
    return values


def _campaign_card_from_block(text: str) -> Optional[CardData]:
    """Кинопоиск-карточка с единым фасадом ключевых показателей."""
    values = _parse_campaign_values(text)
    if values is None:
        return None

    project_name = values.get("Проект", "Без названия")
    rows: list[CardRow] = []

    if values.get("Органический охват"):
        organic_posts_raw = values.get("Organic-публикации", "")
        try:
            organic_tag = _pubs_word(int(re.sub(r"\D", "", organic_posts_raw))) if organic_posts_raw else None
        except ValueError:
            organic_tag = None
        rows.append(CardRow("ОРГАНИКА", values["Органический охват"], organic_tag))

    paid_pct = values.get("Выполнение paid-плана") or ""
    if paid_pct:
        rows.append(CardRow("PAID-ПЛАН", paid_pct, "выполнение"))

    if values.get("CPV"):
        rows.append(CardRow("CPV", values["CPV"], "итоговый"))

    top_name = values.get("Топ-публикация", "")
    top_reach = values.get("Топ-охват", "")
    if top_name and top_reach:
        rows.append(CardRow(top_name, top_reach, "самая охватная", True))
    elif rows:
        rows[0].highlight = True

    return CardData(
        kicker="ИТОГИ ПОСЕВА",
        title_lines=_split_title(project_name),
        hero=values["Общий охват"],
        subtitle="просмотров",
        rows=rows,
        footer="Отчёт по проекту · охват фактический на момент выгрузки",
        breakdown_label="КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ",
        reach_label="РЕЗУЛЬТАТ",
    )


def _music_cover_from_campaign_values(values: dict[str, str]) -> YandexMusicCoverData:
    """Яндекс Музыка: тот же фасад, уложенный в hero + 3 метрики + хайлайт."""
    project_name = values.get("Проект", "Без названия")
    metrics: list[YandexMusicMetric] = []

    if values.get("Органический охват"):
        organic_posts_raw = values.get("Organic-публикации", "")
        try:
            detail = _pubs_word(int(re.sub(r"\D", "", organic_posts_raw))) if organic_posts_raw else ""
        except ValueError:
            detail = ""
        metrics.append(YandexMusicMetric("ОРГАНИКА", values["Органический охват"], detail))

    paid_pct = values.get("Выполнение paid-плана") or ""
    if paid_pct:
        metrics.append(YandexMusicMetric("PAID-ПЛАН", paid_pct, "выполнение"))

    if values.get("CPV"):
        metrics.append(YandexMusicMetric("CPV", values["CPV"], "итоговый"))

    top_name = values.get("Топ-публикация", "")
    top_reach = values.get("Топ-охват", "")
    highlight = ""
    if top_name and top_reach:
        highlight = f"{top_name} — {top_reach}"

    return YandexMusicCoverData(
        title_lines=_split_title(project_name, max_len=18),
        subtitle="Итоги посева",
        hero=values["Общий охват"],
        hero_label="просмотров",
        metrics=metrics[:3],
        highlight=highlight,
    )


def _music_cover_fallback(text: str) -> YandexMusicCoverData:
    lines = [line.strip("•- ") for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else "Яндекс Музыка"
    subtitle = lines[1] if len(lines) > 1 else ""
    subtitle = re.sub(r"(?i)\b(син(?:ий|его)|розов(?:ый|ого)|зелён(?:ый|ого))\s+фон\b", "", subtitle)
    subtitle = re.sub(r"(?i)\b(слева|по центру)\b", "", subtitle)
    subtitle = subtitle.strip(" ·,;-")
    return YandexMusicCoverData(
        title_lines=_split_title(title, max_len=14),
        subtitle=subtitle[:80],
        background="green" if "зелён" in text.lower() else "pink" if "розов" in text.lower() else "blue",
        align="left" if "слева" in text.lower() else "center",
    )


def _music_cover_from_legacy_picture_block(text: str) -> Optional[YandexMusicCoverData]:
    total_match = re.search(
        r"^Итог:\s*([\d\s]+)\s+просмотров\s*·\s*(\d+)\s+публикац",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not total_match:
        return None

    project_match = re.search(r"^Проект:\s*(.*?)(?=\s*Фон:|\n|$)", text, flags=re.IGNORECASE | re.MULTILINE)
    project = project_match.group(1).strip() if project_match else "Итоги посева"
    lowered = text.lower()
    background = "green" if "зелён" in lowered else "pink" if "розов" in lowered else "blue"
    channel_rows: list[tuple[str, int]] = []
    for name, reach in re.findall(
        r"^•\s*(.+?)\s+—\s+\d+\s+публикац[^,]*,\s*([\d\s]+)\s*$",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    ):
        if not name.casefold().startswith("другие площадки"):
            channel_rows.append((name.strip(), int(re.sub(r"\D", "", reach))))
    channel_rows.sort(key=lambda row: row[1], reverse=True)

    metrics = [YandexMusicMetric("ПУБЛИКАЦИИ", total_match.group(2), "вышли в рамках посева")]
    if channel_rows:
        metrics.append(YandexMusicMetric("ТОП-ПУБЛИКАЦИЯ", _format_number(channel_rows[0][1]), channel_rows[0][0]))
    if len(channel_rows) >= 3:
        metrics.append(YandexMusicMetric(
            "ТОП-3 ПЛОЩАДКИ",
            _format_number(sum(row[1] for row in channel_rows[:3])),
            "суммарный охват",
        ))

    return YandexMusicCoverData(
        title_lines=_split_title(project, max_len=18),
        subtitle="Итоги посева",
        background=background,
        align="left" if "слева" in lowered else "center",
        hero=_format_number(int(re.sub(r"\D", "", total_match.group(1)))),
        hero_label="просмотров",
        metrics=metrics,
        highlight=(
            f"{channel_rows[0][0]} — {_format_number(channel_rows[0][1])} просмотров"
            if channel_rows else ""
        ),
    )


def _music_cover_from_preview_text(text: str) -> Optional[YandexMusicCoverData]:
    """Recovers a Music infographic when a user pastes a prior text preview."""
    hero_match = re.search(r"^Главное число:\s*([\d\s]+)\s+(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    metrics_match = re.search(r"^Показатели:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if not hero_match or not metrics_match:
        return None

    project_match = re.search(
        r"^(?:Шаблон:\s*Яндекс Музыка\s*\n)?\s*(?:Заголовок:\s*)?(.+?)(?=\s*Подзаголовок:)",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    project = project_match.group(1).strip(" /\n") if project_match else "Итоги посева"
    subtitle_match = re.search(r"Подзаголовок:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    subtitle = subtitle_match.group(1).removeprefix("Подзаголовок:").strip() if subtitle_match else "Итоги посева"
    background_match = re.search(r"Фон:\s*(синий|розовый|зелёный)", text, flags=re.IGNORECASE)
    background = {
        "синий": "blue", "розовый": "pink", "зелёный": "green",
    }.get(background_match.group(1).lower(), "blue") if background_match else "blue"
    align = "left" if re.search(r"Выравнивание:\s*слева", text, flags=re.IGNORECASE) else "center"

    metrics = []
    for item in metrics_match.group(1).split(";")[:3]:
        label, separator, value = item.strip().partition("—")
        if separator and label.strip() and value.strip():
            metrics.append(YandexMusicMetric(label.strip(), value.strip(), ""))
    if not metrics:
        return None
    highlight_match = re.search(r"^Хайлайт:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    return YandexMusicCoverData(
        title_lines=_split_title(project, max_len=18),
        subtitle=subtitle,
        background=background,
        align=align,
        hero=_format_number(int(re.sub(r"\D", "", hero_match.group(1)))),
        hero_label=hero_match.group(2).strip(),
        metrics=metrics,
        highlight=highlight_match.group(1).strip() if highlight_match else "",
    )


async def compose_music_cover_from_text(
    text: str,
    openai_client=None,
    model: str = "gpt-4o-mini",
) -> YandexMusicCoverData:
    """Собирает обложку Яндекс Музыки полностью локально, без LLM."""
    del openai_client, model
    text = (text or "").strip()
    values = _parse_campaign_values(text)
    if values is not None:
        return _music_cover_from_campaign_values(values)

    return (
        _music_cover_from_preview_text(text)
        or _music_cover_from_legacy_picture_block(text)
        or _music_cover_fallback(text)
    )


def revise_music_cover_from_text(current: YandexMusicCoverData, edits_text: str) -> YandexMusicCoverData:
    """Применяет предсказуемые правки фона, выравнивания и подписей обложки."""
    text = (edits_text or "").strip()
    lowered = text.lower()
    background = (
        "green" if "зелён" in lowered
        else "pink" if "розов" in lowered
        else "blue" if "син" in lowered
        else current.background
    )
    align = (
        "left" if "слева" in lowered
        else "center" if "по центру" in lowered
        else current.align
    )
    title_match = re.search(r"заголовок\s*:\s*(.+)", text, flags=re.IGNORECASE)
    subtitle_match = re.search(r"подзаголовок\s*:\s*(.+)", text, flags=re.IGNORECASE)
    title_lines = (
        [line.strip() for line in title_match.group(1).split("/") if line.strip()][:3]
        if title_match else current.title_lines
    )
    subtitle = subtitle_match.group(1).strip()[:80] if subtitle_match else current.subtitle
    return YandexMusicCoverData(
        title_lines=title_lines,
        subtitle=subtitle,
        background=background,
        align=align,
        hero=current.hero,
        hero_label=current.hero_label,
        metrics=current.metrics,
        highlight=current.highlight,
    )


def format_music_cover_preview(cover: YandexMusicCoverData) -> str:
    background_names = {"blue": "синий", "pink": "розовый", "green": "зелёный"}
    return (
        f"Шаблон: Яндекс Музыка\n"
        f"Заголовок: {' / '.join(cover.title_lines)}\n"
        f"Подзаголовок: {cover.subtitle or 'нет'}\n"
        f"Фон: {background_names.get(cover.background, cover.background)}\n"
        f"Выравнивание: {'слева' if cover.align == 'left' else 'по центру'}"
        + (f"\nГлавное число: {cover.hero} {cover.hero_label}" if cover.hero else "")
        + (
            "\nПоказатели: "
            + "; ".join(f"{metric.name} — {metric.value}" for metric in cover.metrics)
            if cover.metrics else ""
        )
        + (f"\nХайлайт: {cover.highlight}" if cover.highlight else "")
    )


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
            breakdown_label=str(
                parsed.get("breakdown_label", "") or current.breakdown_label or "РАЗБИВКА ПО ПЛОЩАДКАМ"
            ),
            reach_label=str(parsed.get("reach_label", "") or current.reach_label or "ОХВАТ"),
        )
    except Exception as e:
        logger.warning(f"revise_card_from_text: bad JSON structure: {e}")
        return None


def build_picture_data_block(
    project_name: str,
    posts_data: list[dict],
    total_reach: int,
    placement_budget: float = 0.0,
    planned_reach: int = 0,
) -> str:
    """
    Формирует блок фасадных данных для /picture.
    Пользователь копирует блок → /picture → Кинопоиск или Яндекс Музыка.
    """
    facade = compute_facade_metrics(
        posts_data,
        total_reach=total_reach,
        placement_budget=placement_budget,
        planned_reach=planned_reach,
    )
    lines: list[str] = [
        CAMPAIGN_CARD_HEADER,
        f"Проект: {project_name or 'Без названия'}",
        f"Общий охват: {_format_number(facade['total_actual'])}",
    ]
    if facade["organic_actual"] or facade["organic_posts"]:
        lines.append(f"Органический охват: {_format_number(facade['organic_actual'])}")
        lines.append(f"Organic-публикации: {facade['organic_posts']}")
    if facade["paid_plan"]:
        lines.append(f"Paid-охват: {_format_number(facade['paid_actual'])}")
        lines.append(f"Paid-план: {_format_number(facade['paid_plan'])}")
        lines.append(f"Выполнение paid-плана: {_format_pct(facade['paid_completion_pct'])}")
    if facade["actual_cpv"]:
        lines.append(f"CPV: {_format_cpv(facade['actual_cpv'])}")
    if facade["top_channel"] and facade["top_views"]:
        lines.append(f"Топ-публикация: {facade['top_channel']}")
        lines.append(f"Топ-охват: {_format_number(facade['top_views'])}")

    lines.append("")
    lines.append("Скопируй сообщение → /picture → выбери шаблон → получишь карточку.")
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
