from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from .models import ExtractionResult, PdfCandidate, REGISTER_COLUMNS


SHEET_NAME = "Document Register"
PROPERTY_SHEET_NAME = "Property Extraction"

PROPERTY_REGISTER_COLUMNS = (
    "pipeline_version",
    "processed_at",
    "source_file_name",
    "source_file_path",
    "sha256",
    "status",
    "reason",
    "document_type",
    "property_name",
    "matrix_article",
    "matrix_section",
    "area_m2",
    "confidence",
    "lease_score",
    "candidate_score",
    "used_llm",
    "source_section",
    "source_clause",
    "text_source",
    "native_text_chars",
    "ocr_text_chars",
    "extraction_notes",
    "annex_text_chars",
    "llm_error",
    "evidence_model",
    "property_pack_status",
    "property_pack_match_score",
    "property_pack_match_method",
    "property_pack_candidate_count",
    "caderneta_source_file",
    "audit",
)


class ExcelRegister:
    def __init__(self, path: Path):
        self.path = path

    def existing_hashes(self) -> set[str]:
        with _workbook_lock(self.path):
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
        with _workbook_lock(self.path):
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
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install openpyxl with `pip install -r requirements.txt`.") from exc

        if self.path.exists():
            workbook = load_workbook(self.path)
            sheet = workbook[SHEET_NAME] if SHEET_NAME in workbook.sheetnames else workbook.create_sheet(SHEET_NAME, 0)
            migrated = _ensure_headers(sheet)
            if not sheet.tables:
                _add_table(sheet, "DocumentRegister", len(REGISTER_COLUMNS))
                migrated = True
            if migrated:
                self._format(sheet)
                workbook.save(self.path)
            return workbook, sheet

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_NAME
        sheet.append(REGISTER_COLUMNS)
        _add_table(sheet, "DocumentRegister", len(REGISTER_COLUMNS))
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
            "annual_rent": 18,
            "rent_amount": 18,
            "rent_frequency": 16,
            "rent_unit": 18,
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


class PropertyExcelRegister:
    """Dedicated worksheet for the focused property-extraction pipeline."""

    def __init__(self, path: Path):
        self.path = path

    def existing_hashes(self, pipeline_version: str | None = None) -> set[str]:
        with _workbook_lock(self.path):
            workbook, sheet = self._load()
            sha_col = PROPERTY_REGISTER_COLUMNS.index("sha256") + 1
            version_col = PROPERTY_REGISTER_COLUMNS.index("pipeline_version") + 1
            values = {
                str(sheet.cell(row=row, column=sha_col).value)
                for row in range(2, sheet.max_row + 1)
                if sheet.cell(row=row, column=sha_col).value
                and (
                    pipeline_version is None
                    or sheet.cell(row=row, column=version_col).value == pipeline_version
                )
            }
            workbook.close()
            return values

    def append(self, payload: dict[str, object]) -> None:
        with _workbook_lock(self.path):
            workbook, sheet = self._load()
            sheet.append([
                _excel_value(payload.get(column, ""))
                for column in PROPERTY_REGISTER_COLUMNS
            ])
            self._format(sheet)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            workbook.save(self.path)
            workbook.close()

    def upsert(self, payload: dict[str, object]) -> None:
        """Replace older pipeline results for the same PDF hash."""
        sha256 = str(payload.get("sha256") or "")
        if not sha256:
            raise ValueError("Property result requires sha256.")
        with _workbook_lock(self.path):
            workbook, sheet = self._load()
            sha_col = PROPERTY_REGISTER_COLUMNS.index("sha256") + 1
            for row in range(sheet.max_row, 1, -1):
                if str(sheet.cell(row=row, column=sha_col).value or "") == sha256:
                    sheet.delete_rows(row)
            sheet.append([
                _excel_value(payload.get(column, ""))
                for column in PROPERTY_REGISTER_COLUMNS
            ])
            self._format(sheet)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            workbook.save(self.path)
            workbook.close()

    def _load(self):
        try:
            from openpyxl import Workbook, load_workbook
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install openpyxl with `pip install -r requirements.txt`.") from exc

        if self.path.exists():
            workbook = load_workbook(self.path)
            if PROPERTY_SHEET_NAME in workbook.sheetnames:
                sheet = workbook[PROPERTY_SHEET_NAME]
            else:
                sheet = workbook.create_sheet(PROPERTY_SHEET_NAME)
                sheet.append(PROPERTY_REGISTER_COLUMNS)
                _add_table(sheet, "PropertyExtraction", len(PROPERTY_REGISTER_COLUMNS))
            migrated = _ensure_property_headers(sheet)
            if not sheet.tables:
                _add_table(sheet, "PropertyExtraction", len(PROPERTY_REGISTER_COLUMNS))
                migrated = True
            if migrated:
                self._format(sheet)
                workbook.save(self.path)
            return workbook, sheet

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = PROPERTY_SHEET_NAME
        sheet.append(PROPERTY_REGISTER_COLUMNS)
        _add_table(sheet, "PropertyExtraction", len(PROPERTY_REGISTER_COLUMNS))
        self._format(sheet)
        return workbook, sheet

    def _format(self, sheet) -> None:
        sheet.freeze_panes = "A2"
        widths = {
            "pipeline_version": 16, "processed_at": 22, "source_file_name": 34, "source_file_path": 48,
            "sha256": 66, "property_name": 42, "reason": 28,
            "extraction_notes": 54, "llm_error": 48, "evidence_model": 80,
            "property_pack_match_method": 34, "caderneta_source_file": 34, "audit": 80,
        }
        for index, header in enumerate(PROPERTY_REGISTER_COLUMNS, start=1):
            if header in widths:
                sheet.column_dimensions[_column_letter(index)].width = widths[header]
        if sheet.tables:
            table = next(iter(sheet.tables.values()))
            table.ref = f"A1:{_column_letter(len(PROPERTY_REGISTER_COLUMNS))}{max(sheet.max_row, 1)}"


def _excel_value(value: object) -> object:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _ensure_property_headers(sheet) -> bool:
    existing_headers = [
        str(sheet.cell(row=1, column=column).value or "")
        for column in range(1, max(sheet.max_column, len(PROPERTY_REGISTER_COLUMNS)) + 1)
    ]
    if existing_headers[:len(PROPERTY_REGISTER_COLUMNS)] == list(PROPERTY_REGISTER_COLUMNS):
        return False

    rows: list[dict[str, object]] = []
    for row_index in range(2, sheet.max_row + 1):
        rows.append({
            header: sheet.cell(row=row_index, column=column_index).value
            for column_index, header in enumerate(existing_headers, start=1)
            if header
        })
    if sheet.max_column:
        sheet.delete_cols(1, sheet.max_column)
    sheet.append(PROPERTY_REGISTER_COLUMNS)
    for row in rows:
        sheet.append([row.get(column, "") for column in PROPERTY_REGISTER_COLUMNS])
    return True


def _add_table(sheet, name: str, column_count: int) -> None:
    from openpyxl.worksheet.table import Table, TableStyleInfo

    table = Table(displayName=name, ref=f"A1:{_column_letter(column_count)}1")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    sheet.add_table(table)


@contextmanager
def _workbook_lock(path: Path):
    """Serialize local workbook writes when both pipelines run together."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
