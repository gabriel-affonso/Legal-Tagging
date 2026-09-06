"""Versioned observations. Source authority is independent of reading method."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any, Literal

SCHEMA_VERSION = '5.0.0'
Status = Literal['accepted', 'needs_review', 'conflict', 'not_found', 'not_applicable', 'illegible', 'technical_error']
FIELDS = {
    'name', 'tax_id', 'role', 'represented_by', 'property_article', 'property_section',
    'property_parish', 'property_municipality', 'property_district', 'property_name',
    'registry_description', 'property_total_area', 'leased_parcel_area', 'owner',
    'signed_date', 'contract_start_date', 'contract_end_date', 'rent', 'purchase_price',
    'option_price', 'assignment_price', 'iban', 'bank_account_holder', 'contract_type',
    'amends', 'effective_date', 'signature_present',
}
CRITICAL = {'name', 'tax_id', 'role', 'owner', 'signed_date', 'rent', 'iban', 'property_article'}
ROLES = {'lessor', 'lessee', 'representative', 'cadastral_owner', 'registered_owner', 'assignor', 'assignee'}


def identity(*values: Any) -> str:
    return sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def name_key(value: str) -> str:
    # No fuzzy matching, accent removal, suffix removal or identity completion.
    return ' '.join(value.casefold().split())


@dataclass
class Document:
    id: str
    sha256: str
    original_path: str
    page_count: int = 0
    status: str = 'inventoried'


@dataclass
class Region:
    id: str
    page: int
    bbox: list[float]
    text: str
    method: str
    source_type: str = 'unknown'
    logical_document: str = ''
    coordinate_system: str = 'unrotated_page_points_top_left'
    granularity: str = 'block'
    image: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class Page:
    number: int
    width: float
    height: float
    rotation: int
    status: str
    regions: list[Region] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    duplicate_of: int | None = None


@dataclass
class Segment:
    id: str
    logical_document: str
    source_type: str
    region_ids: list[str]
    text: str


@dataclass
class Entity:
    id: str
    label: str
    kind: str
    logical_document: str


@dataclass
class Relationship:
    subject: str
    predicate: str
    object: str
    candidate_ids: list[str]
    status: str
    logical_document: str = ''


@dataclass
class Candidate:
    id: str
    field: str
    entity: str
    logical_document: str
    raw_value: Any
    value: Any
    region_id: str
    quote: str
    start: int | None
    end: int | None
    source_type: str
    method: str
    extractor: str
    normalization: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    probability: float | None = None
    source_confidence: float | None = None
    modality: str = 'unknown'
    # Multiple extractors over one region remain ONE dependent observation.
    dependency_id: str = ''


@dataclass
class FieldResult:
    field: str
    entity: str
    logical_document: str
    status: Status
    value: Any = None
    candidate_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    acceptance: str | None = None


@dataclass
class ProcessingResult:
    document: Document
    run_id: str
    config_fingerprint: str
    schema_version: str = SCHEMA_VERSION
    pages: list[Page] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    fields: list[FieldResult] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    incomplete_coverage: bool = False
    fully_approved: bool = False
    metrics: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def decimal_pt(value: Any) -> str:
    raw = str(value).strip().replace('\u00a0', '').replace(' ', '')
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?', raw):
        raise ValueError('invalid_portuguese_decimal')
    return format(Decimal(raw.replace('.', '').replace(',', '.')).normalize(), 'f')


def normalize(field_name: str, value: Any) -> tuple[Any, list[str]]:
    """Pure strict validators. Never recover missing digits, units or relations."""
    from ..step40 import valid_nif, valid_iban
    if field_name not in FIELDS:
        raise ValueError('unknown_field')
    if field_name in {'rent', 'purchase_price', 'option_price', 'assignment_price', 'property_total_area', 'leased_parcel_area'}:
        if not isinstance(value, dict):
            raise ValueError('structured_value_required')
        required = ({'amount', 'currency', 'frequency', 'basis', 'condition'} if field_name == 'rent' else
                    {'amount', 'unit'} if 'area' in field_name else {'amount', 'currency'})
        if set(value) != required or not all(isinstance(v, str) for v in value.values()):
            raise ValueError('incomplete_or_extra_dimensions')
        out = dict(value)
        out['amount'] = decimal_pt(out['amount'])
        if 'currency' in out and out['currency'] != 'EUR':
            raise ValueError('unsupported_currency')
        if 'unit' in out and out['unit'] not in {'ha', 'm2'}:
            raise ValueError('unsupported_area_unit')
        if field_name == 'rent' and (out['frequency'] not in {'annual', 'monthly', 'other'} or out['basis'] not in {'total', 'per_ha', 'other'}):
            raise ValueError('ambiguous_rent_dimensions')
        return out, ['decimal_pt_to_decimal_string; dimensions_preserved']
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonempty_string_required')
    out = ' '.join(value.split())
    if field_name == 'tax_id':
        if not re.fullmatch(r'[0-9 ]{9,15}', out):
            raise ValueError('invalid_nif_characters')
        out = out.replace(' ', '')
        if not valid_nif(out):
            raise ValueError('invalid_nif_checksum')
    elif field_name == 'iban':
        out = out.replace(' ', '').upper()
        if not re.fullmatch(r'PT50\d{21}', out) or not valid_iban(out):
            raise ValueError('invalid_portuguese_iban')
    elif field_name.endswith('_date'):
        match = re.fullmatch(r'(\d{2})[/-](\d{2})[/-](\d{4})', out)
        if match:
            out = date(int(match[3]), int(match[2]), int(match[1])).isoformat()
        else:
            out = date.fromisoformat(out).isoformat()
    elif field_name == 'property_section':
        out = out.upper()
        if not re.fullmatch(r'[A-Z]{1,3}', out):
            raise ValueError('invalid_section')
    elif field_name == 'role' and out not in ROLES:
        raise ValueError('invalid_role')
    elif field_name == 'name':
        if len(out.split()) < 2 or name_key(out) in {'primeiro outorgante', 'segundo outorgante'} or re.search(r'\d|[:;]', out):
            raise ValueError('invalid_party_name')
    return out, ['strict_whitespace_type_normalization']
