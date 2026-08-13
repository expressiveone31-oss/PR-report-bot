"""Тесты матричной логики обновления Google Sheets — без реальных API.

Мокает fetch_views_for_urls и open_spreadsheet, проверяет что для
каждой комбинации «есть/нет старое значение × API дал число / API молчит»
бот выбирает правильное действие.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.updater.google_sheets_updater import (
    _has_numeric_value,
    _col_letter,
    update_google_sheet,
)


class HasNumericValueTests(unittest.TestCase):
    def test_empty_variants(self):
        for raw in ("", "  ", "-", "—", "н/д", "n/a"):
            self.assertFalse(_has_numeric_value(raw), f"expected False for {raw!r}")

    def test_zero_treated_as_empty(self):
        # 0 = ноль просмотров = фактически нет данных
        self.assertFalse(_has_numeric_value("0"))
        self.assertFalse(_has_numeric_value("0.0"))

    def test_valid_numbers(self):
        for raw in ("1", "1234", "12 345", "1234.56", "1234,56"):
            self.assertTrue(_has_numeric_value(raw), f"expected True for {raw!r}")

    def test_garbage_is_false(self):
        for raw in ("hello", "abc123", "1a2b"):
            self.assertFalse(_has_numeric_value(raw))


class ColLetterTests(unittest.TestCase):
    def test_single_letter(self):
        self.assertEqual(_col_letter(0), "A")
        self.assertEqual(_col_letter(8), "I")
        self.assertEqual(_col_letter(25), "Z")

    def test_double_letter(self):
        self.assertEqual(_col_letter(26), "AA")
        self.assertEqual(_col_letter(27), "AB")
        self.assertEqual(_col_letter(51), "AZ")
        self.assertEqual(_col_letter(52), "BA")


def _fake_worksheet(title: str, rows: list[list], sheet_id: int = 0):
    """Создаёт fake gspread-Worksheet с фиксированными данными."""
    ws = MagicMock()
    ws.title = title
    ws.id = sheet_id
    ws.spreadsheet_id = "test_spreadsheet"
    ws.client = MagicMock()
    ws.client.request = MagicMock(return_value=SimpleNamespace(
        json=lambda: {"sheets": [{"data": [{"rowData": []}]}]}
    ))
    ws.get = MagicMock(return_value=rows)
    ws.batch_format = MagicMock()
    return ws


class UpdateMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, rows: list[list], views_map: dict[int, tuple]):
        """Прогоняет update_google_sheet на одном фейковом листе."""
        ws = _fake_worksheet("Лист1", rows)
        sh = MagicMock()
        sh.worksheets.return_value = [ws]
        sh.values_batch_update = MagicMock()
        sh.title = "test"

        with patch(
            "src.updater.google_sheets_updater.open_spreadsheet", return_value=sh
        ), patch(
            "src.updater.google_sheets_updater.fetch_views_for_urls",
            new=AsyncMock(return_value=views_map),
        ):
            stats, changes = await update_google_sheet("test_id", dry_run=False)
        return stats, changes, ws, sh

    async def test_updated_when_old_has_value_and_api_gives_new(self):
        rows = [
            ["", "", "", "", "", "", "", "Ссылка на публикацию", "Охват (факт)"],
            ["", "", "", "", "", "", "", "https://t.me/ch/1", "100"],
        ]
        views_map = {2: (250, "ok")}
        stats, changes, ws, sh = await self._run(rows, views_map)

        self.assertEqual(stats.updated, 1)
        self.assertEqual(stats.kept, 0)
        self.assertEqual(changes[0].action, "updated")
        # Значение записано
        sh.values_batch_update.assert_called_once()
        payload = sh.values_batch_update.call_args.kwargs["body"]
        self.assertEqual(payload["data"][0]["values"], [[250]])

    async def test_updated_when_old_empty_and_api_gives_new(self):
        rows = [
            ["", "", "", "", "", "", "", "Ссылка на публикацию", "Охват (факт)"],
            ["", "", "", "", "", "", "", "https://t.me/ch/1", ""],
        ]
        views_map = {2: (500, "ok")}
        stats, changes, ws, sh = await self._run(rows, views_map)
        self.assertEqual(stats.updated, 1)
        self.assertEqual(changes[0].action, "updated")

    async def test_kept_when_old_has_value_and_api_error(self):
        rows = [
            ["", "", "", "", "", "", "", "Ссылка на публикацию", "Охват (факт)"],
            ["", "", "", "", "", "", "", "https://t.me/ch/1", "100"],
        ]
        views_map = {2: (None, "error")}
        stats, changes, ws, sh = await self._run(rows, views_map)

        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.kept, 1)
        self.assertEqual(changes[0].action, "kept")
        # values_batch_update НЕ должен вызываться — записывать нечего
        sh.values_batch_update.assert_not_called()

    async def test_kept_when_api_returns_zero_and_old_has_value(self):
        """VK-clip и такие: API вернул 0, старое значение сохраняем."""
        rows = [
            ["", "", "", "", "", "", "", "Ссылка на публикацию", "Охват (факт)"],
            ["", "", "", "", "", "", "", "https://vk.com/wall1_1", "50000"],
        ]
        views_map = {2: (0, "ok")}
        stats, changes, ws, sh = await self._run(rows, views_map)

        self.assertEqual(stats.kept, 1)
        self.assertEqual(changes[0].action, "kept")
        sh.values_batch_update.assert_not_called()

    async def test_empty_no_data_when_both_empty(self):
        rows = [
            ["", "", "", "", "", "", "", "Ссылка на публикацию", "Охват (факт)"],
            ["", "", "", "", "", "", "", "https://t.me/ch/1", ""],
        ]
        views_map = {2: (None, "error")}
        stats, changes, ws, sh = await self._run(rows, views_map)

        self.assertEqual(stats.empty_no_data, 1)
        self.assertEqual(changes[0].action, "empty_no_data")
        sh.values_batch_update.assert_not_called()

    async def test_dry_run_writes_nothing(self):
        rows = [
            ["", "", "", "", "", "", "", "Ссылка на публикацию", "Охват (факт)"],
            ["", "", "", "", "", "", "", "https://t.me/ch/1", "100"],
        ]
        views_map = {2: (250, "ok")}
        ws = _fake_worksheet("Лист1", rows)
        sh = MagicMock()
        sh.worksheets.return_value = [ws]
        sh.values_batch_update = MagicMock()

        with patch(
            "src.updater.google_sheets_updater.open_spreadsheet", return_value=sh
        ), patch(
            "src.updater.google_sheets_updater.fetch_views_for_urls",
            new=AsyncMock(return_value=views_map),
        ):
            stats, changes = await update_google_sheet("test_id", dry_run=True)

        # План есть, действие правильное, но записи и форматирования нет
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].action, "updated")
        sh.values_batch_update.assert_not_called()
        ws.batch_format.assert_not_called()


class TikTokUrlDetectionTests(unittest.TestCase):
    """Регрессия: is_post_url должен ловить все TikTok-форматы."""

    def test_all_tiktok_formats(self):
        from src.updater.common import is_post_url
        cases = [
            ("https://www.tiktok.com/@user/video/1234567890", True),
            ("https://www.tiktok.com/@user/video/1234567890?_r=1&_t=abc", True),
            ("https://vt.tiktok.com/ZS4MEt4Lj/", True),
            ("https://vm.tiktok.com/ZN8eL6b7k/", True),
            ("https://www.tiktok.com/t/ZP8tkeJ4b/", True),
            # Профиль без video — не пост
            ("https://www.tiktok.com/@user", False),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(is_post_url(url), expected)


if __name__ == "__main__":
    unittest.main()
