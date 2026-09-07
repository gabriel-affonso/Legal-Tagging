"""Context-bound deterministic extraction for the Step 5.5 pilot."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
import unicodedata

from .canonical import (AreaValue, Assertion, Duration, Entity, EvidenceSpan,
                        EventReference, LiteralDate, Money, Relation, stable_id)
from .document_map import Observation

MONTHS = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
          "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
          "outubro": 10, "novembro": 11, "dezembro": 12}
ROLE_MAP = {"senhorio": "lessor", "senhoria": "lessor", "senhorios": "lessor",
            "arrendatário": "lessee", "arrendataria": "lessee", "arrendatária": "lessee",
            "arrendatarios": "lessee", "arrendatários": "lessee", "arrendatarias": "lessee",
            "arrendatárias": "lessee"}


def decimal_pt(value: str) -> str:
    normalized = value.replace(" ", "").replace(".", "").replace(",", ".")
    return format(Decimal(normalized).normalize(), "f")


def _evidence(observation: Observation, start: int, end: int) -> EvidenceSpan:
    return EvidenceSpan(id="ev-" + stable_id(observation.id, start, end), page=observation.page,
                        region_id=observation.id, text=observation.text, start=start, end=end,
                        bbox=observation.bbox, reading_method=observation.reading_method,
                        dependency_id="dep-" + stable_id(observation.id))


def _authority(source_type: str) -> str:
    return {"lease_contract": "contract", "cadastral_record": "cadastral_record",
            "payment_receipt": "receipt", "payment_correspondence": "correspondence"}.get(source_type, "unknown")


def _assert(subject: Entity, predicate: str, raw: str, value, evidence: EvidenceSpan,
            source_type: str, extractor: str, *, issues=(), scope=None, accepted=False) -> Assertion:
    return Assertion(id="as-" + stable_id(subject.id, predicate, raw, evidence.id), subject_id=subject.id,
                     predicate=predicate, raw_value=raw, typed_value=value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
                     evidence_ids=[evidence.id], scope=scope or {}, source_authority=_authority(source_type),
                     extractor_id=extractor, resolution_status="resolved",
                     acceptance_status="accepted_automatic" if accepted else "needs_review",
                     validation_issues=list(issues), dependency_ids=[evidence.dependency_id])


def _entity(kind: str, label: str, logical_id: str, evidence: EvidenceSpan) -> Entity:
    return Entity(id=f"{kind[:3]}-" + stable_id(logical_id, kind, label.casefold()), kind=kind,
                  label=" ".join(label.split()), logical_document_id=logical_id, evidence_ids=[evidence.id])


def _date_value(raw: str):
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", raw)
    if numeric:
        return LiteralDate(value=date(int(numeric[3]), int(numeric[2]), int(numeric[1])))
    written = re.fullmatch(r"(\d{1,2})\s+de\s+([A-Za-zçÇãÃ]+)\s+de\s+(\d{4})", raw, re.I)
    if written:
        month = MONTHS.get(unicodedata.normalize("NFC", written[2]).casefold())
        if month:
            return LiteralDate(value=date(int(written[3]), month, int(written[1])))
    raise ValueError("invalid_portuguese_date")


def extract(observation: Observation, logical_document, entities: list[Entity]):
    """Return new evidence/entities/assertions/relations from one classified region.

    The caller supplies earlier entities so an extracted field can target a
    typed contract/property instead of textual labels.  No fuzzy merging occurs.
    """
    source_type = logical_document.source_type
    text = observation.text
    evidence, created, assertions, relations = [], [], [], []
    by_kind = {kind: [item for item in entities if item.kind == kind and item.logical_document_id == logical_document.id]
               for kind in ("contract", "person", "organization", "property", "lease_object", "payment", "financial_obligation", "event_definition")}

    def span(match, group=0):
        start, end = match.span(group)
        item = _evidence(observation, start, end)
        evidence.append(item)
        return item

    def single(kind, label, ev):
        found = next((item for item in by_kind[kind] + created if item.kind == kind and item.label.casefold() == label.casefold()), None)
        if found:
            return found
        item = _entity(kind, label, logical_document.id, ev)
        created.append(item)
        return item

    contract = None
    if source_type == "lease_contract":
        heading = re.search(r"contrato\s+de\s+arrendamento", text, re.I)
        if heading:
            contract = single("contract", "Contrato de arrendamento", span(heading))

    # Explicit line labels only: roles cannot become an entity because the value
    # must be a name-like string with at least two alphabetic tokens.
    if source_type == "lease_contract":
        labelled = re.compile(r"(?im)^\s*(senhorio|senhoria|arrendat[aá]ri[oa])s?\s*:\s*([^\n;:]+?)(?:\s*,?\s*(?:NIF|NIPC)\s*[:º°. ]*([0-9 ]{9,15}))?\s*$")
        for match in labelled.finditer(text):
            name = " ".join(match[2].strip(" ,.").split())
            if len(re.findall(r"[A-Za-zÀ-ÿ]+", name)) < 2 or name.casefold() in ROLE_MAP:
                continue
            ev = span(match)
            kind = "organization" if re.search(r"\bS\.?A\.?\b|\bLDA\.?\b", name, re.I) else "person"
            party = single(kind, name, ev)
            role = ROLE_MAP[match[1].casefold().rstrip("s")]
            assertions.append(_assert(party, "tax_id", match[3].replace(" ", ""), match[3].replace(" ", ""), ev, source_type, "party_label_rule", issues=["tax_id_checksum_not_verified"] if match[3] else [])) if match[3] else None
            if contract:
                relations.append(Relation(id="rel-" + stable_id(contract.id, party.id, role, ev.id), subject_id=party.id,
                                          predicate="has_role", object_id=contract.id, evidence_ids=[ev.id], status="needs_review"))
                assertions.append(_assert(party, "contract_role", role, role, ev, source_type, "party_label_rule", scope={"contract_id": contract.id}))

    # Cadastral/contract property blocks. Each identifier starts an isolated
    # object; area/name are associated only inside that same local window.
    if source_type in {"lease_contract", "cadastral_record"}:
        article_pattern = re.compile(r"\bartigo\s+(?:matricial\s*)?(?:n[.º°o ]*\s*)?[: ]\s*(\d+[A-Za-z]?)\b", re.I)
        matches = list(article_pattern.finditer(text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), match.end() + 850)
            start = max(0, text.rfind("\n", 0, match.start() - 150))
            window = text[start:end]
            ev = span(match)
            property_ = single("property", "Artigo " + match[1], ev)
            assertions.append(_assert(property_, "cadastral_article", match[1], match[1], ev, source_type, "article_rule"))
            name = re.search(r"(?:pr[eé]dio|denominad[oa])\s*(?:r[úu]stico\s*)?(?:designado\s+por\s*)?[\"“]?([A-ZÀ-Ý][^\n,;]{2,70})", window, re.I)
            if name:
                name_ev = _evidence(observation, start + name.start(1), start + name.end(1)); evidence.append(name_ev)
                assertions.append(_assert(property_, "property_name", name[1].strip(" .\"“”"), name[1].strip(" .\"“”"), name_ev, source_type, "property_block_rule"))
            registry = re.search(r"(?:descri[cç][aã]o\s+predial|descrit[oa])\s*(?:n[.º°o ]*)?[: ]\s*(\d+)", window, re.I)
            if registry:
                registry_ev = _evidence(observation, start + registry.start(1), start + registry.end(1)); evidence.append(registry_ev)
                assertions.append(_assert(property_, "registry_description", registry[1], registry[1], registry_ev, source_type, "property_block_rule"))
            for predicate, pattern in (("property_parish", r"\bfreguesia\s+(?:de|do|da)\s+([^,;\n]+)"),
                                       ("property_municipality", r"\bconcelho\s+(?:de|do|da)\s+([^,;\n]+)")):
                location = re.search(pattern, window, re.I)
                if location:
                    loc_ev = _evidence(observation, start + location.start(1), start + location.end(1)); evidence.append(loc_ev)
                    assertions.append(_assert(property_, predicate, location[1].strip(), location[1].strip(), loc_ev, source_type, "property_block_rule"))
            for area in re.finditer(r"[áa]rea\s+(total|[úu]til(?:\s+aproximada)?)\s*(?:de|:)\s*([\d .]+(?:,\d+)?)\s*(m[²2]|ha)\b", window, re.I):
                concept = "cadastral_total" if area[1].casefold() == "total" else "leased_usable"
                area_ev = _evidence(observation, start + area.start(), start + area.end()); evidence.append(area_ev)
                value = AreaValue(amount=decimal_pt(area[2]), unit="m2" if area[3].casefold().startswith("m") else "ha")
                subject = property_
                if concept == "leased_usable" and contract:
                    subject = single("lease_object", "Objeto arrendado", area_ev)
                    relations.append(Relation(id="rel-" + stable_id(contract.id, subject.id, area_ev.id), subject_id=contract.id,
                                              predicate="has_property", object_id=subject.id, evidence_ids=[area_ev.id], status="needs_review"))
                assertions.append(_assert(subject, "area_measurement", area[0], {"concept": concept, **value.model_dump()}, area_ev, source_type, "area_rule", scope={"property_id": property_.id} if subject != property_ else {}))

    if source_type == "lease_contract" and contract:
        for match in re.finditer(r"\b(\d+)\s+anos?(?:\s+e\s+(\d+)\s+mes(?:es)?)?", text, re.I):
            ev = span(match)
            duration = Duration(years=int(match[1]), months=int(match[2] or 0))
            assertions.append(_assert(contract, "term_duration", match[0], duration, ev, source_type, "duration_rule"))
        for match in re.finditer(r"(?:assinado\s+em|celebrado\s+em|data\s+de\s+assinatura\s*[:])\s*((?:\d{1,2}[/-]\d{1,2}[/-]\d{4})|(?:\d{1,2}\s+de\s+[A-Za-zçÇãÃ]+\s+de\s+\d{4}))", text, re.I):
            ev = span(match)
            try:
                assertions.append(_assert(contract, "signed_date", match[1], _date_value(match[1]), ev, source_type, "date_rule"))
            except ValueError:
                pass
        for match in re.finditer(r"(?:in[ií]cio\s+do\s+arrendamento|produz\s+efeitos)[^\.\n]{0,100}(condi[cç][aã]o\s+suspensiva|notifica[cç][aã]o[^\.\n]*)", text, re.I):
            ev = span(match)
            assertions.append(_assert(contract, "term_start_trigger", match[1], EventReference(event_name=match[1]), ev, source_type, "event_rule"))
        for match in re.finditer(r"renda\s+(?:anual\s*)?(?:de|:)\s*([\d .]+(?:,\d{1,2})?)\s*(?:EUR|euros?|€)\s+por\s+hectare[^\n]*(?:anual|anualmente)", text, re.I):
            ev = span(match)
            obligation = single("financial_obligation", "Renda base", ev)
            relations.append(Relation(id="rel-" + stable_id(contract.id, obligation.id, ev.id), subject_id=contract.id,
                                      predicate="has_obligation", object_id=obligation.id, evidence_ids=[ev.id], status="needs_review"))
            assertions.append(_assert(obligation, "rent_term", match[0], {"calculation": "unit_rate", "amount": decimal_pt(match[1]), "currency": "EUR", "per_area_unit": "ha", "billing_frequency": "annual"}, ev, source_type, "rent_rule", scope={"contract_id": contract.id}))
        for match in re.finditer(r"\b(\d+(?:,\d+)?)\s*%\s+da\s+renda\s+anual", text, re.I):
            ev = span(match)
            obligation = single("financial_obligation", "Reserva", ev)
            relations.append(Relation(id="rel-" + stable_id(contract.id, obligation.id, ev.id), subject_id=contract.id,
                                      predicate="has_obligation", object_id=obligation.id, evidence_ids=[ev.id], status="needs_review"))
            assertions.append(_assert(obligation, "reservation_obligation", match[0], {"calculation": "percentage_of_obligation", "fraction": format(Decimal(match[1].replace(",", ".")) / 100, "f"), "base_predicate": "rent_term"}, ev, source_type, "percentage_rule", scope={"contract_id": contract.id}))

    if source_type in {"payment_receipt", "payment_correspondence"}:
        receipt_ev = _evidence(observation, 0, min(len(text), 1))
        evidence.append(receipt_ev)
        receipt = single("payment", "Pagamento reportado", receipt_ev)
        money_patterns = (("payment_gross_amount", r"(?:valor|montante)\s+(?:total|bruto)\s+(?:de|:)\s*([\d .]+(?:,\d{1,2})?)\s*(?:EUR|euros?|€)"),
                          ("withholding_tax_amount", r"(?:retid[oa]\s+na\s+fonte|reten[cç][aã]o\s+(?:de\s+)?IRS)\s*(?:de|:)\s*([\d .]+(?:,\d{1,2})?)\s*(?:EUR|euros?|€)"),
                          ("payment_net_amount", r"valor\s+l[ií]quido\s+(?:de|:)\s*([\d .]+(?:,\d{1,2})?)\s*(?:EUR|euros?|€)"))
        for predicate, pattern in money_patterns:
            for match in re.finditer(pattern, text, re.I):
                ev = span(match)
                assertions.append(_assert(receipt, predicate, match[1], Money(amount=decimal_pt(match[1])), ev, source_type, "receipt_money_rule"))
        for match in re.finditer(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+de\s+[A-Za-zçÇãÃ]+\s+de\s+\d{4})\b", text, re.I):
            ev = span(match)
            try:
                assertions.append(_assert(receipt, "document_date", match[1], _date_value(match[1]), ev, source_type, "date_rule"))
            except ValueError:
                pass
        for match in re.finditer(r"\bartigos?\s+(\d+[A-Za-z]?)(?:\s*(?:e|,)\s*(\d+[A-Za-z]?))?", text, re.I):
            ev = span(match)
            for value in filter(None, match.groups()):
                assertions.append(_assert(receipt, "related_property_article", value, value, ev, source_type, "receipt_article_rule", issues=["cross_document_property_resolution_required"]))
    return evidence, created, assertions, relations
