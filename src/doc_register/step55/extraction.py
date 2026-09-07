"""Domain rules over logical documents with reversible multi-region evidence."""
from __future__ import annotations
from datetime import date
import re
import unicodedata
from .canonical import Assertion, Entity, EvidenceSpan, Relation, stable_id
from .values import decimal_pt

from .rules import VERSION as RULE_VERSION
MONTHS = {'janeiro': 1, 'fevereiro': 2, 'março': 3, 'abril': 4, 'maio': 5, 'junho': 6,
          'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12}
NUMBER = r'\d[\d. \u00a0]*(?:,\d+)?'
DATE = r'(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+de\s+[A-Za-zçÇ]+\s+de\s+\d{4})'
PERSON = r'[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:d[aeo]s?|e|[A-ZÀ-Ý][a-zà-ÿ]+)){1,10}'
GENERIC = {'parte incumpridora', 'parte cumpridora', 'primeiro outorgante', 'segundo outorgante',
           'senhorio', 'senhoria', 'arrendataria', 'arrendatario', 'prédio', 'predio'}


def key(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if not unicodedata.combining(c))


def parse_date(raw):
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
        parsed = date.fromisoformat(raw)
    elif re.fullmatch(r'\d{1,2}[/-]\d{1,2}[/-]\d{4}', raw):
        day, month, year = map(int, re.split(r'[/-]', raw)); parsed = date(year, month, day)
    else:
        match = re.fullmatch(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', raw, re.I)
        if not match or match[2].casefold() not in MONTHS:
            raise ValueError('invalid_portuguese_date')
        parsed = date(int(match[3]), MONTHS[match[2].casefold()], int(match[1]))
    return {'kind': 'literal_date', 'value': parsed.isoformat(), 'precision': 'day'}


class Context:
    def __init__(self, observations, logical):
        self.logical = logical
        self.parts, self.text = [], ''
        self.evidence, self.entities, self.assertions, self.relations, self.issues = {}, {}, {}, {}, []
        for obs in observations:
            start = len(self.text)
            self.text += obs.text + '\n'
            self.parts.append((start, start + len(obs.text), obs))

    def spans(self, start, end):
        ids = []
        for a, b, obs in self.parts:
            lo, hi = max(start, a), min(end, b)
            if lo >= hi or not obs.text[lo-a:hi-a].strip():
                continue
            eid = 'ev-' + stable_id(obs.id, lo-a, hi-a)
            self.evidence[eid] = EvidenceSpan(id=eid, page=obs.page, region_id=obs.id, text=obs.text,
                start=lo-a, end=hi-a, bbox=obs.bbox, reading_method=obs.reading_method,
                dependency_id='dep-' + stable_id(self.logical.id, obs.page))
            ids.append(eid)
        return ids

    def entity(self, kind, label, start, end, identity_key=None):
        ids = self.spans(start, end)
        if not ids:
            raise ValueError('entity_requires_evidence')
        eid = kind + '-' + stable_id(self.logical.id, kind, identity_key or (ids[0], label))
        if eid not in self.entities:
            self.entities[eid] = Entity(id=eid, kind=kind, label=label, logical_document_id=self.logical.id, evidence_ids=ids)
        else:
            self.entities[eid].evidence_ids = list(dict.fromkeys(self.entities[eid].evidence_ids + ids))
        return self.entities[eid]

    def add(self, subject, predicate, value, start, end, *, raw=None, scope=None, issues=(), rule='domain'):
        ids = self.spans(start, end)
        if not ids:
            return None
        authority = {'lease_contract': 'contract', 'cadastral_record': 'cadastral_record',
                     'payment_correspondence': 'correspondence', 'payment_receipt': 'receipt'}.get(self.logical.source_type, 'unknown')
        try:
            all_ids=list(dict.fromkeys(subject.evidence_ids + ids))
            flags=list(issues)
            if any(self.evidence[i].reading_method!='native' for i in all_ids):flags.append('recognition_uncalibrated')
            item = Assertion(id='as-' + stable_id(subject.id, predicate, ids, repr(value)), subject_id=subject.id,
                predicate=predicate, typed_value=value, raw_value=self.text[start:end] if raw is None else raw,
                evidence_ids=all_ids, scope=scope or {}, source_authority=authority,
                extractor_id=RULE_VERSION + ':' + rule, resolution_status='unresolved', validation_issues=list(dict.fromkeys(flags)),
                dependency_ids=sorted({self.evidence[i].dependency_id for i in all_ids}), normalization_steps=['explicit_domain_parser'])
            self.assertions[item.id] = item
            return item
        except ValueError as exc:
            self.issues.append({'predicate': predicate, 'evidence_ids': ids, 'reason': str(exc).splitlines()[0], 'status': 'rejected'})
            return None

    def relate(self, subject, predicate, object_, start, end, role=None):
        ids = list(dict.fromkeys(subject.evidence_ids + object_.evidence_ids + self.spans(start, end)))
        rid = 'rel-' + stable_id(subject.id, predicate, object_.id, role)
        self.relations[rid] = Relation(id=rid, subject_id=subject.id, predicate=predicate, object_id=object_.id,
                                       evidence_ids=ids, status='needs_review', role=role)

    def matches(self, pattern, start=0, end=None):
        return re.finditer(pattern, self.text[start:end], re.I | re.M)


def parties(c, contract):
    text = c.text
    candidates = []
    # Standard AT owner rows place the fiscal identifier before the name.
    for m in re.finditer(r'identifica[cç][aã]o\s+fiscal\s*:\s*(\d{9})\s+Nome\s*:\s*([^\n]+)',text,re.I):
        name=m[2].strip()
        candidates.append((name,m.start(2),m.start(2)+len(name),'',m.start(),m.end()))
    # Labelled individuals/companies, preserving name evidence independently of role.
    for m in re.finditer(r'^\s*(Senhorios?|Senhorias?|Arrendat[aá]ri[oa]s?|Titular|Nome)\s*:\s*([^\n;]+)', text, re.I | re.M):
        name = re.split(r',?\s*(?:NIF|NIPC)\b', m[2], flags=re.I)[0].strip(' ,.'); role = m[1]
        candidates.append((name, m.start(2), m.start(2) + len(name), role, m.start(), m.end()))
    # Narrative identification before NIF/NIPC; avoids collecting domicile prose.
    for m in re.finditer(r'([^\n;]+?)\s*,?\s*(?:NIF|NIPC)\s*[:n.º° ]*([\d ][\d ]{7,14})', text, re.I):
        prefix = m[1]
        org = re.search(r'([A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ &.,-]+?\b(?:S\.?A\.?|Lda\.?))\s*,?\s*$', prefix)
        people = list(re.finditer(PERSON, prefix))
        # Domicile/location names after the party are not its identity.
        people=[p for p in people if key(p[0]) not in GENERIC and not re.search(r'outorgante',p[0],re.I)
                and not re.search(r'morada|residente|domic[ií]lio|freguesia|concelho',prefix[:p.start()],re.I)]
        found = org or (people[0] if len(people)==1 else None)
        if found:
            name = found[1] if org else found[0]
            candidates.append((name.strip(' ,'), m.start(1)+found.start(), m.start(1)+found.end(), '', m.start(), m.end()))
    people = []
    for name, start, end, label, full_start, full_end in candidates:
        if key(name) in GENERIC or len(name.split()) < 2 or re.search(r'\d|incumpridor|cumpridor|outorgante', name, re.I):
            continue
        kind = 'organization' if re.search(r'\b(?:S\.?A\.?|Lda\.?)$', name, re.I) else 'person'
        # Identical overlapping mentions refer to one occurrence. Repeated names elsewhere remain separate.
        overlap = next((p for a,b,p in people if max(a,start) < min(b,end)), None)
        person = overlap or c.entity(kind, name, start, end)
        if not overlap:
            people.append((start, end, person))
            c.add(person, 'name', name, start, end, raw=name, rule='party_name')
        nif = re.search(r'(?:NIF|NIPC)\s*[:n.º° ]*([\d ][\d ]{7,14})', text[end:full_end+30], re.I)
        if nif:
            c.add(person, 'tax_id', nif[1].replace(' ', '').strip(), end+nif.start(), end+nif.end(), rule='tax_id')
        preceding=re.search(r'identifica[cç][aã]o\s+fiscal\s*:\s*(\d{9})',text[full_start:start],re.I)
        if preceding:c.add(person,'tax_id',preceding[1],full_start+preceding.start(),full_start+preceding.end(),rule='tax_id')
        if contract and key(label).startswith(('senhor', 'arrendat')):
            role = 'lessor' if key(label).startswith('senhor') else 'lessee'
            c.add(person, 'contract_role', role, full_start, full_end, scope={'contract_id': contract.id}, rule='labelled_role')
            c.relate(person, 'has_role', contract, full_start, full_end, role)
    if contract:
        # Explicit collective definitions apply to the preceding outorgante block, not the whole page.
        for m in re.finditer(r'doravante\s+designad[oa]s?\s+(?:por\s+)?["“]?(Senhorios?|Arrendat[aá]ri[oa]s?)', text, re.I):
            boundaries = list(re.finditer(r'^\s*(?:\d+[.)]|PRIMEIR[OA]|SEGUND[OA]|TERCEIR[OA])', text[:m.start()], re.I | re.M))
            begin = boundaries[-1].start() if boundaries else 0
            eligible = [p for a,b,p in people if begin <= a < m.start() and not re.search(r'representad[oa]\s+por', text[max(begin,a-50):a], re.I)]
            role = 'lessor' if key(m[1]).startswith('senhor') else 'lessee'
            for p in eligible:
                c.add(p, 'contract_role', role, begin, m.end(), scope={'contract_id': contract.id}, rule='collective_definition')
                c.relate(p, 'has_role', contract, begin, m.end(), role)
        for m in re.finditer(r'(?i:representad[oa]\s+(?:neste\s+ato\s+)?por)\s+(' + PERSON + r')', text):
            organizations = [(a,p) for a,b,p in people if p.kind == 'organization' and a < m.start()]
            if organizations:
                representative = c.entity('person', m[1], m.start(1), m.end(1))
                c.add(representative, 'name', m[1], m.start(1), m.end(1))
                org = max(organizations, key=lambda x:x[0])[1]
                c.relate(representative, 'represents', org, m.start(), m.end())
                cap = re.search(r'qualidade\s+de\s+([^,;.\n]+)', text[m.end():m.end()+100], re.I)
                if cap:
                    c.add(representative, 'representation_capacity', cap[1], m.end()+cap.start(), m.end()+cap.end())
    return people


def properties(c, contract):
    text = c.text
    # Description starts, never overlapping +/- character windows around an article.
    starts = [m.start() for m in re.finditer(r'^\s*(?:[a-z0-9]+[).]\s*)?(?:[Oo]\s+)?pr[eé]dio\s+(?:r[úu]stico|urbano|denominado|designado)', text, re.I | re.M)]
    if c.logical.source_type == 'cadastral_record':
        starts = [0]
    if not starts:
        starts = [m.start() for m in re.finditer(r'^\s*artigo\s+matricial\b', text, re.I | re.M)]
    result = []
    for index, start in enumerate(starts):
        end = starts[index+1] if index+1 < len(starts) else len(text)
        boundary = re.search(r'^\s*(?:cl[áa]usula\b|[áa]rea\s+[úu]til|titulares?\b)', text[start:end], re.I | re.M)
        if boundary and boundary.start() > 0:
            end = start + boundary.start()
        block = text[start:end]
        article = re.search(r'\bartigo\s*(?:matricial\s*)?(?:n[.º°o]+\s*)?[: ]*\s*(\d+[A-Za-z]?)\b', block, re.I)
        if not article:
            continue
        prop = c.entity('property', 'Artigo ' + article[1], start, end)
        result.append((start,end,prop))
        c.add(prop, 'cadastral_article', article[1], start+article.start(), start+article.end(), rule='property_block')
        patterns = {
            'property_name': r'(?:denominad[oa]|designad[oa]\s+por|nome(?:\s+do\s+pr[eé]dio)?\s*:)\s*["“]?([^,;\n”"]+)',
            'registry_description': r'(?:descri[cç][aã]o\s+predial\s*[: ]*|descrit[oa][^\n;]*?sob\s+o\s+n[úu]mero\s+)(\d+)',
            'property_parish': r'\bfreguesia\s*(?:(?:de|do|da)\s+|:\s*)([^,;\n.]+)',
            'property_municipality': r'\bconcelho\s*(?:(?:de|do|da)\s+|:\s*)([^,;\n.]+)',
            'property_district': r'\bdistrito\s*(?:(?:de|do|da)\s+|:\s*)([^,;\n.]+)',
            'cadastral_section': r'\bsec[cç][aã]o\s*:\s*([A-Za-z0-9]{1,5})(?=\s{2,}|\s*$)',
            'matrix_type': r'\b(r[úu]stico|urbano)\b',
        }
        for predicate, pattern in patterns.items():
            if c.logical.source_type=='cadastral_record' and predicate=='property_name':continue
            if c.logical.source_type=='cadastral_record' and predicate in {'property_parish','property_municipality','property_district'}:
                label={'property_parish':'freguesia','property_municipality':'concelho','property_district':'distrito'}[predicate]
                pattern=r'\b'+label+r'\s*:\s*(?:\d+\s*-\s*)?([\s\S]+?)(?=\s+(?:DISTRITO|CONCELHO|FREGUESIA|SEC[CÇ][AÃ]O|ARTIGO|NOME|ELEMENTOS|LOCALIZA[CÇ][AÃ]O|TITULARES|[ÁA]REA)\b|$)'
            for m in re.finditer(pattern, block, re.I):
                if c.logical.source_type=='cadastral_record' and predicate.startswith('property_') and re.search(r'TEVE ORIGEM',block[:m.start()],re.I):continue
                c.add(prop, predicate, re.sub(r'\s+',' ',m[1]).strip(' .'), start+m.start(), start+m.end(), rule='property_block')
        if c.logical.source_type=='cadastral_record':
            name=re.search(r'NOME/LOCALIZA[CÇ][AÃ]O\s+(?:DO\s+)?PR[EÉ]DIO\s*\n([^\n]+)',block,re.I)
            if name:c.add(prop,'property_name',name[1].strip(),start+name.start(),start+name.end(),rule='cadastral_name')
            for m in re.finditer(r'[áa]rea\s+total\s*\((ha|m[²2])\)\s*:\s*('+NUMBER+r')',block,re.I):
                c.add(prop,'area_measurement',{'amount':decimal_pt(m[2]),'unit':m[1].replace('²','2').lower(),
                    'concept':'cadastral_total','approximate':False},start+m.start(),start+m.end(),rule='cadastral_area')
        for m in re.finditer(r'[áa]rea\s+total\s*(?:do\s+terreno\s*)?(?:de|:)?\s*('+NUMBER+r')\s*(m[²2]|ha)(?!\w)', block, re.I):
            try:
                value = {'amount': decimal_pt(m[1]), 'unit': 'm2' if m[2].lower().startswith('m') else 'ha',
                         'concept': 'cadastral_total' if c.logical.source_type == 'cadastral_record' else 'contract_stated_property_total', 'approximate': False}
                c.add(prop, 'area_measurement', value, start+m.start(), start+m.end(), rule='area')
            except ValueError:
                c.issues.append({'reason':'invalid_area', 'evidence_ids': c.spans(start+m.start(),start+m.end())})
        if contract:
            c.relate(contract, 'has_property', prop, start, end)
    if contract:
        for m in re.finditer(r'[áa]rea\s+[úu]til\s*(aproximada)?\s*(?:de|:)?\s*('+NUMBER+r')\s*(ha|hectares?|m[²2])(?!\w)', text, re.I):
            subject = c.entity('lease_object', 'Objeto arrendado', m.start(), m.end(), identity_key='contract-lease-object')
            c.relate(contract, 'has_lease_object', subject, m.start(), m.end())
            c.add(subject, 'area_measurement', {'amount':decimal_pt(m[2]), 'unit':'m2' if m[3].startswith('m') else 'ha',
                  'concept':'leased_usable', 'approximate': bool(m[1])}, m.start(),m.end(), scope={'contract_id':contract.id},rule='leased_area')
    if c.logical.source_type == 'cadastral_record' and len(result) == 1:
        holders = parties(c, None)
        for i,(a,b,person) in enumerate(holders):
            tail = text[b:holders[i+1][0] if i+1<len(holders) else len(text)]
            quota = re.search(r'(?:quota\s*:?\s*)?(\d+)\s*/\s*(\d+)', tail, re.I)
            right = re.search(r'propriedade\s+plena|usufruto|nua\s+propriedade', tail, re.I)
            if quota and right:
                c.add(result[0][2],'ownership',{'right':right[0], 'numerator':int(quota[1]), 'denominator':int(quota[2]), 'holder_id':person.id}, a,b+max(quota.end(),right.end()),rule='cadastral_holder')


def temporal(c, contract):
    text=c.text
    events={}
    for kind,pattern in [('condition_satisfaction',r'verifica[cç][aã]o\s+da\s+condi[cç][aã]o\s+suspensiva|condi[cç][aã]o\s+suspensiva'),
                         ('notification_received',r'rece[cç][aã]o\s+da\s+notifica[cç][aã]o'),
                         ('notification_sent',r'envio\s+da\s+notifica[cç][aã]o'),
                         ('notification',r'notifica[cç][aã]o'),
                         ('production_license',r'licen[cç]a\s+de\s+produ[cç][aã]o'),
                         ('construction_license',r'licen[cç]a\s+de\s+constru[cç][aã]o'),
                         ('signature',r'assinatura')]:
        m=re.search(pattern,text,re.I)
        if m:
            event=c.entity('event_definition',kind,m.start(),m.end(),identity_key=kind)
            c.add(event,'event_kind',kind,m.start(),m.end())
            c.add(event,'event_status','specified',m.start(),m.end())
            c.relate(contract,'defines_event',event,m.start(),m.end())
            events[kind]=event
    duration_pattern=r'(\d+|tr[eê]s|vinte\s+e\s+nove)\s+(anos?|meses?|dias?)(?:\s+e\s+(\d+)\s+meses)?'
    for m in re.finditer(duration_pattern,text,re.I):
        line_start=text.rfind('\n',0,m.start())+1
        line_end=text.find('\n',m.end()); line_end=len(text) if line_end<0 else line_end
        clause=text[line_start:line_end]
        n=int(m[1]) if m[1].isdigit() else 3 if key(m[1])=='tres' else 29
        duration={'kind':'duration','years':n if m[2].lower().startswith('ano') else 0,
                  'months':int(m[3] or 0)+(n if m[2].lower().startswith('mes') else 0),
                  'days':n if m[2].lower().startswith('dia') else 0}
        if re.search(r'dura[cç][aã]o|vigorar[aá]|prazo\s+de\s+arrendamento',clause,re.I):
            c.add(contract,'term_duration',duration,m.start(),m.end(),rule='duration')
        elif re.search(r'prazo|at[eé]|dentro|disponibiliz',clause,re.I):
            anchor = 'notification_received' if re.search(r'rece[cç][aã]o',clause,re.I) else 'notification' if re.search(r'notifica',clause,re.I) else 'signature' if re.search(r'assinatura',clause,re.I) else None
            c.add(contract,'temporal_rule',{'kind':'deadline','duration':duration,
                  'anchor_event_id': events[anchor].id if anchor in events else None,
                  'anchor_text':clause, 'modality':'optional' if re.search(r'poder[aá]|pode',clause,re.I) else 'unspecified'},line_start,line_end,rule='deadline')
    for m in re.finditer(r'(?:assinado\s+em|celebrado\s+em|data\s+de\s+assinatura\s*:)\s*('+DATE+r')',text,re.I):
        surrounding=text[max(0,m.start()-100):m.end()]
        if re.search(r'notarial|reconhecimento|certifico',surrounding,re.I):
            c.issues.append({'reason':'notarial_context','evidence_ids':c.spans(m.start(),m.end())}); continue
        try: c.add(contract,'signed_date',parse_date(m[1]),m.start(),m.end(),rule='signature_date')
        except ValueError: c.issues.append({'reason':'invalid_date','evidence_ids':c.spans(m.start(),m.end())})
    # Closing date needs an adjacent signature label; a bare date is insufficient.
    for m in re.finditer(r'^\s*(?:[A-ZÀ-Ý][^\n,]{1,50},\s*)?('+DATE+r')\s*\n\s*(?:Os\s+)?(?:Outorgantes|Senhorios|Assinaturas)\b',text,re.I|re.M):
        if not re.search(r'notarial|reconhecimento|certifico',text[max(0,m.start()-120):m.end()],re.I):
            try:c.add(contract,'signed_date',parse_date(m[1]),m.start(),m.end(),rule='signature_date')
            except ValueError:c.issues.append({'reason':'invalid_date','evidence_ids':c.spans(m.start(),m.end())})
    # A reported occurrence has its own identity and date; a forecast never creates it.
    for m in re.finditer(r'notifica[cç][aã]o\s+(?:foi\s+)?recebida\s+em\s+('+DATE+r')',text,re.I):
        prefix=text[max(text.rfind('\n',0,m.start())+1,m.start()-70):m.start()]
        if re.search(r'caso|se\b|prevista|exemplo|dever[aá]',prefix,re.I):continue
        definition=events.get('notification_received')
        if definition is None:
            definition=c.entity('event_definition','notification_received',m.start(),m.end(),identity_key='notification_received')
            c.add(definition,'event_kind','notification_received',m.start(),m.end());events['notification_received']=definition
            c.relate(contract,'defines_event',definition,m.start(),m.end())
        occurrence=c.entity('event_occurrence','Receção de notificação reportada',m.start(),m.end())
        c.relate(occurrence,'occurrence_of',definition,m.start(),m.end())
        c.add(occurrence,'event_status','reported',m.start(),m.end())
        try:c.add(occurrence,'event_date',parse_date(m[1]),m.start(),m.end())
        except ValueError:c.issues.append({'reason':'invalid_event_date','evidence_ids':c.spans(m.start(),m.end())})
    for m in re.finditer(r'^.*(?:condi[cç][aã]o\s+suspensiva).*$|^.*in[ií]cio\s+do\s+arrendamento.*$',text,re.I|re.M):
        if 'condition_satisfaction' in events and re.search(r'condi[cç][aã]o',m[0],re.I):
            c.add(contract,'condition',{'clause':m[0],'event_id':events['condition_satisfaction'].id},m.start(),m.end(),rule='condition')
        if re.search(r'in[ií]cio|efeit|efetiv',m[0],re.I):
            event=events.get('notification') if re.search(r'notifica',m[0],re.I) else events.get('condition_satisfaction')
            if event:
                c.add(contract,'term_start_trigger',{'kind':'event_reference','event_id':event.id,'event_name':event.label},m.start(),m.end(),rule='start_trigger')
    return events


def finance(c,contract,events):
    text=c.text
    base=[]
    for m in re.finditer(r'^.*\brenda\b.*$',text,re.I|re.M):
        line=m[0]
        amount=re.search(r'('+NUMBER+r')\s*(?:EUR|euros?|€)',line,re.I)
        if not amount or re.search(r'reserva|indemniza|penaliza|exemplo',line,re.I): continue
        annual=bool(re.search(r'anual|anualmente',line,re.I)); monthly=bool(re.search(r'mensal|mensalmente',line,re.I))
        per_ha=bool(re.search(r'por\s+hectare|/ha',line,re.I))
        obligation=c.entity('financial_obligation','Renda base',m.start(),m.end())
        base.append(obligation)
        c.relate(contract,'has_obligation',obligation,m.start(),m.end())
        c.add(obligation,'rent_term',{'calculation':'unit_rate' if per_ha else 'fixed_amount','amount':decimal_pt(amount[1]),
              'currency':'EUR','per_area_unit':'ha' if per_ha else None, 'billing_frequency':'annual' if annual else 'monthly' if monthly else 'unknown'},m.start(),m.end(),scope={'contract_id':contract.id},rule='rent')
    for m in re.finditer(r'^.*?(\d+(?:,\d+)?)\s*%\s+da\s+renda\s+anual[^\n]*',text,re.I|re.M):
        from decimal import Decimal
        fraction=format(Decimal(m[1].replace(',','.'))/100,'f')
        reserve=bool(re.search(r'reserva',m[0],re.I))
        entity=c.entity('financial_obligation','Reserva' if reserve else 'Referência percentual',m.start(),m.end())
        c.relate(contract,'has_obligation',entity,m.start(),m.end())
        value={'fraction':fraction}
        if reserve:
            value.update(calculation='percentage_of_obligation',base_obligation_id=base[0].id if len(base)==1 else None,
                         start_event_ref=events['production_license'].id if 'production_license' in events and re.search(r'produ[cç][aã]o',m[0],re.I) else None,
                         end_event_ref=events['construction_license'].id if 'construction_license' in events and re.search(r'at[eé].*constru[cç][aã]o',m[0],re.I) else None)
        c.add(entity,'reservation_obligation' if reserve else 'percentage_reference',value,m.start(),m.end(),scope={'contract_id':contract.id},rule='percentage')


def payments(c):
    text=c.text
    payment=c.entity('payment','Pagamento reportado',0,len(text))
    c.add(payment,'payment_status','bank_confirmed' if c.logical.source_type=='bank_payment_confirmation' else 'reported',0,len(text),rule='payment_kind')
    c.add(payment,'payment_evidence_kind',c.logical.source_type,0,len(text))
    patterns={
        'payment_gross_amount':r'(?:valor|montante)\s+(?:bruto|total)\s*(?:de|:)?\s*('+NUMBER+r')\s*(?:EUR|euros?|€)',
        'withholding_tax_amount':r'(?:retid[oa]\s+na\s+fonte|reten[cç][aã]o\s+(?:de\s+)?IRS)\s*(?:no\s+valor\s+)?(?:de|:)?\s*('+NUMBER+r')\s*(?:EUR|euros?|€)',
        'payment_net_amount':r'(?:valor|montante)\s+l[ií]quido\s*(?:de|:)?\s*('+NUMBER+r')\s*(?:EUR|euros?|€)',
    }
    for predicate,pattern in patterns.items():
        for m in re.finditer(pattern,text,re.I):
            try: c.add(payment,predicate,{'amount':decimal_pt(m[1]),'currency':'EUR'},m.start(),m.end(),rule='receipt_money')
            except ValueError: c.issues.append({'reason':'invalid_money','evidence_ids':c.spans(m.start(),m.end())})
    for m in re.finditer(DATE,text,re.I):
        prefix=text[max(text.rfind('\n',0,m.start())+1,m.start()-65):m.start()]
        predicate='bank_value_date' if re.search(r'data[- ]valor',prefix,re.I) else 'bank_execution_date' if re.search(r'transfer[eê]ncia|execu[cç][aã]o',prefix,re.I) else 'receipt_issue_date' if re.search(r'emiss[aã]o',prefix,re.I) else 'document_date'
        if re.search(r'per[ií]odo|renda\s+de',prefix,re.I): continue
        try: c.add(payment,predicate,parse_date(m[0]),m.start(),m.end(),rule='payment_date')
        except ValueError: c.issues.append({'reason':'invalid_date','evidence_ids':c.spans(m.start(),m.end())})
    for m in re.finditer(r'\bartigos?\s+(\d+[A-Za-z]?(?:\s*(?:e|,)\s*\d+[A-Za-z]?)*)(?![.º°])',text,re.I):
        if re.search(r'c[oó]digo|lei\b',text[m.end():m.end()+50],re.I): continue
        for number in re.findall(r'\d+[A-Za-z]?',m[1]):
            c.add(payment,'related_property_article',number,m.start(),m.end(),issues=['cross_document_property_resolution_required'],rule='payment_reference')
    for predicate,pattern in [('related_parish',r'freguesia\s+(?:de|do|da)\s+([^,;.\n]+)'),('related_municipality',r'concelho\s+de\s+([^,;.\n]+)'),
                              ('payment_reference',r'refer[eê]ncia\s*(?:de\s+pagamento)?\s*:\s*([^\n;]+)'),('receipt_reference',r'recibo\s+n[.º° ]*\s*([A-Za-z0-9/-]+)'),
                              ('raw_period',r'per[ií]odo\s*(?:de\s+renda)?\s*:\s*([^\n;]+)')]:
        for m in re.finditer(pattern,text,re.I):c.add(payment,predicate,m[1].strip(),m.start(),m.end())
    for m in re.finditer(r'^\s*(Pagador|Benefici[aá]rio|Remetente|Destinat[aá]rio)\s*:\s*([^\n;]+)',text,re.I|re.M):
        role={'pagador':'payer','beneficiario':'payee','remetente':'sender','destinatario':'recipient'}[key(m[1])]
        name=m[2].strip(' ,')
        if key(name) in GENERIC:continue
        person=c.entity('organization' if re.search(r'S\.?A\.?|Lda',name) else 'person',name,m.start(2),m.end(2))
        c.add(person,'name',name,m.start(2),m.end(2)); c.relate(payment,role,person,m.start(),m.end())


def extract_document(observations, logical):
    c=Context(observations,logical)
    # Persist context even when no rule matches: it remains available to the planner.
    c.spans(0,len(c.text))
    if logical.source_type=='lease_contract':
        contract=c.entity('contract','Contrato de arrendamento',0,min(len(c.text),150),identity_key='contract')
        events={}
        for stage,args in [(parties,(c,contract)),(properties,(c,contract)),(temporal,(c,contract))]:
            try:
                output=stage(*args)
                if stage is temporal:events=output
            except (ValueError,ArithmeticError) as exc:c.issues.append({'reason':stage.__name__+':'+str(exc),'status':'technical_error','document_id':logical.id})
        try:finance(c,contract,events)
        except (ValueError,ArithmeticError) as exc:c.issues.append({'reason':'finance:'+str(exc),'status':'technical_error','document_id':logical.id})
    elif logical.source_type=='cadastral_record':
        try:properties(c,None)
        except (ValueError,ArithmeticError) as exc:c.issues.append({'reason':'properties:'+str(exc),'status':'technical_error','document_id':logical.id})
    elif logical.source_type in {'payment_receipt','payment_correspondence','bank_payment_confirmation'}:
        try:payments(c)
        except (ValueError,ArithmeticError) as exc:c.issues.append({'reason':'payments:'+str(exc),'status':'technical_error','document_id':logical.id})
    return list(c.evidence.values()),list(c.entities.values()),list(c.assertions.values()),list(c.relations.values()),c.issues


def extract(observation,logical_document,entities):
    return extract_document([observation],logical_document)[:4]
