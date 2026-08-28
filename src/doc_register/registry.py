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
        operational = {
            "processed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source_file_name": candidate.source_path.name,
            "copied_file_path": str(candidate.copied_path),
            "sha256": candidate.sha256,
            "file_created_at": candidate.created_at.isoformat(),
            "file_modified_at": candidate.modified_at.isoformat(),
        }
        row = []
        for column in REGISTER_COLUMNS:
            value = operational.get(column, getattr(result, column, ""))
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            row.append(value)
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
            migrated = _ensure_headers(sheet)
            if migrated:
                self._format(sheet)
                workbook.save(self.path)
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
        widths_by_header = {
            "processed_at": 22,
            "source_file_name": 34,
            "copied_file_path": 48,
            "sha256": 66,
            "processing_status": 20,
            "review_reason": 48,
            "error_message": 54,
            "document_category": 24,
            "document_type": 26,
            "document_date": 18,
            "summary": 54,
            "extraction_notes": 42,
            "ocr_quality_flags": 34,
            "lessor": 34,
            "lessor_2": 34,
            "lessee": 34,
            "lessee_tax_id": 18,
            "option_price": 18,
            "purchase_price": 18,
            "assignment_price": 18,
            "property_display_name": 42,
            "property_address": 48,
            "owner_address": 48,
            "bank_account_holder": 34,
            "iban": 34,
            "payment_description": 54,
            "ai_review_status": 22,
            "ai_reviewed_fields": 34,
            "ai_accepted_fields": 34,
            "ai_rejected_fields": 34,
            "ai_review_reason": 54,
            "deterministic_json": 80,
            "llm_json": 80,
            "raw_json": 80,
        }
        for index, header in enumerate(REGISTER_COLUMNS, start=1):
            width = widths_by_header.get(header)
            if width:
                sheet.column_dimensions[_column_letter(index)].width = width

        if sheet.tables:
            table = next(iter(sheet.tables.values()))
            last_column = _column_letter(len(REGISTER_COLUMNS))
            table.ref = f"A1:{last_column}{max(sheet.max_row, 1)}"


def _ensure_headers(sheet) -> bool:
    existing_headers = [
        sheet.cell(row=1, column=column).value
        for column in range(1, max(sheet.max_column, len(REGISTER_COLUMNS)) + 1)
    ]
    existing_headers = [str(header) if header is not None else "" for header in existing_headers]
    if existing_headers[: len(REGISTER_COLUMNS)] == REGISTER_COLUMNS:
        return False

    rows: list[dict[str, object]] = []
    for row_index in range(2, sheet.max_row + 1):
        row_data: dict[str, object] = {}
        for column_index, header in enumerate(existing_headers, start=1):
            if header:
                row_data[header] = sheet.cell(row=row_index, column=column_index).value
        rows.append(row_data)

    if sheet.max_column:
        sheet.delete_cols(1, sheet.max_column)
    sheet.append(REGISTER_COLUMNS)
    for row_data in rows:
        sheet.append([row_data.get(column, "") for column in REGISTER_COLUMNS])
    return True


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
