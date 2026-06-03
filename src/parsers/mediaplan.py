"""
Парсер CSV медиаплана.

Ожидаемые колонки (paid-блок):
  Название, Ссылка, Планируемый охват, Общая стоимость с АК 15%,
  Планируемый CPV, Площадка, Дата, Ссылка на публикацию,
  Охват (факт), Факт CPV, Скрины публикации

Органика живёт в правой части таблицы — колонка со ссылками
и следующая за ней с охватом.
"""

import csv
import re
import io
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Post:
    name: str
    channel_url: str
    platform: str                     # vk | telegram | instagram | twitter
    post_url: str
    planned_reach: int = 0
    actual_reach: Optional[int] = None
    cost: Optional[float] = None
    planned_cpv: Optional[float] = None
    actual_cpv: Optional[float] = None
    is_organic: bool = False
    date: Optional[str] = None


@dataclass
class MediaPlan:
    project_name: str = ""
    paid_posts: list[Post] = field(default_factory=list)
    organic_posts: list[Post] = field(default_factory=list)
    mp_total_actual_reach: Optional[int] = None  # итог из строки "Итого с органикой" в МП

    @property
    def total_planned_reach(self) -> int:
        return sum(p.planned_reach for p in self.paid_posts)

    @property
    def total_actual_reach(self) -> int:
        # Приоритет — зафиксированный итог из МП, иначе сумма постов
        if self.mp_total_actual_reach:
            return self.mp_total_actual_reach
        return sum(p.actual_reach or 0 for p in self.paid_posts + self.organic_posts)

    @property
    def total_budget(self) -> float:
        return sum(p.cost or 0 for p in self.paid_posts)


def _detect_platform(url: str) -> str:
    url = url.lower()
    if "vk.com" in url or "vk.ru" in url:
        return "vk"
    if "t.me" in url or "telegram" in url:
        return "telegram"
    if "instagram.com" in url:
        return "instagram"
    if "x.com" in url or "twitter.com" in url:
        return "twitter"
    if "threads.com" in url or "threads.net" in url:
        return "threads"
    return "unknown"


def _pick_best_url(raw: str) -> str:
    """
    Из ячейки с несколькими ссылками (разделены переносом строки, пробелом или ;)
    выбирает одну — предпочтительно wall-пост, а не clip/video.
    """
    # Разбиваем по любому разделителю
    candidates = [u.strip() for u in re.split(r'[\n\r;]+', raw) if u.strip().startswith('http')]
    if not candidates:
        return raw.strip()
    if len(candidates) == 1:
        return candidates[0]
    # Предпочитаем wall-ссылки (пост) над clip/video
    wall = [u for u in candidates if 'wall' in u]
    if wall:
        return wall[0]
    # Иначе берём первую
    return candidates[0]


def _parse_number(value: str) -> Optional[float]:
    """Убирает пробелы, символы валюты и возвращает float."""
    if not value:
        return None
    cleaned = re.sub(r"[^\d,\.]", "", value.strip()).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(value: str) -> int:
    result = _parse_number(value)
    return int(result) if result is not None else 0


def parse_csv(content: str) -> MediaPlan:
    """
    Принимает текст CSV и возвращает объект MediaPlan.
    Работает с форматом МП «Настя прости меня» и аналогичными.
    """
    mp = MediaPlan()
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    # --- Ищем итоговый охват из строки "Итого с органикой" ---
    for row in rows:
        if not row:
            continue
        for col_idx, cell in enumerate(row):
            if "итого с органикой" in cell.strip().lower():
                # Ищем число в этой же строке правее триггера
                for next_cell in row[col_idx + 1:]:
                    val = _parse_number(next_cell.strip())
                    if val and val > 1000:
                        mp.mp_total_actual_reach = int(val)
                        break
                break

    # --- Paid-блок ---
    # Ищем строку-заголовок: первая строка, где есть "Название" или "Площадка"
    header_idx = None
    for i, row in enumerate(rows):
        if row and any(cell.strip() in ("Название", "Площадка") for cell in row):
            header_idx = i
            break

    if header_idx is not None:
        headers = [h.strip() for h in rows[header_idx]]
        col = {h: idx for idx, h in enumerate(headers) if h}

        def find_col(*keywords: str, exclude: tuple = ()) -> Optional[int]:
            """Ищет индекс колонки по ключевым словам в заголовке."""
            for kw in keywords:
                for h, idx in col.items():
                    h_l = h.lower()
                    if kw.lower() in h_l and not any(ex.lower() in h_l for ex in exclude):
                        return idx
            return None

        # Определяем индексы колонок один раз для всего блока
        idx_channel_url   = find_col("Ссылка", exclude=("публикацию", "пост", "твит", "рекламу", "скрин"))
        idx_post_url      = find_col("Ссылка на публикацию", "Ссылка на пост", "Ссылка на твит", "Публикация", "Ссылка на рекламу")
        # Сначала ищем финальный охват по точным словам — это приоритет
        idx_actual_reach  = find_col(
            "Охват (факт)", "Охват факт", "Просмотры факт", "Просмотры (факт)",
            "Реальный охват", "Итого охват", "Итого просмотры",
            "Реальные просмотры", "Факт охват", "Факт просмотры",
        )
        # Плановый охват — ищем после того как нашли финальный
        # «Просмотры» без уточнения — это плановые, но только если финальный уже найден отдельно
        idx_planned_reach = find_col(
            "Планируемый охват", "Охват прогноз", "Охват план", "Ожидаемый охват",
            "Просмотры", "Охват",
            exclude=("факт", "реальный", "итого"),
        )
        idx_cost          = find_col("Общая стоимость с АК", "Цена с АК", "Стоимость с АК", "Цена с ак")
        idx_planned_cpv   = find_col("Планируемый CPV", "Плановый CPV", "CPV план", "CPV прогноз",
                                     exclude=("факт",))
        idx_actual_cpv    = find_col("Факт CPV", "CPV факт", "CPV ФАКТ")
        idx_date          = find_col("Дата")

        def get_by_idx(row: list, idx: Optional[int]) -> str:
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        for row in rows[header_idx + 1:]:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            # Стоп-строки: итого завершает paid-блок
            if name.lower().startswith("итого") or name.lower().startswith("общий"):
                break
            # Если строка явно из блока стоимостей проекта — пропускаем
            if any(kw in name for kw in ("Стоимость", "Копирайт", "Account", "Junior", "Прогноз", "Факт", "Сумма", "Менеджер", "Мемы", "Видеоролик", "Логистик", "Печать")):
                continue

            def get(key: str) -> str:
                idx = col.get(key)
                if idx is not None and idx < len(row):
                    return row[idx].strip()
                return ""

            channel_url      = get_by_idx(row, idx_channel_url)
            raw_post_urls    = get_by_idx(row, idx_post_url)
            planned_reach    = _parse_int(get_by_idx(row, idx_planned_reach))
            actual_reach_raw = int(_parse_number(get_by_idx(row, idx_actual_reach)) or 0) or None
            cost             = _parse_number(get_by_idx(row, idx_cost))
            planned_cpv      = _parse_number(get_by_idx(row, idx_planned_cpv))
            actual_cpv       = _parse_number(get_by_idx(row, idx_actual_cpv))
            date             = get_by_idx(row, idx_date)

            # Извлекаем все ссылки из ячейки — один блогер может иметь несколько публикаций
            all_post_urls = [
                u.strip() for u in re.split(r'[\n\r;]+', raw_post_urls)
                if u.strip().startswith('http')
                and not any(skip in u for skip in ('disk.yandex', 'yandex.ru/i/', 'prnt.sc', 'drive.google', 'clck.ru'))
            ]

            if not all_post_urls:
                all_post_urls = [_pick_best_url(raw_post_urls)]

            # Пропускаем строки без планового охвата
            if planned_reach == 0 and not actual_reach_raw:
                continue

            for i, post_url in enumerate(all_post_urls):
                platform = _detect_platform(post_url or channel_url)
                # Для нескольких ссылок: охват и стоимость только на первой строке
                # (чтобы не задваивать бюджет и план)
                post = Post(
                    name=name,
                    channel_url=channel_url,
                    platform=platform,
                    post_url=post_url,
                    planned_reach=planned_reach if i == 0 else 0,
                    actual_reach=actual_reach_raw if i == 0 else None,
                    cost=cost if i == 0 else None,
                    planned_cpv=planned_cpv if i == 0 else None,
                    actual_cpv=actual_cpv if i == 0 else None,
                    date=date,
                    is_organic=False,
                )
                if post.post_url or post.channel_url:
                    mp.paid_posts.append(post)

    # --- Органика ---
    # Ищем триггер «органически» в любой ячейке любой строки.
    # После триггера сканируем все ячейки каждой строки в поисках http-ссылок.
    # Охват берём из следующей непустой ячейки после ссылки.
    organic_started = False
    seen_urls: set[str] = set()

    for row in rows:
        if not row:
            continue

        # Триггер начала органики — несколько вариантов оформления
        if any("органик" in str(cell).lower() for cell in row):
            organic_started = True
            continue

        if not organic_started:
            continue

        # Ищем http-ссылки в любой ячейке строки
        for col_idx, cell in enumerate(row):
            cell = cell.strip()
            if not cell.startswith("http"):
                continue

            # Пропускаем ссылки на скриншоты и новостные статьи (не соцсети)
            SOCIAL_DOMAINS = ("vk.com", "vk.ru", "t.me", "instagram.com", "x.com", "twitter.com", "threads.com")
            if not any(domain in cell for domain in SOCIAL_DOMAINS):
                continue
            if any(skip in cell for skip in ("disk.yandex", "yandex.ru/i/", "yandex.ru/d/")):
                continue

            # Это органическая ссылка — берём охват из следующей ячейки
            reach = 0
            for next_cell in row[col_idx + 1:]:
                next_cell = next_cell.strip()
                if next_cell and not next_cell.startswith("http"):
                    parsed = _parse_number(next_cell)
                    if parsed is not None:
                        reach = int(parsed)
                    break

            # Дедупликация
            if cell in seen_urls:
                continue
            seen_urls.add(cell)

            platform = _detect_platform(cell)
            post = Post(
                name=cell,
                channel_url=cell,
                platform=platform,
                post_url=cell,
                planned_reach=0,
                actual_reach=reach if reach else None,
                is_organic=True,
            )
            mp.organic_posts.append(post)

    return mp
