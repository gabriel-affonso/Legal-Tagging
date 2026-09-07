"""Versioned canonical records for the Step 5.5 pilot."""
from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .values import AreaValue, Money, Duration, LiteralDate, EventReference

SCHEMA_VERSION = "5.5.1"


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
    parent_id: str | None = None
    attachment_label: str | None = None


class Entity(StrictModel):
    id: str
    kind: Literal["contract", "person", "organization", "property", "lease_object", "payment", "financial_obligation", "event_definition", "event_occurrence"]
    label: str = Field(min_length=1)
    logical_document_id: str
    identity_status: Literal["provisional", "resolved", "conflict"] = "provisional"
    evidence_ids: list[str] = Field(min_length=1)


TemporalValue = Annotated[Duration | LiteralDate | EventReference, Field(discriminator="kind")]


def assertion_json_schema(schema):
    from .values import VALUE_MODELS, TEXT_FIELDS
    def inline(root):
        defs=root.get('$defs',{})
        def expand(item):
            if isinstance(item,list):return [expand(x) for x in item]
            if not isinstance(item,dict):return item
            if '$ref' in item:return expand(defs[item['$ref'].split('/')[-1]])
            return {k:expand(v) for k,v in item.items() if k!='$defs'}
        return expand(root)
    schema['allOf']=[{'anyOf':[
        {'properties':{'predicate':{'const':predicate},'typed_value':inline(model.model_json_schema())}}
        for predicate,model in sorted(VALUE_MODELS.items())
    ]+[{'properties':{'predicate':{'enum':sorted(TEXT_FIELDS)},'typed_value':{'type':'string','minLength':1}}}]}]


class Assertion(StrictModel):
    model_config=ConfigDict(extra='forbid',strict=True,json_schema_extra=assertion_json_schema)
    id: str
    subject_id: str
    predicate: str
    raw_value: str
    typed_value: Any
    evidence_ids: list[str] = Field(min_length=1)
    scope: dict[str, str] = Field(default_factory=dict)
    evidence_offsets: dict[str, tuple[int, int]] = Field(default_factory=dict)
    source_authority: Literal["contract", "cadastral_record", "correspondence", "receipt", "unknown"]
    extractor_id: str
    extraction_status: Literal["observed", "proposed", "validated"] = "validated"
    resolution_status: Literal["unresolved", "resolved", "conflict"] = "unresolved"
    acceptance_status: Literal["needs_review", "accepted_automatic", "accepted_human", "rejected"] = "needs_review"
    validation_issues: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    origin_kind: Literal['documental', 'derivado', 'externo', 'operacional', 'humano'] = 'documental'
    normalization_steps: list[str] = Field(default_factory=list)
    acceptance_basis: str | None = None

    @model_validator(mode='after')
    def value_matches_predicate(self):
        from .values import validate_value
        self.typed_value = validate_value(self.predicate, self.typed_value)
        if self.acceptance_status.startswith('accepted_') and (not self.acceptance_basis or self.resolution_status != 'resolved'):
            raise ValueError('acceptance_requires_resolution_and_policy')
        if self.acceptance_status == 'accepted_automatic' and self.validation_issues:
            raise ValueError('automatic_acceptance_with_issues')
        return self


class Relation(StrictModel):
    id: str
    subject_id: str
    predicate: Literal["has_role", "represents", "has_property", "measures", "has_obligation", "reports_payment",
                       "payer", "payee", "sender", "recipient", "defines_event", "occurrence_of", "same_as", "possible_same_as", "has_lease_object", "references_property"]
    object_id: str
    evidence_ids: list[str] = Field(min_length=1)
    status: Literal["proposed", "resolved", "needs_review", "rejected"] = "proposed"
    role: str | None = None
    reason: str | None = None


class ExtractionTask(StrictModel):
    id: str
    task_type: Literal["parts", "properties", "temporal", "financial"]
    logical_document_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    permitted_predicates: list[str] = Field(min_length=1)
    reason: str
    status: Literal["planned", "not_needed", "blocked", "completed", "technical_error", "deferred", "rejected"] = "planned"
    entity_ids: list[str] = Field(default_factory=list)
    priority: int = 0
    max_input_chars: int = 9000
    max_output_tokens: int = 768
    timeout_seconds: int = 60
    attempts: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def task_context(self):
        if self.status == 'planned' and not self.evidence_ids:
            raise ValueError('planned_task_requires_context')
        return self


class Derivation(StrictModel):
    id: str
    subject_id: str
    operation: str
    input_ids: list[str]
    value: Any = None
    status: Literal['calculated', 'blocked', 'conflict']
    reason: str | None = None
    origin_kind: Literal['derivado'] = 'derivado'


class ResolutionDecision(StrictModel):
    id: str
    target_id: str
    action: str
    reason: str
    author: str
    at: str
    evidence_ids: list[str] = Field(default_factory=list)


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
    run_id: str = ''
    observations: list[dict] = Field(default_factory=list)
    pages: list[dict] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    decisions: list[ResolutionDecision] = Field(default_factory=list)
    derivations: list[Derivation] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_exist(self):
        from .values import allowed_subjects, document_predicates
        for collection in (self.entities, self.assertions, self.relations, self.evidence, self.logical_documents, self.tasks, self.decisions, self.derivations):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError('duplicate_record_id')
        evidence_ids = {item.id for item in self.evidence}
        evidence_by_id = {item.id: item for item in self.evidence}
        entity_ids = {item.id for item in self.entities}
        kinds = {item.id: item.kind for item in self.entities}
        logical_ids = {item.id for item in self.logical_documents}
        document_types = {item.id:item.source_type for item in self.logical_documents}
        entities_by_id={item.id:item for item in self.entities}
        assertion_ids = {item.id for item in self.assertions}
        for doc in self.logical_documents:
            if doc.parent_id and (doc.parent_id not in logical_ids or doc.parent_id == doc.id):
                raise ValueError('invalid_document_parent')
        for entity in self.entities:
            if entity.logical_document_id not in logical_ids or not set(entity.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_entity_reference")
        for assertion in self.assertions:
            if assertion.subject_id not in entity_ids or not set(assertion.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_assertion_reference")
            if kinds[assertion.subject_id] not in allowed_subjects(assertion.predicate):
                raise ValueError('predicate_subject_type_mismatch')
            source_type=document_types[entities_by_id[assertion.subject_id].logical_document_id]
            if assertion.origin_kind=='documental' and assertion.predicate not in document_predicates(source_type):
                raise ValueError('predicate_document_type_mismatch')
            if assertion.predicate=='area_measurement' and assertion.typed_value['concept'] in {'cadastral_total','registry_total','contract_stated_property_total'} and kinds[assertion.subject_id]!='property':
                raise ValueError('property_total_requires_property_subject')
            for eid, (start, end) in assertion.evidence_offsets.items():
                if eid not in assertion.evidence_ids:
                    raise ValueError('invalid_assertion_offset_reference')
                ev=evidence_by_id[eid]
                if not ev.start <= start < end <= ev.end or ev.text[start:end] != assertion.raw_value:
                    raise ValueError('assertion_quote_offset_mismatch')
            for key, value in assertion.scope.items():
                if key.endswith('_id') and value not in entity_ids | logical_ids:
                    raise ValueError('invalid_scope_reference')
            if isinstance(assertion.typed_value, dict):
                for key, value in assertion.typed_value.items():
                    if value and key in {'event_id', 'anchor_event_id', 'base_obligation_id', 'start_event_ref', 'end_event_ref', 'holder_id', 'obligation_id'} and value not in entity_ids:
                        raise ValueError('invalid_value_reference')
                    if value and key == 'area_basis_ref' and value not in assertion_ids:
                        raise ValueError('invalid_area_basis_reference')
                    expected = {'event_id': {'event_definition'}, 'anchor_event_id': {'event_definition'},
                                'start_event_ref': {'event_definition'}, 'end_event_ref': {'event_definition'},
                                'holder_id': {'person', 'organization'}, 'base_obligation_id': {'financial_obligation'},
                                'obligation_id': {'financial_obligation'}}.get(key)
                    if value and expected and kinds.get(value) not in expected:
                        raise ValueError('value_reference_kind_mismatch')
        for relation in self.relations:
            if relation.subject_id not in entity_ids or relation.object_id not in entity_ids or not set(relation.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_relation_reference")
            signatures = {
                'has_role': ({'person', 'organization'}, {'contract'}),
                'represents': ({'person', 'organization'}, {'person', 'organization'}),
                'has_property': ({'contract'}, {'property'}),
                'has_lease_object': ({'contract'}, {'lease_object'}),
                'references_property': ({'payment'}, {'property'}),
                'has_obligation': ({'contract'}, {'financial_obligation'}),
                'defines_event': ({'contract'}, {'event_definition'}),
                'occurrence_of': ({'event_occurrence'}, {'event_definition'}),
                **{p: ({'payment'}, {'person', 'organization'}) for p in ('payer','payee','sender','recipient')},
            }
            pair = signatures.get(relation.predicate)
            if pair and (kinds[relation.subject_id] not in pair[0] or kinds[relation.object_id] not in pair[1]):
                raise ValueError('relation_type_mismatch')
            if relation.predicate in {'same_as', 'possible_same_as'} and (kinds[relation.subject_id] != kinds[relation.object_id] or relation.subject_id == relation.object_id):
                raise ValueError('invalid_identity_relation')
            if relation.predicate == 'has_role' and relation.role not in {'lessor','lessee','representative'}:
                raise ValueError('relation_role_required')
        for task in self.tasks:
            if task.logical_document_id not in logical_ids or not set(task.evidence_ids) <= evidence_ids:
                raise ValueError("invalid_task_reference")
            if not set(task.entity_ids) <= entity_ids:
                raise ValueError('invalid_task_entity')
        observations = {item['id']: item for item in self.observations}
        for derivation in self.derivations:
            if derivation.subject_id not in entity_ids or not set(derivation.input_ids) <= assertion_ids:
                raise ValueError('invalid_derivation_reference')
        dependencies={}
        for assertion in self.assertions:
            if assertion.acceptance_status=='rejected' or not isinstance(assertion.typed_value,dict):continue
            base=assertion.typed_value.get('base_obligation_id')
            if base:dependencies.setdefault(assertion.subject_id,set()).add(base)
        def visit(node,path):
            if node in path:raise ValueError('obligation_dependency_cycle')
            for parent in dependencies.get(node,()):visit(parent,path|{node})
        for node in dependencies:visit(node,set())
        if observations:
            for item in self.evidence:
                source = observations.get(item.region_id)
                if not source or source['text'] != item.text or source['page'] != item.page:
                    raise ValueError('evidence_observation_mismatch')
        return self
