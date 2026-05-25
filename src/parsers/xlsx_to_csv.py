"""
Конвертер Excel (.xlsx) → CSV строка.
Читает только первую вкладку.
Сохраняет значения ячеек как есть, формулы заменяет на cached_value.
"""

import csv
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def xlsx_bytes_to_csv(xlsx_bytes: bytes) -> tuple[str, str]:
    """
    Конвертирует xlsx-файл в CSV строку.
    Возвращает (csv_content, sheet_name).
    """
    import openpyxl

    wb = openpyxl.load_workbook(
        io.BytesIO(xlsx_bytes),
        read_only=True,
        data_only=True,   # берём cached значения, не формулы
        keep_links=False,
    )

    sheet = wb.worksheets[0]
    sheet_name = sheet.title
    logger.info(f"xlsx: reading sheet '{sheet_name}', dims={sheet.dimensions}")

    output = io.StringIO()
    writer = csv.writer(output)

    for row in sheet.iter_rows(values_only=True):
        # Конвертируем каждую ячейку в строку
        str_row = []
        for cell in row:
            if cell is None:
                str_row.append("")
            elif isinstance(cell, float):
                # Убираем лишние нули: 25000.0 → 25000
                if cell == int(cell):
                    str_row.append(str(int(cell)))
                else:
                    str_row.append(str(cell))
            else:
                str_row.append(str(cell))
        writer.writerow(str_row)

    wb.close()
    return output.getvalue(), sheet_name
