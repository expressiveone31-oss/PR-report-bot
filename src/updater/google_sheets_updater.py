"""Обновление охватов в живой Google Sheets через service account.

Правила поведения по ячейке «Охват (факт)»:

| В ячейке уже есть число? | API вернул число? | Действие                              |
|--------------------------|-------------------|---------------------------------------|
| нет (пусто)              | да, X             | пишем X, ЖЁЛТАЯ заливка (updated)     |
| нет (пусто)              | нет (error/None)  | ничего не пишем, КРАСНАЯ заливка      |
| да, N                    | да, X             | пишем X, ЖЁЛТАЯ заливка (updated)     |
| да, N                    | нет / X = 0       | НЕ трогаем значение, СЕРАЯ заливка    |

Дополнительно:
- Ячейки с РОЗОВОЙ заливкой (ручная пометка «deleted») не трогаются.
- API возвращает 0 при существующем значении → трактуем как no-data
  (VK-клипы часто отдают 0 когда клип недоступен по причинам API).

Публичная функция update_google_sheet(spreadsheet_id, dry_run=False)
возвращает (stats, plan). В dry-run режиме таблица не меняется, plan
содержит все изменения, которые бот бы сделал.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.parsers.google_sheets_client import open_spreadsheet
from src.updater.common import (
    FINAL_STOP_KEYWORDS,
    ORGANIC_MARKERS,
    POST_URL_KEYWORDS,
    REACH_FACT_KEYWORDS,
    fetch_views_for_urls,
    find_col,
    is_post_url,
)

logger = logging.getLogger(__name__)

# Google Sheets background colors (значения 0..1).
COLOR_UPDATED = {"red": 1.0, "green": 1.0, "blue": 0.6}       # жёлтый  #FFFF99 — обновлено новым числом
COLOR_KEPT = {"red": 0.85, "green": 0.85, "blue": 0.85}       # серый   #D9D9D9 — сохранили старое (API не ответил)
COLOR_ERROR = {"red": 1.0, "green": 0.702, "blue": 0.702}     # красный #FFB3B3 — пусто было, данных нет
COLOR_DELETED_MARKER = {"red": 1.0, "green": 0.816, "blue": 0.894}  # розовый — ручная пометка «удалён», бот не трогает

# Sentinel-цвета для распознавания ручной пометки «пост удалён».
# Пользователь красит ячейку розовым → бот при следующем запуске её пропускает.
_PREV_DELETED_MARKERS = [
    (1.0, 0.816, 0.894),  # розовый — «удалён»
]


@dataclass
class SheetPlan:
    sheet_title: str
    sheet_id: int
    header_row: int  # 1-indexed
    col_url: int     # 0-indexed
    col_reach: int   # 0-indexed
    url_rows: list[tuple[int, str]] = field(default_factory=list)  # (1-indexed row, url)
    skipped_deleted: list[int] = field(default_factory=list)


@dataclass
class RowChange:
    sheet_title: str
    row: int          # 1-indexed
    col_a1: str       # A1-адрес ячейки, например "I5"
    url: str
    old_value: str
    api_value: Optional[int]  # что вернул API площадки
    action: str       # "updated" | "kept" | "empty_no_data" | "deleted_marker"


@dataclass
class UpdateStats:
    updated: int = 0           # старое значение перезаписано новым
    kept: int = 0              # старое значение сохранено (API не ответил)
    empty_no_data: int = 0     # ячейка была пуста и API ничего не дал
    skipped_deleted: int = 0   # ячейка помечена розовым (удалён) — пропущена


def _has_numeric_value(raw: str) -> bool:
    """True если в ячейке уже есть валидное числовое значение охвата.

    Не считает валидным: пусто, '-', 'н/д', '0' (0 просмотров — тоже фактически «нет»).
    """
    s = raw.strip()
    if not s or s in ("-", "—", "н/д", "n/a"):
        return False
    # UNFORMATTED_VALUE даёт числа как int/float, но мы всё нормализовали в str.
    try:
        n = float(s.replace(",", ".").replace(" ", ""))
    except ValueError:
        return False
    return n > 0


def _col_letter(col_zero_indexed: int) -> str:
    """0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA."""
    result = ""
    n = col_zero_indexed
    while True:
        result = chr(ord("A") + (n % 26)) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


def _cells_to_grid(cells: list[list]) -> list[list[str]]:
    """Нормализует прямоугольник значений в матрицу строк одинаковой ширины."""
    max_cols = max((len(r) for r in cells), default=0)
    grid: list[list[str]] = []
    for row in cells:
        normalized = [("" if c is None else str(c)) for c in row]
        while len(normalized) < max_cols:
            normalized.append("")
        grid.append(normalized)
    return grid


def _find_header_row(grid: list[list[str]]) -> Optional[int]:
    """Возвращает 0-based индекс строки-хедера, где встречается ключ вида
    «ссылка на публикацию» или «охват (факт)»."""
    header_hints = (
        "ссылка на публикацию", "ссылка на пост", "ссылка на твит",
        "охват (факт)", "просмотры факт", "публикация",
        "охват факт", "реальный охват",
    )
    for i, row in enumerate(grid):
        row_lower = " ".join(row).lower()
        if any(kw in row_lower for kw in header_hints):
            return i
    return None


def _collect_url_rows(
    grid: list[list[str]],
    header_row: int,
    col_url: int,
    deleted_rows: set[int],
) -> tuple[list[tuple[int, str]], list[int]]:
    """Собирает URL-ы для обновления. Возвращает (url_rows_1indexed, skipped_deleted_1indexed).

    url_rows — список (row_1indexed, url), которые надо обновить.
    skipped_deleted — строки с розовой заливкой (deleted), их не трогаем.
    """
    url_rows: list[tuple[int, str]] = []
    skipped_deleted: list[int] = []
    row_idx = header_row + 1

    while row_idx < len(grid):
        row = grid[row_idx]
        row_text = " ".join(c.lower() for c in row if c.strip())

        # 1. Финальный стоп
        if any(kw in row_text for kw in FINAL_STOP_KEYWORDS):
            logger.info("gs updater: final stop at row %d ('%s')", row_idx + 1, row_text[:60])
            break

        # 2. Промежуточное "Итого:"
        is_intermediate_total = False
        for col_check in range(0, min(3, len(row))):
            cv = row[col_check].strip().lower()
            if (cv.startswith("итого") and "органик" not in cv) or cv.startswith("итого (охват)"):
                is_intermediate_total = True
                break

        if is_intermediate_total:
            organic_found = False
            for look in range(1, 6):
                ci = row_idx + look
                if ci >= len(grid):
                    break
                check_text = " ".join(c.lower() for c in grid[ci] if c.strip())
                if any(m in check_text for m in ORGANIC_MARKERS):
                    organic_found = True
                    break
            if not organic_found:
                logger.info("gs updater: intermediate 'Итого' at row %d, no organic → stop", row_idx + 1)
                break
            row_idx += 1
            continue

        # 3. URL в ячейке
        url = row[col_url].strip() if col_url < len(row) else ""
        if is_post_url(url):
            if (row_idx + 1) in deleted_rows:
                skipped_deleted.append(row_idx + 1)
            else:
                url_rows.append((row_idx + 1, url))

        row_idx += 1

    return url_rows, skipped_deleted


def _find_deleted_rows(
    color_grid: list[list[Optional[tuple[float, float, float]]]],
    header_row: int,
    col_reach: int,
) -> set[int]:
    """По цветовой сетке находит 1-indexed строки с розовой (deleted) заливкой
    в колонке col_reach. Их не трогаем при повторных запусках."""
    deleted: set[int] = set()
    for row_idx in range(header_row + 1, len(color_grid)):
        row = color_grid[row_idx]
        if col_reach >= len(row) or row[col_reach] is None:
            continue
        r, g, b = row[col_reach]
        for pr, pg, pb in _PREV_DELETED_MARKERS:
            if abs(r - pr) < 0.02 and abs(g - pg) < 0.02 and abs(b - pb) < 0.02:
                deleted.add(row_idx + 1)
                break
    return deleted


def _read_color_grid(ws, a1_range: str) -> list[list[Optional[tuple[float, float, float]]]]:
    """Читает фактические background-цвета ячеек через spreadsheets.get с fields.

    Возвращает 2D-сетку: (r, g, b) или None если цвет не задан.
    """
    resp = ws.client.request(
        "get",
        f"https://sheets.googleapis.com/v4/spreadsheets/{ws.spreadsheet_id}",
        params={
            "ranges": f"{ws.title}!{a1_range}",
            "fields": "sheets(data(rowData(values(effectiveFormat(backgroundColor)))))",
        },
    )
    data = resp.json()
    sheets = data.get("sheets", [])
    if not sheets:
        return []
    grid_data = sheets[0].get("data", [])
    if not grid_data:
        return []
    row_data = grid_data[0].get("rowData", [])
    result: list[list[Optional[tuple[float, float, float]]]] = []
    for row in row_data:
        values = row.get("values", [])
        row_colors: list[Optional[tuple[float, float, float]]] = []
        for cell in values:
            ef = cell.get("effectiveFormat") or {}
            bg = ef.get("backgroundColor") or {}
            r = bg.get("red")
            g = bg.get("green")
            b = bg.get("blue")
            if r is None and g is None and b is None:
                row_colors.append(None)
            else:
                row_colors.append((r or 0.0, g or 0.0, b or 0.0))
        result.append(row_colors)
    return result


async def update_google_sheet(
    spreadsheet_id: str,
    dry_run: bool = False,
) -> tuple[UpdateStats, list[RowChange]]:
    """Обновляет охваты во всех листах Google Sheets.

    dry_run=True — ничего не пишет, только возвращает план изменений.
    Raises SpreadsheetAccessError если нет доступа.
    """
    sh = open_spreadsheet(spreadsheet_id)
    logger.info("gs updater: opened '%s' with %d worksheets", sh.title, len(sh.worksheets()))

    stats = UpdateStats()
    all_changes: list[RowChange] = []

    for ws in sh.worksheets():
        logger.info("gs updater: processing sheet '%s'", ws.title)

        # Читаем значения — сразу весь верх листа, достаточно и для больших МП.
        # UNFORMATTED_VALUE отдаёт числа как числа, а не как форматированные строки.
        values = ws.get(
            "A1:Z200",
            value_render_option="UNFORMATTED_VALUE",
        )
        grid = _cells_to_grid(values)
        if not grid:
            logger.info("gs updater: sheet '%s' is empty, skipping", ws.title)
            continue

        header_row = _find_header_row(grid)
        if header_row is None:
            logger.info("gs updater: sheet '%s' no header, skipping", ws.title)
            continue

        headers = grid[header_row]
        col_url = find_col(headers, POST_URL_KEYWORDS, exclude_keywords=("канал", "channel", "профил"))
        col_reach = find_col(headers, REACH_FACT_KEYWORDS, exclude_keywords=("план", "прогноз", "ожидаем", "plan"))

        if col_url is None or col_reach is None:
            logger.warning(
                "gs updater: sheet '%s' col_url=%s col_reach=%s — не найдены нужные колонки",
                ws.title, col_url, col_reach,
            )
            continue

        logger.info(
            "gs updater: sheet '%s' header_row=%d, col_url=%d ('%s'), col_reach=%d ('%s')",
            ws.title, header_row + 1, col_url, headers[col_url], col_reach, headers[col_reach],
        )

        # Читаем цвета — чтобы уважать deleted-помеченные строки.
        try:
            color_grid = _read_color_grid(ws, f"A1:Z{len(grid)}")
            deleted_rows = _find_deleted_rows(color_grid, header_row, col_reach)
            if deleted_rows:
                logger.info(
                    "gs updater: sheet '%s' skip deleted rows: %s",
                    ws.title, sorted(deleted_rows),
                )
        except Exception as e:
            logger.warning("gs updater: failed to read colors, ignoring deleted flag: %s", e)
            deleted_rows = set()

        url_rows, skipped_deleted = _collect_url_rows(grid, header_row, col_url, deleted_rows)
        if not url_rows and not skipped_deleted:
            logger.info("gs updater: sheet '%s' no URLs found", ws.title)
            continue

        stats.skipped_deleted += len(skipped_deleted)
        logger.info(
            "gs updater: sheet '%s' collected %d URLs (skipped %d deleted)",
            ws.title, len(url_rows), len(skipped_deleted),
        )

        # Идём в API площадок.
        views_map = await fetch_views_for_urls(url_rows)

        # Формируем план изменений по правилам из docstring модуля.
        value_updates: list[dict] = []
        format_requests: list[dict] = []
        col_letter = _col_letter(col_reach)

        for row, url in url_rows:
            api_views, api_status = views_map.get(row, (None, "error"))
            cell_a1 = f"{col_letter}{row}"
            old_raw = grid[row - 1][col_reach] if col_reach < len(grid[row - 1]) else ""
            old_has_value = _has_numeric_value(old_raw)
            # API «дал число» — только если status ok и число > 0.
            # 0 при существующем значении считаем мусором (частый случай VK-clip).
            api_has_value = (
                api_status == "ok"
                and api_views is not None
                and (api_views > 0 or not old_has_value)
            )

            if old_has_value and api_has_value:
                # Оба есть — перезаписываем новым, жёлтый.
                action = "updated"
                stats.updated += 1
                value_updates.append({"range": f"'{ws.title}'!{cell_a1}", "values": [[api_views]]})
                format_requests.append({"range": cell_a1, "format": {"backgroundColor": COLOR_UPDATED}})
            elif not old_has_value and api_has_value:
                # Ячейка была пуста — пишем впервые, жёлтый.
                action = "updated"
                stats.updated += 1
                value_updates.append({"range": f"'{ws.title}'!{cell_a1}", "values": [[api_views]]})
                format_requests.append({"range": cell_a1, "format": {"backgroundColor": COLOR_UPDATED}})
            elif old_has_value and not api_has_value:
                # В ячейке было число, API молчит или дал 0 — оставляем как есть, серый.
                action = "kept"
                stats.kept += 1
                format_requests.append({"range": cell_a1, "format": {"backgroundColor": COLOR_KEPT}})
            else:
                # Пусто и API молчит — красная заливка, ячейка остаётся пустой.
                action = "empty_no_data"
                stats.empty_no_data += 1
                format_requests.append({"range": cell_a1, "format": {"backgroundColor": COLOR_ERROR}})

            all_changes.append(RowChange(
                sheet_title=ws.title,
                row=row,
                col_a1=cell_a1,
                url=url,
                old_value=old_raw,
                api_value=api_views,
                action=action,
            ))

        if dry_run:
            logger.info("gs updater: dry-run, skipping writes to '%s'", ws.title)
            continue

        # Пишем — два батча. Сначала значения (batchUpdate values), потом форматирование.
        if value_updates:
            sh.values_batch_update(body={
                "valueInputOption": "RAW",
                "data": value_updates,
            })
            logger.info("gs updater: wrote %d values to '%s'", len(value_updates), ws.title)

        if format_requests:
            ws.batch_format(format_requests)
            logger.info("gs updater: applied %d format ops to '%s'", len(format_requests), ws.title)

    return stats, all_changes
