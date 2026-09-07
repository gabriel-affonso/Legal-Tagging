"""Offline object-oriented review and immutable decisions with dependency rebuild."""
from datetime import datetime,timezone
import html
import json
from pathlib import Path
from .canonical import CanonicalResult,ResolutionDecision,Entity,Assertion,Relation,stable_id
from .resolution import reconcile
from .storage import atomic_json,locked


def write_review(result,path):
    esc=lambda x:html.escape(str(x))
    evidence={e.id:e for e in result.evidence}
    blocks=[]
    for entity in result.entities:
        facts=[]
        for a in result.assertions:
            if a.subject_id!=entity.id:continue
            quotes=''.join(f'<details><summary>Página {evidence[e].page} · {esc(evidence[e].reading_method)}</summary><a href="{esc(Path(result.source_path).as_uri())}#page={evidence[e].page}">Abrir original nesta página</a><pre>{esc(evidence[e].quote)}</pre></details>' for e in a.evidence_ids)
            facts.append(f'<tr><td>{esc(a.predicate)}<br><small>{esc(a.id)}</small></td><td><pre>{esc(json.dumps(a.typed_value,ensure_ascii=False,indent=2))}</pre></td><td>{esc(a.acceptance_status)} / {esc(a.resolution_status)}<br>{esc(a.validation_issues)}{quotes}</td></tr>')
        relations=[r.model_dump() for r in result.relations if entity.id in {r.subject_id,r.object_id}]
        blocks.append(f'<section><h2>{esc(entity.label)} · {esc(entity.kind)}</h2><p>{esc(entity.id)} · {esc(entity.identity_status)}</p><table>{"".join(facts)}</table><details><summary>Relações</summary><pre>{esc(json.dumps(relations,ensure_ascii=False,indent=2))}</pre></details></section>')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('<!doctype html><html lang="pt"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Step 5.5 revisão</title><style>body{font:15px system-ui;max-width:1200px;margin:32px auto}section{margin:30px 0}td{border:1px solid #ddd;padding:12px;vertical-align:top}pre{white-space:pre-wrap;max-width:600px}table{width:100%;border-collapse:collapse}</style><h1>Revisão por entidade e relação</h1><p>As decisões são importadas pelo comando review; o original permanece imutável.</p><pre>'+esc(json.dumps(result.coverage,ensure_ascii=False,indent=2))+'</pre>'+''.join(blocks)+'<h2>Lacunas e tarefas</h2><pre>'+esc(json.dumps([t.model_dump() for t in result.tasks],ensure_ascii=False,indent=2))+'</pre><h2>Derivações</h2><pre>'+esc(json.dumps([d.model_dump() for d in result.derivations],ensure_ascii=False,indent=2))+'</pre></html>',encoding='utf-8')


def apply_decisions(result_path,decisions_path,reviewer):
    if not reviewer.strip():raise ValueError('reviewer_required')
    path=Path(result_path).resolve();result=CanonicalResult.model_validate_json(path.read_text())
    payload=json.loads(Path(decisions_path).read_text())
    if payload.get('run_id')!=result.run_id or payload.get('source_sha256')!=result.source_sha256:raise ValueError('review_run_mismatch')
    review_id=stable_id(path.read_bytes().hex(),payload,reviewer)
    destination=path.parent/'reviews'/review_id/'result.json'
    with locked(path.parent/'.review.lock'):
        if destination.exists():return destination
        facts={a.id:a for a in result.assertions};entities={e.id:e for e in result.entities};relations={r.id:r for r in result.relations}
        evidence={e.id:e for e in result.evidence};accepted=[]
        for change in payload['decisions']:
            if not change.get('reason'):raise ValueError('decision_reason_required')
            target=change['target_id'];action=change['action'];ids=change.get('evidence_ids',[])
            if not ids or not set(ids)<=set(evidence):raise ValueError('decision_evidence_required')
            if action in {'accept','reject','reassign','correct'}:
                if target not in facts:raise ValueError('unknown_assertion')
                a=facts[target]
                if not set(ids)&set(a.evidence_ids):raise ValueError('evidence_not_related_to_assertion')
                if action=='reject':a.acceptance_status='rejected';a.acceptance_basis=None
                elif action=='reassign':
                    if change['subject_id'] not in entities:raise ValueError('unknown_reassignment_entity')
                    a.subject_id=change['subject_id'];a.acceptance_status='needs_review';a.acceptance_basis=None
                elif action=='correct':
                    from .values import validate_value
                    value=validate_value(a.predicate,change['value'])
                    a.acceptance_status='rejected';a.acceptance_basis=None
                    corrected=a.model_copy(deep=True);corrected.id='as-'+stable_id(target,review_id,change)
                    corrected.typed_value=value;corrected.origin_kind='humano';corrected.evidence_ids=ids
                    corrected.extractor_id='human-review';corrected.normalization_steps=['human_correction_of:'+a.id]
                    corrected.evidence_offsets={}
                    corrected.acceptance_status='needs_review';corrected.validation_issues=[]
                    corrected.dependency_ids=[a.id];result.assertions.append(corrected);facts[corrected.id]=corrected;accepted.append(corrected.id)
                else:accepted.append(a.id)
            elif action in {'confirm_link','reject_link'}:
                if target not in relations:raise ValueError('unknown_relation')
                r=relations[target]
                if not set(ids)&set(r.evidence_ids):raise ValueError('relation_evidence_mismatch')
                if action=='reject_link':r.status='rejected'
                elif r.predicate=='possible_same_as':
                    if entities[r.subject_id].kind!=entities[r.object_id].kind:raise ValueError('identity_type_mismatch')
                    for field in ('tax_id','cadastral_article','cadastral_section','property_parish','property_municipality','registry_description'):
                        left={str(a.typed_value) for a in facts.values() if a.subject_id==r.subject_id and a.predicate==field and a.acceptance_status!='rejected'}
                        right={str(a.typed_value) for a in facts.values() if a.subject_id==r.object_id and a.predicate==field and a.acceptance_status!='rejected'}
                        if left and right and left!=right:raise ValueError('conflicting_identity_requires_correction')
                    r.predicate='same_as'
                    r.status='resolved'
                else:r.status='resolved'
            elif action=='confirm_entity':
                if target not in entities or not set(ids)&set(entities[target].evidence_ids):raise ValueError('unknown_or_unanchored_entity')
                if entities[target].identity_status=='conflict':raise ValueError('conflicting_entity_requires_correction')
                entities[target].identity_status='resolved'
            elif action=='add_entity':
                from .extraction import GENERIC,key
                label=change['label']
                if key(label) in GENERIC or not any(label in evidence[i].quote for i in ids):raise ValueError('unanchored_entity_label')
                doc=change['logical_document_id']
                if not any(d.id==doc and all(evidence[i].region_id in d.region_ids for i in ids) for d in result.logical_documents):raise ValueError('entity_document_evidence_mismatch')
                entity=Entity(id='human-'+stable_id(review_id,change),kind=change['kind'],label=label,logical_document_id=doc,evidence_ids=ids,identity_status='resolved')
                result.entities.append(entity);entities[entity.id]=entity
                target=entity.id
            elif action=='add_assertion':
                a=Assertion(id='as-'+stable_id(review_id,change),subject_id=change['subject_id'],predicate=change['predicate'],
                    raw_value='\n'.join(evidence[i].quote for i in ids),typed_value=change['value'],evidence_ids=ids,
                    scope=change.get('scope',{}),source_authority='unknown',extractor_id='human-review',origin_kind='humano')
                result.assertions.append(a);facts[a.id]=a;accepted.append(a.id);target=a.id
            elif action=='add_relation':
                relation=Relation(id='rel-'+stable_id(review_id,change),subject_id=change['subject_id'],
                    predicate=change['predicate'],object_id=change['object_id'],evidence_ids=ids,
                    role=change.get('role'),status='resolved',reason=change['reason'])
                if relation.predicate in {'same_as','possible_same_as'}:raise ValueError('identity_links_require_matching_proposal')
                result.relations.append(relation);relations[relation.id]=relation;target=relation.id
            elif action=='split_entity':
                if target not in entities:raise ValueError('unknown_entity')
                original=entities[target]
                selected=change.get('assertion_ids',[])
                if not selected or any(i not in facts or facts[i].subject_id!=target for i in selected):raise ValueError('split_assertions_invalid')
                clone=original.model_copy(deep=True);clone.id=original.kind+'-'+stable_id(review_id,target,selected);clone.evidence_ids=ids
                result.entities.append(clone);entities[clone.id]=clone
                for i in selected:facts[i].subject_id=clone.id;facts[i].acceptance_status='needs_review';facts[i].acceptance_basis=None
            else:raise ValueError('unknown_review_action')
            result.decisions.append(ResolutionDecision(id=stable_id(review_id,change),target_id=target,action=action,reason=change['reason'],author=reviewer,at=datetime.now(timezone.utc).isoformat(),evidence_ids=ids))
        reconcile(result)
        for aid in accepted:
            a=facts[aid]
            if a.resolution_status=='conflict':raise ValueError('reject_conflicting_alternatives_before_acceptance')
            a.resolution_status='resolved';a.acceptance_status='accepted_human';a.acceptance_basis='human:'+reviewer
        # Newly accepted occurrence dates can now unlock dependent calculations.
        reconcile(result)
        changed={d.target_id for d in result.decisions if d.id in {stable_id(review_id,c) for c in payload['decisions']}}
        changed_facts=[a for a in result.assertions if a.id in changed or set(a.dependency_ids)&changed]
        for task in result.tasks:
            relevant=[a for a in changed_facts if a.subject_id in task.entity_ids and a.predicate in task.permitted_predicates]
            if relevant:
                task.metrics['invalidated_by_review']=[a.id for a in relevant]
                task.status='deferred';task.reason+=';review_inputs_changed'
                result.coverage[f'{task.logical_document_id}:{task.task_type}']='requires_reassessment_after_review'
        for a in changed_facts:
            doc=entities[a.subject_id].logical_document_id
            candidates=[x for x in result.assertions if entities[x.subject_id].logical_document_id==doc and x.predicate==a.predicate and x.acceptance_status!='rejected']
            state='conflict' if any(x.resolution_status=='conflict' for x in candidates) else 'reviewed' if candidates and all(x.acceptance_status=='accepted_human' for x in candidates) else 'evaluated_needs_review' if candidates else 'unresolved_after_review'
            result.coverage[f'{doc}:field:{a.predicate}']=state
        result=CanonicalResult.model_validate(result.model_dump())
        result.provenance['automatic_result']=str(path)
        result.provenance['review_id']=review_id
        atomic_json(destination,result.model_dump(mode='json'))
        write_review(result,destination.parent/'review.html')
        return destination
