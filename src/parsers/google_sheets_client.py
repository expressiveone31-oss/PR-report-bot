"""Google Sheets service-account client.

Авторизация service account из env: либо GOOGLE_SERVICE_ACCOUNT_JSON
(весь JSON строкой — Railway), либо GOOGLE_SERVICE_ACCOUNT_KEY_PATH
(путь к файлу — локальная разработка). Приоритет у _JSON.

Публичные функции:
- get_service_account_email() -> str | None: адрес сервисника из ключа
  (для сообщений «поделись таблицей с …»); None если ключ не задан.
- open_spreadsheet(spreadsheet_id) -> gspread.Spreadsheet: открывает
  таблицу от имени сервисника. Кидает SpreadsheetAccessError с понятной
  причиной если что-то не так.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from src.config import GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SERVICE_ACCOUNT_KEY_PATH

logger = logging.getLogger(__name__)

# Пишем в Sheets, читаем метаданные из Drive (для check-доступа).
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


class SpreadsheetAccessError(Exception):
    """Ошибка доступа к Google Sheets через service account.

    kind:
      - "no_credentials" — ключ сервисника не задан в env;
      - "invalid_credentials" — ключ есть, но невалидный;
      - "not_found" — таблица не существует или сервисник её не видит;
      - "forbidden" — таблица найдена, но нет прав (не поделена с сервисником);
      - "api_error" — прочая ошибка Google API.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _load_key_info() -> Optional[dict]:
    """Загружает JSON ключа из env. None если ключ не задан."""
    if GOOGLE_SERVICE_ACCOUNT_JSON.strip():
        try:
            return json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as e:
            raise SpreadsheetAccessError(
                "invalid_credentials",
                f"GOOGLE_SERVICE_ACCOUNT_JSON не парсится как JSON: {e}",
            )
    if GOOGLE_SERVICE_ACCOUNT_KEY_PATH.strip():
        path = GOOGLE_SERVICE_ACCOUNT_KEY_PATH
        if not os.path.isfile(path):
            raise SpreadsheetAccessError(
                "invalid_credentials",
                f"Файл GOOGLE_SERVICE_ACCOUNT_KEY_PATH не найден: {path}",
            )
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise SpreadsheetAccessError(
                "invalid_credentials",
                f"Не удалось прочитать ключ по {path}: {e}",
            )
    return None


def get_service_account_email() -> Optional[str]:
    """Email сервисника из ключа. None если ключ не задан или битый."""
    try:
        info = _load_key_info()
    except SpreadsheetAccessError:
        return None
    if not info:
        return None
    return info.get("client_email")


@lru_cache(maxsize=1)
def _client() -> gspread.Client:
    info = _load_key_info()
    if not info:
        raise SpreadsheetAccessError(
            "no_credentials",
            "Google service account не настроен. Задай GOOGLE_SERVICE_ACCOUNT_JSON "
            "или GOOGLE_SERVICE_ACCOUNT_KEY_PATH в окружении.",
        )
    try:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    except Exception as e:
        raise SpreadsheetAccessError(
            "invalid_credentials",
            f"Не удалось построить credentials: {type(e).__name__}: {e}",
        )
    return gspread.authorize(creds)


def open_spreadsheet(spreadsheet_id: str) -> gspread.Spreadsheet:
    """Открывает Google Sheets по ID от имени сервисника.

    Raises SpreadsheetAccessError с понятной kind если что-то не так.
    """
    client = _client()
    try:
        return client.open_by_key(spreadsheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        raise SpreadsheetAccessError(
            "not_found",
            f"Таблица {spreadsheet_id} не найдена или не расшарена с сервисником.",
        )
    except gspread.exceptions.APIError as e:
        # Гуглов API HTTP-ошибки: 403 = нет прав, 404 = не существует.
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 403:
            raise SpreadsheetAccessError(
                "forbidden",
                f"Нет прав на таблицу {spreadsheet_id}. "
                f"Поделись с сервисником и повтори.",
            )
        if status == 404:
            raise SpreadsheetAccessError(
                "not_found",
                f"Таблица {spreadsheet_id} не найдена.",
            )
        raise SpreadsheetAccessError(
            "api_error",
            f"Google API error {status}: {e}",
        )
