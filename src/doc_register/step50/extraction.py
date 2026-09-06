"""Candidate producers and a single conservative, non-voting resolver."""
from __future__ import annotations
from collections import defaultdict
import json
import re

from .schema import Candidate, Entity, FieldResult, Region, Segment, identity, name_key, normalize

ROLE_LABELS = {'senhorio': 'lessor', 'senhoria': 'lessor', 'senhorios': 'lessor',
               'arrendatário': 'lessee', 'arrendatária': 'lessee', 'arrendatario': 'lessee',
               'arrendataria': 'lessee', 'representante': 'representative',
               'titular cadastral': 'cadastral_owner', 'titular registral': 'registered_owner',
               'cedente': 'assignor', 'cessionário': 'assignee'}


def segment_pages(pages, maximum_chars):
    logical = 'document-1'
    source_type = 'unknown'
    segments = []
    for page in pages:
        page_text = '\n'.join(r.text for r in page.regions if r.method == 'native') or '\n'.join(r.text for r in page.regions)
        heading = page_text[:1000].casefold()
        new_type = ('caderneta' if re.search(r'caderneta\s+predial', heading) else
                    'registry' if re.search(r'certid[aã]o\s+(?:permanente|predial)', heading) else
                    'amendment' if re.search(r'\baditamento\b', heading[:300]) else
                    'contract' if re.search(r'contrato\s+de\s+arrendamento', heading[:300]) else None)
        if new_type:
            if new_type != source_type or new_type in {'caderneta', 'registry', 'amendment'}:
                logical = f'{new_type}-p{page.number}'
            source_type = new_type
        for region in page.regions:
            region.logical_document = logical
            region.source_type = source_type
            # Chunk contexts, never silently truncate the underlying observation.
            stride = max(1, maximum_chars - 300)
            for start in range(0, len(region.text), stride):
                chunk = region.text[start:start + maximum_chars]
                segments.append(Segment(identity(region.id, start), logical, source_type, [region.id], chunk))
                if start + maximum_chars >= len(region.text):
                    break
    return segments


def make_candidate(region, field, entity, raw_value, quote, extractor='label_rules', issues=()):
    start = region.text.find(quote)
    problems = list(issues)
    if start < 0 or not quote:
        problems.append('unanchored_evidence')
    try:
        value, history = normalize(field, raw_value)
    except (ValueError, TypeError) as exc:
        value, history = None, []
        problems.append('validation:' + str(exc))
    if extractor != 'label_rules':
        # A matching string alone does not prove the entity/field relation.
        problems.append('semantic_association_requires_review')
    if region.method != 'native':
        problems.append('recognition_uncalibrated')
    if region.source_type == 'unknown':
        problems.append('unknown_document_type')
    problems.extend(region.issues)
    if re.search(r'rasur|riscad|sem efeito|anulad', quote, re.I):
        problems.append('possible_crossed_out_value')
    return Candidate(identity(region.id, field, entity, raw_value, quote, extractor), field, name_key(entity),
                     region.logical_document, raw_value, value, region.id, quote, start if start >= 0 else None,
                     start + len(quote) if start >= 0 else None, region.source_type, region.method, extractor,
                     history, list(dict.fromkeys(problems)), modality=region.image.get('modality', 'unknown'),
                     dependency_id=identity(region.page, region.bbox))


def deterministic_candidates(region):
    candidates = []
    text = region.text
    # Intentionally narrow: a labelled single-person line, with NIF on the SAME line.
    role_pattern = '|'.join(re.escape(label) for label in ROLE_LABELS)
    party = re.compile(rf'(?im)^(?P<label>{role_pattern})\s*:\s*(?P<name>[^\n;:\d]+?)(?:,?\s+NIF\s*[:n.º° ]*\s*(?P<nif>\d{{9}}))?\s*[.;]?\s*$', re.I | re.M)
    for match in party.finditer(text):
        name = match['name'].strip(' ,.')
        # Multi-party strings and descriptive prose need entity resolution, not splitting guesses.
        if re.search(r'\be\b|representad|residente|casad|na qualidade', name, re.I):
            continue
        role = ROLE_LABELS[match['label'].casefold()]
        if (role in {'lessor', 'lessee', 'assignor', 'assignee'} and region.source_type not in {'contract', 'amendment'}) or (role == 'cadastral_owner' and region.source_type != 'caderneta'):
            continue
        candidates += [make_candidate(region, 'name', name, name, match[0]), make_candidate(region, 'role', name, role, match[0])]
        if match['nif']:
            candidates.append(make_candidate(region, 'tax_id', name, match['nif'], match[0]))
    for field, pattern in {
        'signed_date': r'(?:assinado\s+em|data\s+de\s+assinatura\s*:)\s*(\d{2}[/-]\d{2}[/-]\d{4})',
        'contract_start_date': r'(?:in[ií]cio\s+do\s+contrato\s*:|produz\s+efeitos\s+a\s+partir\s+de)\s*(\d{2}[/-]\d{2}[/-]\d{4})',
        'contract_end_date': r'(?:termo\s+do\s+contrato\s*:|termina\s+em)\s*(\d{2}[/-]\d{2}[/-]\d{4})',
    }.items():
        if region.source_type not in {'contract', 'amendment'}:
            continue
        for match in re.finditer(pattern, text, re.I):
            surrounding = text[max(0, match.start()-100):match.end()+100]
            issues = ['notarial_context'] if re.search(r'not[aá]ri|reconhec|certifico', surrounding, re.I) else []
            candidates.append(make_candidate(region, field, 'contract', match[1], match[0], issues=issues))
    # Explicit article + section within one evidence block; no invented cross-page links.
    articles = list(re.finditer(r'\bartigo\s+(?:matricial\s*)?(?:n[.º°o ]*\s*)?[: ]\s*(\d+[A-Za-z]?)\b', text, re.I))
    for match in articles:
        entity = 'artigo ' + match[1]
        candidates.append(make_candidate(region, 'property_article', entity, match[1], match[0], issues=['property_identity_requires_review']))
        if len(articles) == 1:
            for section in re.finditer(r'\bsec[cç][aã]o\s*[: ]\s*([A-Za-z]{1,3})\b', text, re.I):
                quote = text[min(match.start(), section.start()):max(match.end(), section.end())]
                candidates.append(make_candidate(region, 'property_section', entity, section[1], quote, issues=['property_identity_requires_review']))
    if region.source_type in {'contract', 'amendment'}:
        for match in re.finditer(r'\brenda\s+(mensal|anual)\s*(?:de|:)\s*([\d .]+(?:,\d{1,2})?)\s*(EUR|euros?|€)(\s+por\s+hectare)?', text, re.I):
            value = {'amount': match[2].strip(), 'currency': 'EUR', 'frequency': 'monthly' if match[1].casefold() == 'mensal' else 'annual', 'basis': 'per_ha' if match[4] else 'total', 'condition': ''}
            # Resolve the whole financial clause with human evidence until conditions are calibrated.
            candidates.append(make_candidate(region, 'rent', 'contract', value, match[0], issues=['financial_conditions_require_review']))
    return candidates


def model_candidates(region, response):
    if not isinstance(response, dict) or not isinstance(response.get('candidates'), list):
        raise ValueError('invalid_candidate_response')
    output = []
    for item in response['candidates'][:100]:
        if not isinstance(item, dict) or set(item) != {'field', 'entity', 'value', 'region_id', 'quote'}:
            raise ValueError('invalid_candidate_schema')
        if item['region_id'] != region.id or not all(isinstance(item[k], str) for k in ['field', 'entity', 'quote']):
            raise ValueError('invalid_candidate_scope')
        problems = []
        quote = item['quote']
        if item['entity'] != 'contract' and name_key(item['entity']) not in name_key(quote):
            problems.append('entity_not_in_evidence')
        raw = item['value']
        lexical = raw.get('amount', '') if isinstance(raw, dict) else str(raw)
        if item['field'] not in {'role', 'contract_type', 'signature_present'} and name_key(lexical) not in name_key(quote):
            problems.append('value_not_in_evidence')
        output.append(make_candidate(region, item['field'], item['entity'], raw, quote, 'ollama', problems))
    return output


def resolve(candidates):
    grouped = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.logical_document, candidate.entity, candidate.field)].append(candidate)
    results = []
    for (logical, entity, field), group in sorted(grouped.items()):
        valid = [c for c in group if c.value is not None and 'unanchored_evidence' not in c.issues]
        values = {json.dumps(c.value, sort_keys=True, ensure_ascii=False) for c in valid}
        clean = [c for c in valid if not c.issues]
        reasons = list(dict.fromkeys(issue for c in group for issue in c.issues))
        status = 'conflict' if len(values) > 1 else 'accepted' if clean else 'needs_review'
        value = clean[0].value if status == 'accepted' else None
        results.append(FieldResult(field, entity, logical, status, value, [c.id for c in group], reasons,
                                   'automatic' if status == 'accepted' else None))
    # A tax number cannot be automatically attached to multiple distinct identities.
    tax_groups = defaultdict(list)
    for result in results:
        if result.field == 'tax_id' and result.status == 'accepted':
            tax_groups[(result.logical_document, result.value)].append(result)
    for group in tax_groups.values():
        if len({r.entity for r in group}) > 1:
            for result in group:
                result.status, result.value, result.acceptance = 'conflict', None, None
                result.reasons.append('nif_associated_with_multiple_entities')
    return results
