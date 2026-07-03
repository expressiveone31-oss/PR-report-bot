"""
Скачивание xlsx / csv по ссылке.

Поддерживает:
- Google Sheets: https://docs.google.com/spreadsheets/d/{id}/...
- Яндекс.Диск: https://disk.yandex.ru/i/... или https://disk.yandex.ru/d/...
- Прямая ссылка на .xlsx / .csv
"""

import re
import logging
import aiohttp
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 60  # секунд на скачивание файла
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB — защита от гигантских файлов


@dataclass
class DownloadResult:
    """Результат скачивания."""
    file_bytes: Optional[bytes] = None
    filename: Optional[str] = None  # предполагаемое имя файла (для _updated суффикса)
    source: Optional[str] = None    # "google_sheets" | "yandex_disk" | "direct"
    error: Optional[str] = None


def _detect_source(url: str) -> str:
    """Определяет тип ссылки."""
    url_lower = url.lower()
    if "docs.google.com/spreadsheets" in url_lower:
        return "google_sheets"
    if "disk.yandex" in url_lower:
        return "yandex_disk"
    return "direct"


def _extract_google_sheets_id(url: str) -> Optional[str]:
    """Извлекает spreadsheet ID из ссылки Google Sheets."""
    # https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/...
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None


def _extract_google_sheets_gid(url: str) -> Optional[str]:
    """Извлекает gid (id вкладки) из URL, если есть."""
    match = re.search(r"[#&?]gid=(\d+)", url)
    return match.group(1) if match else None


async def _download_google_sheets(session: aiohttp.ClientSession, url: str) -> DownloadResult:
    """Скачивает Google Sheets как xlsx через export endpoint."""
    spreadsheet_id = _extract_google_sheets_id(url)
    if not spreadsheet_id:
        return DownloadResult(
            source="google_sheets",
            error="Не удалось найти ID таблицы в ссылке Google Sheets",
        )

    # Формируем URL для экспорта в xlsx
    export_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"
    )
    gid = _extract_google_sheets_gid(url)
    if gid:
        export_url += f"&gid={gid}"

    logger.info(f"Google Sheets: downloading id={spreadsheet_id}, gid={gid}")

    try:
        async with session.get(
            export_url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
            allow_redirects=True,
        ) as resp:
            if resp.status == 404:
                return DownloadResult(
                    source="google_sheets",
                    error="Таблица не найдена. Проверь ссылку.",
                )
            if resp.status in (401, 403):
                return DownloadResult(
                    source="google_sheets",
                    error=(
                        "Нет доступа к таблице. Открой доступ по ссылке: "
                        "«Настройки доступа» → «Все, у кого есть ссылка» → «Читатель»."
                    ),
                )
            if resp.status != 200:
                text = await resp.text()
                return DownloadResult(
                    source="google_sheets",
                    error=f"HTTP {resp.status}: {text[:150]}",
                )

            data = await resp.read()
            if len(data) > MAX_FILE_SIZE:
                return DownloadResult(
                    source="google_sheets",
                    error=f"Файл слишком большой ({len(data) // 1024} KB)",
                )

            # Если Google вернул html-страницу — значит нет доступа
            if data[:100].lower().startswith(b"<!doctype html") or b"<html" in data[:200].lower():
                return DownloadResult(
                    source="google_sheets",
                    error=(
                        "Google вернул страницу входа вместо файла. "
                        "Открой доступ по ссылке: «Настройки доступа» → "
                        "«Все, у кого есть ссылка» → «Читатель»."
                    ),
                )

            logger.info(f"Google Sheets: downloaded {len(data)} bytes")
            return DownloadResult(
                file_bytes=data,
                filename=f"{spreadsheet_id}.xlsx",
                source="google_sheets",
            )
    except aiohttp.ClientError as e:
        return DownloadResult(source="google_sheets", error=f"Ошибка сети: {e}")
    except Exception as e:
        return DownloadResult(source="google_sheets", error=f"{type(e).__name__}: {e}")


async def _download_yandex_disk(session: aiohttp.ClientSession, url: str) -> DownloadResult:
    """
    Скачивает файл по публичной ссылке Яндекс.Диск.
    Использует официальное API: https://cloud-api.yandex.net/v1/disk/public/resources/download
    """
    api_url = (
        f"https://cloud-api.yandex.net/v1/disk/public/resources/download"
        f"?public_key={url}"
    )
    logger.info(f"Yandex Disk: getting download URL for {url}")

    try:
        # Шаг 1: получаем прямую ссылку на файл
        async with session.get(
            api_url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                return DownloadResult(
                    source="yandex_disk",
                    error=f"Яндекс.Диск API HTTP {resp.status}: {text[:150]}",
                )
            meta = await resp.json()

        href = meta.get("href")
        if not href:
            return DownloadResult(
                source="yandex_disk",
                error="Яндекс.Диск не вернул ссылку на скачивание",
            )

        # Шаг 2: скачиваем файл по прямой ссылке
        logger.info(f"Yandex Disk: downloading from href")
        async with session.get(
            href,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                return DownloadResult(
                    source="yandex_disk",
                    error=f"Ошибка скачивания HTTP {resp.status}",
                )
            data = await resp.read()

            if len(data) > MAX_FILE_SIZE:
                return DownloadResult(
                    source="yandex_disk",
                    error=f"Файл слишком большой ({len(data) // 1024} KB)",
                )

            # Пытаемся достать имя файла из headers или из meta
            filename = "yandex_file.xlsx"
            content_disposition = resp.headers.get("Content-Disposition", "")
            fname_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
            if fname_match:
                filename = fname_match.group(1)
            elif meta.get("name"):
                filename = meta["name"]

            logger.info(f"Yandex Disk: downloaded {len(data)} bytes, filename={filename}")
            return DownloadResult(
                file_bytes=data,
                filename=filename,
                source="yandex_disk",
            )
    except aiohttp.ClientError as e:
        return DownloadResult(source="yandex_disk", error=f"Ошибка сети: {e}")
    except Exception as e:
        return DownloadResult(source="yandex_disk", error=f"{type(e).__name__}: {e}")


async def _download_direct(session: aiohttp.ClientSession, url: str) -> DownloadResult:
    """Скачивает файл по прямой ссылке."""
    logger.info(f"Direct download: {url}")
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                return DownloadResult(
                    source="direct",
                    error=f"HTTP {resp.status}",
                )
            data = await resp.read()

            if len(data) > MAX_FILE_SIZE:
                return DownloadResult(
                    source="direct",
                    error=f"Файл слишком большой ({len(data) // 1024} KB)",
                )

            # Проверяем что это похоже на xlsx (PK header) или csv
            is_xlsx = data[:2] == b"PK"
            is_csv_like = not is_xlsx and (b"," in data[:1000] or b";" in data[:1000])
            if not is_xlsx and not is_csv_like:
                return DownloadResult(
                    source="direct",
                    error="Файл по ссылке не похож на xlsx или csv",
                )

            # Имя файла
            filename = url.split("/")[-1].split("?")[0] or "downloaded.xlsx"
            if not (filename.endswith(".xlsx") or filename.endswith(".csv")):
                filename = "downloaded.xlsx" if is_xlsx else "downloaded.csv"

            logger.info(f"Direct: downloaded {len(data)} bytes as {filename}")
            return DownloadResult(
                file_bytes=data,
                filename=filename,
                source="direct",
            )
    except aiohttp.ClientError as e:
        return DownloadResult(source="direct", error=f"Ошибка сети: {e}")
    except Exception as e:
        return DownloadResult(source="direct", error=f"{type(e).__name__}: {e}")


def is_supported_url(text: str) -> bool:
    """Проверяет, содержит ли текст ссылку, которую мы умеем обрабатывать."""
    text = text.strip()
    if not text.startswith(("http://", "https://")):
        return False
    return (
        "docs.google.com/spreadsheets" in text
        or "disk.yandex" in text
        or text.lower().endswith(".xlsx")
        or text.lower().endswith(".csv")
    )


async def download_from_url(url: str) -> DownloadResult:
    """
    Основная точка входа. Определяет тип ссылки и скачивает файл.
    """
    url = url.strip()

    if not url.startswith(("http://", "https://")):
        return DownloadResult(error="Ссылка должна начинаться с http:// или https://")

    source = _detect_source(url)
    logger.info(f"Downloading from {source}: {url[:100]}")

    async with aiohttp.ClientSession() as session:
        if source == "google_sheets":
            return await _download_google_sheets(session, url)
        elif source == "yandex_disk":
            return await _download_yandex_disk(session, url)
        else:
            return await _download_direct(session, url)
