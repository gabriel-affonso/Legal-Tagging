from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .models import ExtractionResult, PdfCandidate, REGISTER_COLUMNS


SHEET_NAME = "Document Register"


class ExcelRegister:
    def __init__(self, path: Path):
        self.path = path

    def existing_hashes(self) -> set[str]:
        workbook, sheet = self._load()
        sha_col = REGISTER_COLUMNS.index("sha256") + 1
        values: set[str] = set()
        for row in range(2, sheet.max_row + 1):
            value = sheet.cell(row=row, column=sha_col).value
            if value:
                values.add(str(value))
        workbook.close()
        return values

    def append(self, candidate: PdfCandidate, result: ExtractionResult) -> None:
        workbook, sheet = self._load()
        row = [
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            candidate.source_path.name,
            str(candidate.copied_path),
            candidate.sha256,
            candidate.created_at.isoformat(),
            candidate.modified_at.isoformat(),
            result.processing_status,
            result.processed_ok,
            result.needs_review,
            result.review_reason,
            result.reviewed_by,
            result.reviewed_at,
            result.error_message,
            result.document_category,
            result.document_type,
            result.document_subtype,
            result.document_date,
            result.summary,
            result.language,
            result.signed_date,
            result.contract_type,
            result.lessor,
            result.lessee,
            result.property_address,
            result.contract_start_date,
            result.contract_end_date,
            result.rent_payment_day,
            result.monthly_rent,
            result.currency,
            result.payment_date,
            result.payer,
            result.payee,
            result.payment_amount,
            result.payment_method,
            result.payment_reference,
            result.payment_description,
            result.confidence,
            result.extraction_notes,
            result.text_source,
            result.native_text_chars,
            result.ocr_text_chars,
            json.dumps(result.raw_json, ensure_ascii=False),
        ]
        sheet.append(row)
        self._format(sheet)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(self.path)
        workbook.close()

    def _load(self):
        try:
            from openpyxl import Workbook, load_workbook
            from openpyxl.worksheet.table import Table, TableStyleInfo
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install openpyxl with `pip install -r requirements.txt`.") from exc

        if self.path.exists():
            workbook = load_workbook(self.path)
            sheet = workbook[SHEET_NAME] if SHEET_NAME in workbook.sheetnames else workbook.active
            if sheet.title != SHEET_NAME:
                sheet.title = SHEET_NAME
            _ensure_headers(sheet)
            return workbook, sheet

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_NAME
        sheet.append(REGISTER_COLUMNS)
        last_column = _column_letter(len(REGISTER_COLUMNS))
        table = Table(displayName="DocumentRegister", ref=f"A1:{last_column}1")
        style = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        table.tableStyleInfo = style
        sheet.add_table(table)
        self._format(sheet)
        return workbook, sheet

    def _format(self, sheet) -> None:
        sheet.freeze_panes = "A2"
        widths = {
            "A": 22,
            "B": 32,
            "C": 48,
            "D": 66,
            "G": 24,
            "J": 44,
            "M": 54,
            "N": 24,
            "O": 24,
            "Q": 22,
            "R": 54,
            "V": 28,
            "W": 28,
            "X": 42,
            "AJ": 54,
            "AN": 80,
        }
        for column, width in widths.items():
            sheet.column_dimensions[column].width = width

        if sheet.tables:
            table = next(iter(sheet.tables.values()))
            last_column = _column_letter(len(REGISTER_COLUMNS))
            table.ref = f"A1:{last_column}{max(sheet.max_row, 1)}"


def _ensure_headers(sheet) -> None:
    for index, expected in enumerate(REGISTER_COLUMNS, start=1):
        current = sheet.cell(row=1, column=index).value
        if current == expected:
            continue
        existing_headers = [
            sheet.cell(row=1, column=column).value
            for column in range(1, max(sheet.max_column, len(REGISTER_COLUMNS)) + 1)
        ]
        if expected not in existing_headers:
            sheet.insert_cols(index)
        sheet.cell(row=1, column=index).value = expected


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
