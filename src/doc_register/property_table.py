"""Step 3.4: normalized property-grain rows for the main contract pipeline."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .models import ExtractionResult


PROPERTY_TABLE_VERSION = "3.4"
PROPERTY_OVERRIDE_FIELDS = (
    "property_name",
    "property_display_name",
    "property_article",
    "property_section",
    "property_matrix_key",
    "property_parish",
    "property_municipality",
    "property_district",
    "property_total_area",
    "leased_parcel_area",
    "property_location",
    "property_address",
    "owner_name",
    "owner_tax_id",
    "owner_address",
)


def build_property_table_rows(result: ExtractionResult) -> list[dict[str, Any]]:
    """Expand one contract result into one independently auditable row per property.

    Contract-level values are copied to every row. Property and cadastral
    values are reset first and then populated only from one aligned
    ``cadastral_properties`` item, preventing cross-property field leakage.
    """
    base = _result_dict(result)
    properties = _structured_properties(result)
    if not properties:
        return [_unresolved_row(base)]

    matched = [item for item in properties if item.get("matched_contract_identity") is True]
    selected = matched if matched else properties
    selected = _deduplicate_properties(selected)
    property_count = len(selected)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        row = deepcopy(base)
        for field_name in PROPERTY_OVERRIDE_FIELDS:
            row[field_name] = ""

        matrix_key = str(item.get("matrix_key") or item.get("property_matrix_key") or "").strip()
        row.update({
            "property_table_version": PROPERTY_TABLE_VERSION,
            "property_row_id": matrix_key or f"property-{index:03d}",
            "property_index": index,
            "property_count": property_count,
            "property_row_status": "resolved" if matrix_key else "needs_review",
            "property_match_status": (
                "contract_identity_match"
                if item.get("matched_contract_identity") is True
                else "internal_annex_unconfirmed"
            ),
            "property_source_pages": "; ".join(str(page) for page in item.get("pages", []) if page is not None),
            "property_structure_status": str(item.get("structure_status") or "verified"),
            "property_owner_status": str(item.get("owner_status") or ("verified" if item.get("owner_name") else "owner_not_found")),
            "property_source_file": str(item.get("source_file") or "internal_contract_annex"),
            "property_json": dict(item),
            "property_name": str(item.get("property_name") or ""),
            "property_display_name": str(item.get("property_name") or ""),
            "property_article": str(item.get("property_article") or item.get("matrix_article") or ""),
            "property_section": str(item.get("property_section") or item.get("matrix_section") or ""),
            "property_matrix_key": matrix_key,
            "property_parish": str(item.get("property_parish") or ""),
            "property_municipality": str(item.get("property_municipality") or ""),
            "property_district": str(item.get("property_district") or ""),
            "property_total_area": str(item.get("property_total_area") or ""),
            "property_total_area_m2": _area_m2(item),
            "leased_parcel_area": str(item.get("leased_parcel_area") or "") if property_count == 1 else "",
            "owner_name": str(item.get("owner_name") or ""),
            "owner_tax_id": str(item.get("owner_tax_id") or ""),
        })
        if row["property_match_status"] == "internal_annex_unconfirmed":
            row["needs_review"] = "yes"
            row["human_review_required"] = "yes"
            row["review_reason"] = _join_reason(row.get("review_reason"), "property_contract_identity_unconfirmed")
        row["raw_json"] = _with_row_audit(row.get("raw_json"), row)
        rows.append(row)
    return rows


def _result_dict(result: ExtractionResult) -> dict[str, Any]:
    return {
        field_name: deepcopy(value)
        for field_name, value in vars(result).items()
    }


def _structured_properties(result: ExtractionResult) -> list[dict[str, Any]]:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    step33 = raw.get("step3_3_field_centric", {})
    properties = step33.get("cadastral_properties", []) if isinstance(step33, dict) else []
    return [dict(item) for item in properties if isinstance(item, dict)]


def _deduplicate_properties(properties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_identity: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for item in properties:
        matrix_key = str(item.get("matrix_key") or item.get("property_matrix_key") or "").strip().upper()
        if not matrix_key:
            anonymous.append(item)
            continue
        current = by_identity.get(matrix_key)
        if current is None:
            by_identity[matrix_key] = item
            continue
        pages = list(dict.fromkeys([*current.get("pages", []), *item.get("pages", [])]))
        merged = {**current, **{key: value for key, value in item.items() if value not in (None, "", [])}}
        merged["pages"] = pages
        by_identity[matrix_key] = merged
    return [*by_identity.values(), *anonymous]


def _unresolved_row(base: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(base)
    for field_name in PROPERTY_OVERRIDE_FIELDS:
        row[field_name] = ""
    row.update({
        "property_table_version": PROPERTY_TABLE_VERSION,
        "property_row_id": "unresolved",
        "property_index": 0,
        "property_count": 0,
        "property_row_status": "unresolved_no_property",
        "property_match_status": "not_found",
        "property_source_pages": "",
        "property_structure_status": "not_found",
        "property_owner_status": "owner_not_found",
        "property_source_file": "",
        "property_json": {},
        "property_total_area_m2": None,
        "needs_review": "yes",
        "human_review_required": "yes",
        "review_reason": _join_reason(base.get("review_reason"), "property_table_no_resolved_property"),
    })
    row["raw_json"] = _with_row_audit(row.get("raw_json"), row)
    return row


def _area_m2(item: dict[str, Any]) -> float | int | None:
    direct = item.get("property_total_area_m2")
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return direct
    value = str(item.get("property_total_area") or "")
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(hectares?|ha|m2|m²)\s*", value, re.I)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return number * 10_000 if match.group(2).lower() in {"ha", "hectare", "hectares"} else number


def _join_reason(existing: object, reason: str) -> str:
    values = [part.strip() for part in str(existing or "").split(";") if part.strip()]
    if reason not in values:
        values.append(reason)
    return "; ".join(values)


def _with_row_audit(raw_json: object, row: dict[str, Any]) -> dict[str, Any]:
    raw = deepcopy(raw_json) if isinstance(raw_json, dict) else {}
    raw["step3_4_property_table"] = {
        "version": PROPERTY_TABLE_VERSION,
        "property_row_id": row.get("property_row_id", ""),
        "property_index": row.get("property_index", 0),
        "property_count": row.get("property_count", 0),
        "property_row_status": row.get("property_row_status", ""),
        "property_match_status": row.get("property_match_status", ""),
        "property_matrix_key": row.get("property_matrix_key", ""),
        "property_source_pages": row.get("property_source_pages", ""),
    }
    return raw


__all__ = ["PROPERTY_TABLE_VERSION", "build_property_table_rows"]
