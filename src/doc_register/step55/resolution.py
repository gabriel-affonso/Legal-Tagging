"""Non-voting resolution, dimensional checks, proposed links and derivations."""
from collections import defaultdict
from decimal import Decimal
import json
from .canonical import Derivation, Relation, stable_id
from .values import calendar_offset

MULTI={'related_property_article','condition','temporal_rule','percentage_reference'}


def reconcile(result):
    """Rebuild dependent decisions without deleting evidence or human history."""
    facts=[a for a in result.assertions if a.acceptance_status != 'rejected']
    entities={e.id:e for e in result.entities}
    grouped=defaultdict(list)
    result.derivations=[]
    for a in facts:
        # Recompute transient checks so rejecting an alternative clears its conflict.
        a.validation_issues=[i for i in a.validation_issues if i not in {
            'conflicting_values_same_scope','gross_minus_withholding_ne_net',
            'nif_associated_with_multiple_names','reference_namespace_or_value_mismatch'}]
        # Local association is distinct from global identity resolution.
        association_issues=[i for i in a.validation_issues if i!='recognition_uncalibrated']
        a.resolution_status='resolved' if a.acceptance_status=='accepted_human' else ('unresolved' if association_issues or a.predicate.startswith('related_') else 'resolved')
        if a.extractor_id.startswith('ollama') and a.acceptance_status != 'accepted_human':
            a.resolution_status='unresolved'
        dimensions={**a.scope}
        if a.predicate=='area_measurement':dimensions['concept']=a.typed_value['concept']
        if a.predicate=='ownership':dimensions['holder_id']=a.typed_value['holder_id']
        grouped[(a.subject_id,a.predicate,json.dumps(dimensions,sort_keys=True))].append(a)
    for (_,predicate,_),group in grouped.items():
        if predicate in MULTI:continue
        def comparison(a):
            value=a.typed_value
            if a.predicate=='area_measurement':
                value={**value,'amount':format(Decimal(value['amount'])*(10000 if value['unit']=='ha' else 1),'f'),'unit':'m2'}
                value['amount']=format(Decimal(value['amount']).normalize(),'f')
            return json.dumps(value,sort_keys=True)
        if len({comparison(a) for a in group})>1:
            for a in group:
                a.resolution_status='conflict'
                if a.acceptance_status.startswith('accepted_'):a.acceptance_status='needs_review'; a.acceptance_basis=None
                a.validation_issues=list(dict.fromkeys(a.validation_issues+['conflicting_values_same_scope']))
    by_subject=defaultdict(lambda:defaultdict(list))
    for a in facts:by_subject[a.subject_id][a.predicate].append(a)
    tax=defaultdict(list)
    for subject,fields in by_subject.items():
        for a in fields.get('tax_id',[]):tax[a.typed_value].append(subject)
    for number,subjects in tax.items():
        if len({entities[s].label.casefold() for s in subjects})>1:
            for subject in subjects:
                entities[subject].identity_status='conflict'
                for a in by_subject[subject]['tax_id']:
                    a.resolution_status='conflict'; a.validation_issues=list(dict.fromkeys(a.validation_issues+['nif_associated_with_multiple_names']))
    for subject,fields in by_subject.items():
        amounts=[fields.get(k,[]) for k in ('payment_gross_amount','withholding_tax_amount','payment_net_amount')]
        if all(len(x)==1 for x in amounts):
            gross,tax_,net=[x[0] for x in amounts]
            g,w,n=[Decimal(x.typed_value['amount']) for x in (gross,tax_,net)]
            equation=g-w==n
            if not equation:
                for a in (gross,tax_,net):
                    a.validation_issues=list(dict.fromkeys(a.validation_issues+['gross_minus_withholding_ne_net']))
                    a.resolution_status='conflict'
            result.derivations.append(Derivation(id='derive-'+stable_id(subject,'payment_equation'),subject_id=subject,
                operation='gross_minus_withholding_equals_net',input_ids=[gross.id,tax_.id,net.id],value=equation,
                status='calculated' if equation else 'conflict',reason=None if equation else 'check_other_deductions_or_reading'))
            if g and equation:
                result.derivations.append(Derivation(id='derive-'+stable_id(subject,'withholding_fraction'),subject_id=subject,
                    operation='withholding_divided_by_gross',input_ids=[gross.id,tax_.id],value=str(w/g),status='calculated'))
        for area in fields.get('area_measurement',[]):
            value=Decimal(area.typed_value['amount']); unit=area.typed_value['unit']
            result.derivations.append(Derivation(id='derive-'+stable_id(area.id,'ha'),subject_id=subject,
                operation='convert_area_to_ha',input_ids=[area.id],value={'amount':format(value/10000 if unit=='m2' else value,'f'),'unit':'ha'},
                status='blocked' if area.resolution_status=='conflict' else 'calculated',reason='conflicting_input' if area.resolution_status=='conflict' else None))
        for temporal in fields.get('temporal_rule',[]):
            anchor=temporal.typed_value.get('anchor_event_id')
            occurrences=[r.subject_id for r in result.relations if r.predicate=='occurrence_of' and r.object_id==anchor and r.status=='resolved']
            dates=[a for s in occurrences for a in by_subject[s].get('event_date',[]) if a.acceptance_status.startswith('accepted_')]
            calculated=calendar_offset(dates[0].typed_value['value'],temporal.typed_value['duration'],temporal.typed_value['direction'],temporal.typed_value['calendar_convention']) if len(dates)==1 else None
            result.derivations.append(Derivation(id='derive-'+stable_id(temporal.id,'date'),subject_id=subject,operation='calendar_deadline',
                input_ids=[temporal.id]+[a.id for a in dates],value=calculated,status='calculated' if calculated else 'blocked',reason=None if calculated else 'trigger_or_calendar_convention_not_documented'))
        if fields.get('term_start_trigger') and not fields.get('contract_end_date'):
            result.derivations.append(Derivation(id='derive-'+stable_id(subject,'end'),subject_id=subject,operation='contract_end_date',
                input_ids=[a.id for k in ('term_start_trigger','term_duration') for a in fields.get(k,[])],status='blocked',reason='trigger_not_documented'))
    propose_links(result,by_subject)
    derive_areas_and_rates(result,by_subject)
    for a in facts:
        if a.resolution_status=='conflict' and a.acceptance_status.startswith('accepted_'):
            a.acceptance_status='needs_review';a.acceptance_basis=None
    return result


def derive_areas_and_rates(result,fields):
    """Diagnostics retain their inputs; an area is never selected by proximity."""
    def hectares(a):
        return Decimal(a.typed_value['amount'])/(10000 if a.typed_value['unit']=='m2' else 1)
    assertions={a.id:a for a in result.assertions if a.acceptance_status!='rejected'}
    for contract in (e for e in result.entities if e.kind=='contract'):
        parcels={r.object_id for r in result.relations if r.subject_id==contract.id and r.predicate=='has_property' and r.status!='rejected'}
        areas=[a for p in parcels for a in fields[p].get('area_measurement',[]) if a.typed_value['concept']=='contract_stated_property_total']
        if parcels and len(areas)==len(parcels) and len({a.subject_id for a in areas})==len(parcels):
            valid=all(a.resolution_status!='conflict' for a in areas)
            result.derivations.append(Derivation(id='derive-'+stable_id(contract.id,'property_area_sum'),subject_id=contract.id,
                operation='sum_contract_stated_property_areas',input_ids=[a.id for a in areas],
                value={'amount':format(sum((hectares(a) for a in areas),Decimal(0)),'f'),'unit':'ha'},
                status='calculated' if valid else 'blocked',reason='diagnostic_not_leased_area'))
    for obligation in (e for e in result.entities if e.kind=='financial_obligation'):
        for a in fields[obligation.id].get('rent_term',[]):
            rate=a.typed_value;area=assertions.get(rate.get('area_basis_ref')); reason=None
            if a.resolution_status=='conflict':reason='conflicting_rate'
            elif rate['calculation']=='unit_rate' and (not area or area.predicate!='area_measurement'):reason='area_basis_not_documented'
            elif area and area.resolution_status=='conflict':reason='conflicting_area_basis'
            elif area and a.scope.get('contract_id')!=area.scope.get('contract_id'):reason='area_basis_scope_mismatch'
            elif area and area.typed_value.get('approximate'):reason='approximate_area_requires_explicit_financial_policy'
            amount=Decimal(rate['amount'])
            if not reason and area:
                quantity=hectares(area)*(10000 if rate['per_area_unit']=='m2' else 1)
                amount*=quantity
            result.derivations.append(Derivation(id='derive-'+stable_id(a.id,'rent_amount'),subject_id=obligation.id,
                operation='rent_amount_for_explicit_basis',input_ids=[a.id]+([area.id] if area else []),
                value=None if reason else {'amount':format(amount,'f'),'currency':'EUR','frequency':rate['billing_frequency']},
                status='blocked' if reason else 'calculated',reason=reason))


def propose_links(result,by_subject=None):
    if by_subject is None:
        by_subject=defaultdict(lambda:defaultdict(list))
        for a in result.assertions:
            if a.acceptance_status!='rejected':by_subject[a.subject_id][a.predicate].append(a)
    def unique(subject,predicate):
        values={json.dumps(a.typed_value,sort_keys=True) for a in by_subject[subject].get(predicate,[]) if a.resolution_status!='conflict'}
        return json.loads(next(iter(values))) if len(values)==1 else None
    properties=[e for e in result.entities if e.kind=='property']
    existing={r.id for r in result.relations}
    parties=[e for e in result.entities if e.kind in {'person','organization'}]
    for i,left in enumerate(parties):
        for right in parties[i+1:]:
            from .extraction import key
            if left.kind!=right.kind or left.logical_document_id==right.logical_document_id:continue
            number=unique(left.id,'tax_id')
            if number and number==unique(right.id,'tax_id') and key(left.label)==key(right.label):
                rid='rel-'+stable_id(left.id,right.id,'possible_same_as')
                if rid not in existing:
                    result.relations.append(Relation(id=rid,subject_id=left.id,predicate='possible_same_as',object_id=right.id,
                        evidence_ids=list(dict.fromkeys(left.evidence_ids+right.evidence_ids)),reason='matching_valid_tax_id_and_name_requires_review'))
                    existing.add(rid)
    for i,left in enumerate(properties):
        for right in properties[i+1:]:
            keys=['cadastral_article','property_parish','property_municipality']
            complete=all(unique(left.id,k) and unique(left.id,k)==unique(right.id,k) for k in keys)
            compatible=all(not unique(left.id,k) or not unique(right.id,k) or unique(left.id,k)==unique(right.id,k) for k in ['cadastral_section','matrix_type','registry_description'])
            if complete and compatible and left.logical_document_id!=right.logical_document_id:
                rid='rel-'+stable_id(left.id,right.id,'possible_same_as')
                if rid not in existing:
                    result.relations.append(Relation(id=rid,subject_id=left.id,predicate='possible_same_as',object_id=right.id,
                        evidence_ids=list(dict.fromkeys(left.evidence_ids+right.evidence_ids)),reason='matching_cadastral_identity_requires_review'))
                    existing.add(rid)
    articles={unique(p.id,'cadastral_article') for p in properties}
    descriptions={unique(p.id,'registry_description') for p in properties}
    for payment in [e for e in result.entities if e.kind=='payment']:
        refs=by_subject[payment.id].get('related_property_article',[])
        for a in refs:
            if a.typed_value not in articles and a.typed_value in descriptions:
                a.validation_issues=list(dict.fromkeys(a.validation_issues+['reference_namespace_or_value_mismatch']))
            for prop in properties:
                if a.typed_value!=unique(prop.id,'cadastral_article'):continue
                if not all(unique(payment.id,x) and unique(payment.id,x)==unique(prop.id,y) for x,y in
                    [('related_parish','property_parish'),('related_municipality','property_municipality')]):continue
                rid='rel-'+stable_id(payment.id,prop.id,'references_property')
                if rid not in existing:
                    result.relations.append(Relation(id=rid,subject_id=payment.id,predicate='references_property',object_id=prop.id,
                        evidence_ids=list(dict.fromkeys(a.evidence_ids+prop.evidence_ids)),reason='matching_article_and_locality_requires_review'))
                    existing.add(rid)
    payments=[e for e in result.entities if e.kind=='payment']
    for i,left in enumerate(payments):
        for right in payments[i+1:]:
            reference=unique(left.id,'payment_reference') or unique(left.id,'receipt_reference')
            other=unique(right.id,'payment_reference') or unique(right.id,'receipt_reference')
            if reference and reference==other:
                rid='rel-'+stable_id(left.id,right.id,'payment_duplicate')
                if rid not in existing:
                    result.relations.append(Relation(id=rid,subject_id=left.id,predicate='possible_same_as',object_id=right.id,
                        evidence_ids=list(dict.fromkeys(left.evidence_ids+right.evidence_ids)),reason='possible_duplicate_payment_do_not_sum'))
                    existing.add(rid)
