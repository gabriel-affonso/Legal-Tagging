"""Select unanswered questions from persisted observations, not extracted spans."""
import re
from .canonical import EvidenceSpan, ExtractionTask, stable_id

GROUPS={
    'parts': ['name','tax_id','contract_role','representation_capacity'],
    'properties': ['cadastral_article','registry_description','property_name','property_parish','property_municipality','area_measurement','ownership'],
    'temporal': ['signed_date','term_duration','term_start_trigger','temporal_rule','condition'],
    'financial': ['rent_term','reservation_obligation','payment_gross_amount','withholding_tax_amount','payment_net_amount','document_date','payment_reference'],
}
PATTERNS={'parts':r'outorgante|NIF|NIPC|senhor|arrendat|representa',
          'properties':r'pr[eé]dio|artigo|freguesia|concelho|[áa]rea|titular',
          'temporal':r'dura[cç][aã]o|assin|prazo|condi[cç][aã]o|notifica|licen[cç]a',
          'financial':r'renda|reserva|reten[cç][aã]o|pagamento|l[ií]quido|EUR|€'}


def plan_tasks(result):
    output=[]
    evidence={e.id:e for e in result.evidence}
    for doc in result.logical_documents:
        groups=['parts','properties','temporal','financial'] if doc.source_type=='lease_contract' else ['properties'] if doc.source_type=='cadastral_record' else ['financial'] if doc.source_type in {'payment_receipt','payment_correspondence','bank_payment_confirmation'} else []
        entities=[e for e in result.entities if e.logical_document_id==doc.id]
        for group in groups:
            predicates=GROUPS[group]
            facts=[a for a in result.assertions if a.subject_id in {e.id for e in entities} and a.predicate in predicates]
            present={a.predicate for a in facts if a.resolution_status=='resolved'}
            expected={'parts':{'tax_id','contract_role'},'properties':{'cadastral_article','property_name','area_measurement'},
                      'temporal':{'signed_date','term_duration','term_start_trigger'},'financial':{'rent_term'} if doc.source_type=='lease_contract' else {'payment_gross_amount','withholding_tax_amount','payment_net_amount'}}[group]
            missing=expected-present
            if group=='parts':
                roles={a.typed_value for a in facts if a.predicate=='contract_role'}
                if not {'lessor','lessee'}<=roles:missing.add('contract_role')
            if group=='properties':
                for entity in (e for e in entities if e.kind=='property'):
                    fields={a.predicate for a in facts if a.subject_id==entity.id}
                    missing|=expected-fields
            if not missing and not any(a.resolution_status=='conflict' for a in facts):continue
            # Use full-region evidence, which exists even if there are zero rule candidates.
            context=[e for e in evidence.values() if e.region_id in doc.region_ids and e.start==0 and e.end==len(e.text)]
            chunks=[]
            for e in context:
                hits=list(re.finditer(PATTERNS[group],e.text,re.I))
                for hit in hits:
                    start=max(0, e.text.rfind('\n',0,max(0,hit.start()-350))+1)
                    end=min(len(e.text),start+1800)
                    if end<len(e.text):
                        boundary=e.text.rfind('\n',hit.end(),end)
                        if boundary>hit.end():end=boundary
                    if not start<=hit.start()<end: start=max(0,hit.start()-350);end=min(len(e.text),hit.end()+1000)
                    if any(x.region_id==e.region_id and x.start<=hit.start()<x.end for x in chunks):continue
                    chunk=EvidenceSpan(id='ev-'+stable_id(e.region_id,start,end),page=e.page,region_id=e.region_id,
                        text=e.text,start=start,end=end,bbox=e.bbox,reading_method=e.reading_method,dependency_id=e.dependency_id)
                    chunks.append(chunk)
            if not chunks and context:
                e=context[0];end=min(len(e.text),1800)
                chunks=[e.model_copy(update={'id':'ev-'+stable_id(e.region_id,0,end),'end':end})]
            for e in chunks:
                if e.id not in evidence:result.evidence.append(e);evidence[e.id]=e
            selected=[e.id for e in chunks]
            output.append(ExtractionTask(id='task-'+stable_id(doc.id,group,selected,sorted(missing)),task_type=group,
                logical_document_id=doc.id,evidence_ids=selected,entity_ids=[e.id for e in entities],permitted_predicates=predicates,
                reason='missing_or_conflicting:'+','.join(sorted(missing)),priority={'parts':0,'properties':1,'temporal':2,'financial':3}[group],
                status='planned' if selected else 'blocked'))
    return sorted(output,key=lambda t:t.priority)
