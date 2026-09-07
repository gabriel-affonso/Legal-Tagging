"""Closed value registry shared by rules, model responses, review and export."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Value(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def decimal_string(raw: str) -> str:
    if not isinstance(raw, str) or not re.fullmatch(r'\d+(?:\.\d+)?', raw):
        raise ValueError('canonical_decimal_required')
    return format(Decimal(raw).normalize(), 'f')


def decimal_pt(raw: str) -> str:
    raw = re.sub(r'[ \u00a0]', '', raw.strip())
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?', raw):
        raise ValueError('invalid_portuguese_decimal')
    return decimal_string(raw.replace('.', '').replace(',', '.'))


class Money(Value):
    amount: str
    currency: Literal['EUR'] = 'EUR'
    _amount = field_validator('amount')(decimal_string)


class AreaValue(Value):
    amount: str
    unit: Literal['m2', 'ha']
    _amount = field_validator('amount')(decimal_string)


class AreaMeasurement(AreaValue):
    concept: Literal['cadastral_total', 'registry_total', 'contract_stated_property_total',
                     'leased_usable', 'measured', 'operational_contracted', 'considered']
    approximate: bool = False


class Duration(Value):
    kind: Literal['duration'] = 'duration'
    years: int = Field(default=0, ge=0)
    months: int = Field(default=0, ge=0)
    days: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def positive(self):
        if not self.years + self.months + self.days:
            raise ValueError('empty_duration')
        return self


class LiteralDate(Value):
    kind: Literal['literal_date'] = 'literal_date'
    value: str
    precision: Literal['day', 'month', 'year'] = 'day'

    @model_validator(mode='after')
    def valid_date(self):
        patterns = {'day': r'\d{4}-\d{2}-\d{2}', 'month': r'\d{4}-\d{2}', 'year': r'\d{4}'}
        if not re.fullmatch(patterns[self.precision], self.value):
            raise ValueError('invalid_date_precision')
        date.fromisoformat(self.value + {'day': '', 'month': '-01', 'year': '-01-01'}[self.precision])
        return self


class EventReference(Value):
    kind: Literal['event_reference'] = 'event_reference'
    event_name: str = Field(min_length=1)
    event_id: str | None = None


class TemporalRule(Value):
    kind: Literal['deadline', 'relative_date']
    duration: Duration
    anchor_event_id: str | None = None
    anchor_text: str | None = None
    direction: Literal['after', 'before'] = 'after'
    consequence: str | None = None
    modality: Literal['automatic', 'optional', 'unspecified'] = 'unspecified'
    calendar_convention: Literal['clamp_month_end'] | None = None


class Condition(Value):
    clause: str = Field(min_length=1)
    event_id: str
    effect: str | None = None
    status: Literal['specified', 'satisfied', 'failed', 'unknown'] = 'specified'


class Rate(Money):
    calculation: Literal['unit_rate', 'fixed_amount']
    per_area_unit: Literal['ha', 'm2'] | None = None
    billing_frequency: Literal['annual', 'monthly', 'one_off', 'unknown']
    area_basis_ref: str | None = None
    start_event_ref: str | None = None
    end_event_ref: str | None = None

    @model_validator(mode='after')
    def dimensions(self):
        if (self.calculation == 'unit_rate') != (self.per_area_unit is not None):
            raise ValueError('rate_area_dimension_mismatch')
        return self


class Percentage(Value):
    fraction: str
    _fraction = field_validator('fraction')(decimal_string)

    @model_validator(mode='after')
    def range(self):
        if Decimal(self.fraction) > 1:
            raise ValueError('percentage_out_of_range')
        return self


class PercentageObligation(Percentage):
    calculation: Literal['percentage_of_obligation'] = 'percentage_of_obligation'
    base_obligation_id: str | None = None
    payment_frequency: Literal['annual', 'monthly', 'one_off'] | None = None
    start_event_ref: str | None = None
    end_event_ref: str | None = None
    offset_rule: str | None = None


class Ownership(Value):
    right: str = Field(min_length=1)
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    holder_id: str

    @model_validator(mode='after')
    def quota(self):
        if self.numerator > self.denominator:
            raise ValueError('invalid_quota')
        return self


class PaymentAllocation(Value):
    obligation_id: str
    amount: Money | None = None
    period: str | None = None


DATE_FIELDS = {'signed_date', 'document_date', 'receipt_issue_date', 'bank_execution_date',
               'bank_value_date', 'rent_period_start', 'rent_period_end', 'event_date', 'contract_end_date'}
MONEY_FIELDS = {'payment_gross_amount', 'withholding_tax_amount', 'payment_net_amount'}
VALUE_MODELS = {**{key: LiteralDate for key in DATE_FIELDS}, **{key: Money for key in MONEY_FIELDS},
                'area_measurement': AreaMeasurement, 'term_duration': Duration, 'temporal_rule': TemporalRule,
                'term_start_trigger': EventReference, 'condition': Condition, 'rent_term': Rate,
                'reservation_obligation': PercentageObligation, 'percentage_reference': Percentage,
                'explicit_tax_rate': Percentage, 'ownership': Ownership, 'payment_allocation': PaymentAllocation}
TEXT_FIELDS = {'name', 'tax_id', 'contract_role', 'cadastral_article', 'cadastral_section', 'matrix_type',
               'registry_description', 'property_name', 'property_parish', 'property_municipality', 'property_district',
               'related_property_article', 'related_parish', 'related_municipality', 'payment_reference',
               'receipt_reference', 'payment_status', 'payment_evidence_kind', 'tax_type', 'raw_period',
               'iban', 'event_kind', 'event_status', 'representation_capacity', 'controller_contact'}
PREDICATES = set(VALUE_MODELS) | TEXT_FIELDS


def validate_value(predicate, value):
    if predicate in VALUE_MODELS:
        model = VALUE_MODELS[predicate]
        return model.model_validate(value).model_dump(mode='json')
    if predicate not in TEXT_FIELDS or not isinstance(value, str) or not value.strip():
        raise ValueError('unknown_field_or_invalid_text_value')
    if predicate == 'tax_id':
        from ..step40 import valid_nif
        if not re.fullmatch(r'\d{9}', value) or not valid_nif(value):
            raise ValueError('invalid_nif_checksum')
    if predicate == 'iban':
        from ..step40 import valid_iban
        if not re.fullmatch(r'PT50\d{21}', value) or not valid_iban(value):
            raise ValueError('invalid_portuguese_iban')
    if predicate in {'cadastral_article', 'related_property_article', 'registry_description'} and not re.fullmatch(r'\d+[A-Za-z]?', value):
        raise ValueError('invalid_property_identifier')
    if predicate == 'contract_role' and value not in {'lessor', 'lessee', 'representative', 'cadastral_owner', 'registered_owner'}:
        raise ValueError('invalid_role')
    if predicate == 'payment_status' and value not in {'reported', 'bank_confirmed'}:
        raise ValueError('invalid_payment_status')
    if predicate == 'event_status' and value not in {'specified','reported','occurred','unknown'}:
        raise ValueError('invalid_event_status')
    return value


def allowed_subjects(predicate):
    if predicate.startswith('property_') or predicate in {'cadastral_article', 'cadastral_section', 'matrix_type', 'registry_description', 'ownership'}:
        return {'property'}
    if predicate in {'name', 'tax_id', 'contract_role', 'representation_capacity', 'iban', 'controller_contact'}:
        return {'person', 'organization'}
    if predicate == 'area_measurement':
        return {'property', 'lease_object', 'contract'}
    if predicate in {'rent_term', 'reservation_obligation', 'percentage_reference'}:
        return {'financial_obligation'}
    if predicate in {'term_duration', 'term_start_trigger', 'temporal_rule', 'condition', 'signed_date', 'contract_end_date'}:
        return {'contract'}
    if predicate=='event_date':return {'event_occurrence'}
    if predicate.startswith('event_'):
        return {'event_definition', 'event_occurrence'}
    return {'payment'}


def document_predicates(source_type):
    people={'name','tax_id','contract_role','representation_capacity','iban'}
    properties={p for p in PREDICATES if allowed_subjects(p)=={'property'}}|{'area_measurement'}
    if source_type=='lease_contract':
        return people|properties|{p for p in PREDICATES if allowed_subjects(p)&{'contract','financial_obligation','event_definition','event_occurrence'}}
    if source_type=='cadastral_record':return properties|{'name','tax_id'}
    if source_type in {'payment_receipt','payment_correspondence','bank_payment_confirmation'}:
        return people|{p for p in PREDICATES if allowed_subjects(p)=={'payment'}}
    if source_type=='privacy_notice':return {'name','controller_contact'}
    if source_type=='identity_document':return {'name','tax_id'}
    if source_type=='site_plan':return {'area_measurement'}
    if source_type=='land_registry_record':return properties|{'name','tax_id'}
    return set()


def calendar_offset(anchor: str, duration: dict, direction='after', convention=None):
    """Explicit calendar arithmetic. Unknown anchors/conventions stay unresolved."""
    if not anchor or convention != 'clamp_month_end':
        return None
    from datetime import timedelta
    d = date.fromisoformat(anchor)
    sign = 1 if direction == 'after' else -1
    offset = sign * (12 * duration.get('years', 0) + duration.get('months', 0))
    year, month = divmod(d.year * 12 + d.month - 1 + offset, 12)
    shifted = date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1]))
    return (shifted + timedelta(days=sign * duration.get('days', 0))).isoformat()


def evaluate_formula(expression, inputs, visiting=None):
    """Small dimensional AST; no evaluation of executable strings."""
    visiting = set() if visiting is None else visiting
    if set(expression) == {'ref'}:
        ref = expression['ref']
        if ref in visiting:
            raise ValueError('formula_cycle')
        value = inputs[ref]
        if 'expression' in value:
            return evaluate_formula(value['expression'], inputs, visiting | {ref})
        return Decimal(decimal_string(value['amount'])), value['unit']
    if set(expression) != {'op', 'left', 'right'}:
        raise ValueError('invalid_expression')
    left, lu = evaluate_formula(expression['left'], inputs, visiting)
    right, ru = evaluate_formula(expression['right'], inputs, visiting)
    if expression['op'] == 'subtract' and lu == ru:
        return left - right, lu
    if expression['op'] == 'percentage_of' and ru == 'fraction':
        return left * right, lu
    if expression['op'] == 'multiply' and (lu, ru) == ('EUR/ha/year', 'ha'):
        return left * right, 'EUR/year'
    raise ValueError('incompatible_formula_dimensions')
