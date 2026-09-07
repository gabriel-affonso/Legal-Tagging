"""Versioned canonical records for the Step 5.5 pilot."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "5.5.0"


def stable_id(*parts: object) -> str:
    return sha256("\x1f".join(str(value) for value in parts).encode("utf-8")).hexdigest()[:24]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceSpan(StrictModel):
    id: str
    page: int = Field(ge=1)
    region_id: str
    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    bbox: tuple[float, float, float, float] | None = None
    reading_method: Literal["native", "ocr", "visual"]
    dependency_id: str

    @model_validator(mode="after")
    def valid_offsets(self):
        if self.end <= self.start or self.end > len(self.text):
            raise ValueError("invalid_evidence_offsets")
        return self

    @property
    def quote(self) -> str:
        return self.text[self.start:self.end]


class LogicalDocument(StrictModel):
    id: str
    source_type: Literal[
        "lease_contract", "payment_receipt", "payment_correspondence",
        "bank_payment_confirmation", "cadastral_record", "land_registry_record",
        "privacy_notice", "identity_document", "site_plan", "unknown",
    ]
    region_ids: list[str] = Field(min_length=1)
    page_numbers: list[int] = Field(min_length=1)
    classification_reason: str
    page_state: Literal["processed", "low_text", "unreadable", "continuation_uncertain"] = "processed"


class Entity(StrictModel):
    id: str
    kind: Literal["contract", "person", "organization", "property", "lease_object", "payment", "financial_obligation", "event_definition"]
    label: str = Field(min_length=1)
    logical_document_id: str
    identity_status: Literal["provisional", "resolved", "conflict"] = "provisional"
    evidence_ids: list[str] = Field(min_length=1)


class AreaValue(StrictModel):
    amount: str
    unit: Literal["m2", "ha"]

    @field_validator("amount")
    @classmethod
    def decimal_amount(cls, value: str) -> str:
        try:
            number = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("invalid_decimal") from exc
        if not number.is_finite() or number < 0:
            raise ValueError("invalid_decimal")
        return format(number.normalize(), "f")


class Money(StrictModel):
    amount: str
    currency: Literal["EUR"] = "EUR"

    @field_validator("amount")
    @classmethod
    def decimal_amount(cls, value: str) -> str:
        return AreaValue(amount=value, unit="m2").amount


class Duration(StrictModel):
    kind: Literal["duration"] = "duration"
    years: int = Field(default=0, ge=0)
    months: int = Field(default=0, ge=0, le=11)
    days: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def nonzero(self):
        if self.years + self.months + self.days == 0:
            raise ValueError("empty_duration")
        return self


class LiteralDate(StrictModel):
    kind: Literal["literal_date"] = "literal_date"
    value: date
    precision: Literal["day"] = "day"


class EventReference(StrictModel):
    kind: Literal["event_reference"] = "event_reference"
    event_name: str = Field(min_length=1)


TemporalValue = Annotated[Duration | LiteralDate | EventReference, Field(discriminator="kind")]


class Assertion(StrictModel):
    id: str
    subject_id: str
    predicate: str
    raw_value: str
    typed_value: Any
    evidence_ids: list[str] = Field(min_length=1)
    scope: dict[str, str] = Field(default_factory=dict)
    source_authority: Literal["contract", "cadastral_record", "correspondence", "receipt", "unknown"]
    extractor_id: str
    extraction_status: Literal["observed", "proposed", "validated"] = "validated"
    resolution_status: Literal["unresolved", "resolved", "conflict"] = "unresolved"
    acceptance_status: Literal["needs_review", "accepted_automatic", "accepted_human", "rejected"] = "needs_review"
    validation_issues: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)


class Relation(StrictModel):
    id: str
    subject_id: str
    predicate: Literal["has_role", "represents", "has_property", "measures", "has_obligation", "reports_payment"]
    object_id: str
    evidence_ids: list[str] = Field(min_length=1)
    status: Literal["proposed", "resolved", "needs_review"] = "proposed"


class ExtractionTask(StrictModel):
    id: str
    task_type: Literal["parts", "properties", "temporal", "financial"]
    logical_document_id: str
    evidence_ids: list[str] = Field(min_length=1)
    permitted_predicates: list[str] = Field(min_length=1)
    reason: str
    status: Literal["planned", "not_needed", "blocked"] = "planned"


class CanonicalResult(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    source_sha256: str = Field(min_length=64, max_length=64)
    source_path: str
    logical_documents: list[LogicalDocument]
    evidence: list[EvidenceSpan]
    entities: list[Entity]
    assertions: list[Assertion]
    relations: list[Relation]
    tasks: list[ExtractionTask]
    coverage: dict[str, str]
    issues: list[str] = Field(default_factory=list)
    metrics: dict[str, int | float | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references_exist(self):
        evidence_ids = {item.id for item in self.evidence}
        entity_ids = {item.id for item in self.entities}
        logical_ids = {item.id for item in self.logical_documents}
        for entity in self.entities:
            if entity.logical_document_id not in logical_ids or not set(entity.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_entity_reference")
        for assertion in self.assertions:
            if assertion.subject_id not in entity_ids or not set(assertion.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_assertion_reference")
        for relation in self.relations:
            if relation.subject_id not in entity_ids or relation.object_id not in entity_ids or not set(relation.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_relation_reference")
        for task in self.tasks:
            if task.logical_document_id not in logical_ids or not set(task.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_task_reference")
        return self
