"""Materialize lease contracts as one auditable row per cadastral property.

The Property Table is a projection of already-resolved contract evidence.  It
must never turn a bank attachment or a standalone cadastral document into an
unresolved property row merely because it happened to be scanned.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable, Mapping

from .models import ExtractionResult
from .property_pipeline import is_valid_matrix_article, is_valid_matrix_section


PROPERTY_TABLE_VERSION = "3.5.1"
PROPERTY_TABLE_ELIGIBLE_CATEGORIES = frozenset({"lease_contract"})
PROPERTY_OVERRIDE_FIELDS = (
    "property_number", "property_name", "property_display_name", "property_article",
    "property_section", "property_matrix_key", "property_parish", "property_municipality",
    "property_district", "property_total_area", "leased_parcel_area", "property_location",
    "property_address", "owner_name", "owner_tax_id", "owner_address",
)


def build_property_table_rows(result: ExtractionResult) -> list[dict[str, Any]]:
    """Return normalized property rows for an eligible lease contract only.

    The source order prefers structured contract resolution and independent
    Property Extraction output over ad-hoc scalar fields. A missing property
    remains visible as one control row *for a contract*, but non-contract
    documents return no rows at all.
    """
    if not _is_eligible_contract(result):
        return []
    base = _result_dict(result)
    properties = _deduplicate_properties(_structured_properties(result))
    if not properties:
        return [_unresolved_row(base)]
    selected = _select_contract_properties(properties, result)
    if not selected:
        return [_unresolved_row(base)]

    rows: list[dict[str, Any]] = []
    property_count = len(selected)
    for index, item in enumerate(selected, start=1):
        row = deepcopy(base)
        for field_name in PROPERTY_OVERRIDE_FIELDS:
            row[field_name] = ""
        matrix_key = str(item["property_matrix_key"])
        association_status = _association_status(item, result)
        property_name = str(item.get("property_name") or "")
        name_is_plausible = _is_plausible_property_name(property_name)
        if property_name and not name_is_plausible:
            property_name = ""
        needs_review = association_status != "confirmed" or not name_is_plausible
        row.update({
            "property_table_version": PROPERTY_TABLE_VERSION,
            "property_row_id": matrix_key,
            "property_index": index,
            "property_count": property_count,
            "property_row_status": "needs_review" if needs_review else "resolved",
            "property_match_status": "contract_identity_match" if association_status == "confirmed" else "internal_annex_unconfirmed",
            "property_association_status": association_status,
            "property_association_evidence": "; ".join(
                _strings(item.get("association_evidence"))
                or ([str(item.get("source"))] if association_status == "confirmed" else [])
            ),
            "contract_operational_property_id": str(item.get("contract_operational_property_id") or item.get("contract_property_number") or item.get("property_number") or base.get("property_number") or ""),
            "property_source_pages": "; ".join(str(page) for page in _pages(item) if page is not None),
            "property_structure_status": str(item.get("structure_status") or "partially_verified"),
            "property_owner_status": str(item.get("owner_status") or ("verified" if item.get("owner_name") else "owner_not_found")),
            "property_source_file": str(item.get("source_file") or item.get("source") or "structured_contract_evidence"),
            "property_json": dict(item),
            "property_number": str(item.get("contract_property_number") or item.get("property_number") or base.get("property_number") or ""),
            "property_name": property_name,
            "property_display_name": property_name,
            "property_article": str(item.get("property_article") or ""),
            "property_section": str(item.get("property_section") or ""),
            "property_matrix_key": matrix_key,
            "property_parish": str(item.get("property_parish") or ""),
            "property_municipality": str(item.get("property_municipality") or ""),
            "property_district": str(item.get("property_district") or ""),
            "property_total_area": str(item.get("property_total_area") or ""),
            "property_total_area_m2": _area_m2(item),
            "leased_parcel_area": str(item.get("leased_parcel_area") or "") if property_count == 1 else "",
            "property_location": str(item.get("property_location") or ""),
            "property_address": str(item.get("property_address") or ""),
            "owner_name": str(item.get("owner_name") or ""),
            "owner_tax_id": str(item.get("owner_tax_id") or ""),
            "owner_address": str(item.get("owner_address") or ""),
        })
        if needs_review:
            row["needs_review"] = "yes"
            row["human_review_required"] = "yes"
        if association_status != "confirmed":
            row["review_reason"] = _join_reason(row.get("review_reason"), "property_contract_identity_unconfirmed")
        if not name_is_plausible:
            row["review_reason"] = _join_reason(row.get("review_reason"), "property_name_invalid_or_missing")
        row["raw_json"] = _with_row_audit(row.get("raw_json"), row)
        rows.append(row)
    return rows


def _is_eligible_contract(result: ExtractionResult) -> bool:
    return str(result.document_category or "").strip().lower() in PROPERTY_TABLE_ELIGIBLE_CATEGORIES


def _result_dict(result: ExtractionResult) -> dict[str, Any]:
    return {field_name: deepcopy(value) for field_name, value in vars(result).items()}


def _structured_properties(result: ExtractionResult) -> list[dict[str, Any]]:
    """Collect property lists from known audited sources in precedence order."""
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    source_lists: tuple[tuple[str, object], ...] = (
        ("final_contract_resolution", _nested_list(raw, "final_contract_resolution", "properties")),
        ("final_contract_resolution", _nested_list(raw, "step2_7_final_resolution", "properties")),
        ("property_extraction", _nested_list(raw, "property_extraction", "properties")),
        ("property_extraction", raw.get("property_extraction_properties")),
        ("field_centric", _nested_list(raw, "step3_3_field_centric", "cadastral_properties")),
        ("internal_caderneta", raw.get("internal_caderneta_groups")),
        ("property_pack", _nested_list(raw, "property_pack", "properties")),
    )
    for source, items in source_lists:
        normalized = [_normalize_property(item, source) for item in _mappings(items)]
        normalized = [item for item in normalized if item is not None]
        if normalized:
            return normalized
    singular = _normalize_property({
        "property_name": result.property_name, "property_article": result.property_article,
        "property_section": result.property_section, "property_matrix_key": result.property_matrix_key,
        "property_parish": result.property_parish, "property_municipality": result.property_municipality,
        "property_district": result.property_district, "property_total_area": result.property_total_area,
        "owner_name": result.owner_name, "owner_tax_id": result.owner_tax_id,
        "property_number": result.property_number,
    }, "resolved_contract")
    return [singular] if singular else []


def _nested_list(raw: Mapping[str, Any], parent: str, key: str) -> object:
    value = raw.get(parent)
    return value.get(key) if isinstance(value, Mapping) else []


def _mappings(value: object) -> Iterable[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        evidence = item.get("evidence")
        values = evidence.get("values") if isinstance(evidence, Mapping) else None
        if isinstance(values, Mapping):
            output.append({**values, "property_matrix_key": evidence.get("matrix_key"), "pages": item.get("pages") or evidence.get("page_numbers"), "structure_status": evidence.get("structure_status"), "owner_status": evidence.get("owner_status")})
        else:
            output.append(item)
    return output


def _normalize_property(item: Mapping[str, Any], source: str) -> dict[str, Any] | None:
    article = str(item.get("property_article") or item.get("matrix_article") or "").strip().upper().replace(" ", "")
    section = str(item.get("property_section") or item.get("matrix_section") or "").strip().upper().replace(" ", "")
    supplied_key = str(item.get("matrix_key") or item.get("property_matrix_key") or "").strip().upper().replace(" ", "")
    key_match = re.fullmatch(r"(\d{1,10})-([A-Z])", supplied_key)
    if key_match and not article and not section:
        article, section = key_match.groups()
    if not (is_valid_matrix_article(article) and is_valid_matrix_section(section)):
        return None
    return {**dict(item), "property_article": article, "property_section": section, "property_matrix_key": f"{article}-{section}", "source": source, "pages": _pages(item)}


def _select_contract_properties(properties: list[dict[str, Any]], result: ExtractionResult) -> list[dict[str, Any]]:
    matched = [item for item in properties if _association_status(item, result) == "confirmed"]
    # If an explicit contract matrix exists, never publish unmatched annexes.
    return matched if matched else properties


def _association_status(item: Mapping[str, Any], result: ExtractionResult) -> str:
    declared = str(item.get("association_status") or "").strip().lower()
    if declared in {"confirmed", "contract_identity_match", "matched"} or item.get("matched_contract_identity") is True:
        return "confirmed"
    if item.get("source") in {"final_contract_resolution", "property_extraction"}:
        return "confirmed"
    if str(result.property_matrix_key or "").strip().upper() == str(item.get("property_matrix_key") or "").strip().upper():
        return "confirmed"
    return "unconfirmed"


def _deduplicate_properties(properties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in properties:
        key = str(item["property_matrix_key"])
        current = merged.get(key)
        if current is None:
            merged[key] = dict(item)
            continue
        preferred, supplemental = sorted((current, item), key=_property_completeness, reverse=True)
        combined = dict(preferred)
        for field, value in supplemental.items():
            if combined.get(field) in (None, "", [], {}):
                combined[field] = value
        combined["pages"] = list(dict.fromkeys([*_pages(current), *_pages(item)]))
        combined["association_evidence"] = list(dict.fromkeys([*_strings(current.get("association_evidence")), *_strings(item.get("association_evidence"))]))
        combined["matched_contract_identity"] = bool(current.get("matched_contract_identity") or item.get("matched_contract_identity"))
        merged[key] = combined
    return list(merged.values())


def _property_completeness(item: Mapping[str, Any]) -> int:
    fields = ("property_name", "property_total_area", "area_m2", "owner_name", "owner_tax_id", "property_parish")
    return sum(value not in (None, "", [], {}) for value in (item.get(field) for field in fields))


def _pages(item: Mapping[str, Any]) -> list[Any]:
    values = item.get("pages") or item.get("page_numbers") or []
    return list(values) if isinstance(values, (list, tuple)) else []


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _is_plausible_property_name(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    if not normalized:
        return True
    if len(normalized) < 3 or len(normalized) > 120:
        return False
    blocked = ("encargos", "potenciais", "doravante", "objeto do contrato", "sobre o predio", "sobre o prédio", "designado como predio", "designado como prédio")
    return not any(term in normalized.casefold() for term in blocked)


def _unresolved_row(base: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(base)
    for field_name in PROPERTY_OVERRIDE_FIELDS:
        row[field_name] = ""
    row.update({
        "property_table_version": PROPERTY_TABLE_VERSION, "property_row_id": "unresolved",
        "property_index": 0, "property_count": 0, "property_row_status": "unresolved_no_property",
        "property_match_status": "not_found", "property_association_status": "not_found",
        "property_association_evidence": "", "contract_operational_property_id": str(base.get("property_number") or ""),
        "property_source_pages": "", "property_structure_status": "not_found",
        "property_owner_status": "owner_not_found", "property_source_file": "", "property_json": {},
        "property_total_area_m2": None, "needs_review": "yes", "human_review_required": "yes",
        "review_reason": _join_reason(base.get("review_reason"), "property_table_no_resolved_property"),
    })
    row["raw_json"] = _with_row_audit(row.get("raw_json"), row)
    return row


def _area_m2(item: Mapping[str, Any]) -> float | int | None:
    direct = item.get("property_total_area_m2") or item.get("area_m2")
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


def _with_row_audit(raw_json: object, row: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(raw_json) if isinstance(raw_json, dict) else {}
    raw["step3_5_property_table"] = {
        "version": PROPERTY_TABLE_VERSION, "property_row_id": row.get("property_row_id", ""),
        "property_index": row.get("property_index", 0), "property_count": row.get("property_count", 0),
        "property_row_status": row.get("property_row_status", ""), "property_match_status": row.get("property_match_status", ""),
        "property_matrix_key": row.get("property_matrix_key", ""), "property_source_pages": row.get("property_source_pages", ""),
        "property_association_status": row.get("property_association_status", ""),
    }
    return raw


__all__ = ["PROPERTY_TABLE_ELIGIBLE_CATEGORIES", "PROPERTY_TABLE_VERSION", "build_property_table_rows"]
