"""Logical documents and sections, independent of physical block boundaries."""
from dataclasses import dataclass
import re
from .canonical import LogicalDocument, stable_id


@dataclass(frozen=True)
class Observation:
    id: str
    page: int
    text: str
    bbox: tuple[float, float, float, float] | None
    reading_method: str
    source_region_id: str = ''
    source_offset: int = 0
    section: str = 'unknown'


HEADINGS = [
    ('lease_contract', r'contrato\s+de\s+arrendamento\b'),
    ('cadastral_record', r'caderneta\s+predial\b'),
    ('privacy_notice', r'(?:pol[ií]tica\s+de\s+privacidade|declara[cç][aã]o\s+de\s+prote[cç][aã]o\s+de\s+dados|regulamento\s+geral\s+de\s+prote[cç][aã]o)'),
    ('identity_document', r'(?:cart[aã]o\s+de\s+cidad[aã]o|bilhete\s+de\s+identidade)\s*$'),
    ('land_registry_record', r'certid[aã]o\s+(?:permanente|predial)\b'),
    ('site_plan', r'(?:anexo\s+[IVX\d]+\s*[-–:]?\s*)?(?:planta\b|levantamento\s+topogr[aá]fico)'),
    ('payment_correspondence', r'(?:assunto\s*:\s*)?(?:envio\s+de\s+recibo|carta\s+de\s+envio)'),
    ('payment_receipt', r'recibo\s+(?:de\s+renda|n[.º°])'),
    ('bank_payment_confirmation', r'comprovativo\s+(?:de\s+)?transfer[eê]ncia\b'),
]


def heading_type(line):
    for kind, pattern in HEADINGS:
        if re.match(pattern, line.strip(), re.I):
            return kind
    return None


def classify(text):
    first = next((line for line in text.splitlines() if line.strip()), '')
    kind = heading_type(first)
    if kind:
        return kind, 'explicit_document_heading', 'processed'
    if len(text.strip()) < 12:
        return 'unknown', 'insufficient_text', 'low_text'
    if re.search(r'envio\s+de\s+recibo|junto\s+enviamos.*recibo', text, re.I):
        return 'payment_correspondence', 'correspondence_language', 'processed'
    if re.search(r'retido\s+na\s+fonte|valor\s+l[ií]quido', text, re.I):
        return 'payment_receipt', 'financial_receipt_language', 'processed'
    return 'unknown', 'no_document_heading', 'continuation_uncertain'


def section_type(text):
    for kind, pattern in [('properties', r'artigo\s+matricial|pr[eé]dio|[áa]rea\s+total'),
                          ('financial', r'renda|reten[cç][aã]o|l[ií]quido|pagamento'),
                          ('temporal', r'dura[cç][aã]o|condi[cç][aã]o|assina|prazo|licen[cç]a'),
                          ('parts', r'outorgante|NIF|NIPC|senhorio|arrendat[aá]ri')]:
        if re.search(pattern, text, re.I):
            return kind
    return 'unknown'


def split_observations(observations):
    output = []
    for obs in observations:
        boundaries = [0] + [m.start() for m in re.finditer(r'^.*$', obs.text, re.M) if m.start() and heading_type(m[0])] + [len(obs.text)]
        for start, end in zip(boundaries, boundaries[1:]):
            text = obs.text[start:end]
            if text.strip():
                output.append(Observation(obs.id if start == 0 and end == len(obs.text) else 'region-' + stable_id(obs.id, start, end),
                                          obs.page, text, obs.bbox, obs.reading_method,
                                          obs.source_region_id or obs.id, obs.source_offset + start, section_type(text)))
    return output


def map_documents(observations):
    documents, current, contract_parent = [], None, None
    page_text={page:'\n'.join(o.text for o in observations if o.page==page) for page in {o.page for o in observations}}
    def cadastral_key(page):
        m=re.search(r'^\s*(\d{6}\s+-[^\n]+)',page_text[page],re.M)
        return m[1].strip() if m else None
    for obs in observations:
        kind, reason, state = classify(obs.text)
        explicit = heading_type(next((x for x in obs.text.splitlines() if x.strip()), ''))
        # Short layout blocks (a name, TITULARES, article number) are not short pages.
        if state=='low_text' and len(page_text[obs.page].strip())>=12:
            state='continuation_uncertain'
        if current and current.source_type=='cadastral_record' and obs.page>max(current.page_numbers):
            if not re.search(r'caderneta\s+predial',page_text[obs.page],re.I) and not cadastral_key(obs.page):
                current=None
        if explicit=='cadastral_record' and current and current.source_type=='cadastral_record':
            previous=cadastral_key(current.page_numbers[0])
            if previous and previous==cadastral_key(obs.page):explicit=None
        if state == 'low_text':
            documents.append(LogicalDocument(id='doc-' + stable_id(obs.id, 'unknown'), source_type='unknown',
                                             region_ids=[obs.id], page_numbers=[obs.page], classification_reason=reason, page_state=state))
            continue
        if current is None or explicit or (kind != 'unknown' and current.source_type == 'unknown'):
            current = LogicalDocument(id='doc-' + stable_id(obs.id, kind), source_type=kind,
                                      region_ids=[obs.id], page_numbers=[obs.page], classification_reason=reason,
                                      page_state='processed' if kind != 'unknown' else 'continuation_uncertain',
                                      parent_id=contract_parent if kind != 'lease_contract' else None)
            documents.append(current)
            if kind == 'lease_contract':
                contract_parent = current.id
        else:
            current.region_ids.append(obs.id)
            current.page_numbers = sorted(set(current.page_numbers + [obs.page]))
    return documents
