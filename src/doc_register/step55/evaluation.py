"""Fact evaluation at extraction/automatic/human layers with evidence and identity."""
from collections import Counter
import json
from .canonical import CanonicalResult


def annotation_template(result):
    return {'documents':[{'id':result.source_sha256,'sha256':result.source_sha256,'group_id':'SET_CONTRACT_FAMILY',
        'split':'development','annotators':[],'fully_annotated_fields':[],
        'entities':[{'gold_id':e.id,'kind':e.kind,'identity':{},'evidence_pages':sorted({x.page for x in result.evidence if x.id in e.evidence_ids})} for e in result.entities],
        'facts':[], 'relations':[], 'fully_annotated_relations':[], 'complete_objects':[]}], 'instructions':'Annotate original PDF independently. Identity is a mapping of predicate to canonical value. Do not copy predictions into gold. Mark complete_objects only after annotating the entire object.'}


def evaluate(manifest,predictions,split='test'):
    families={};hashes={};seen=set()
    for doc in manifest['documents']:
        if doc['id'] in seen:raise ValueError('duplicate_document_id')
        seen.add(doc['id'])
        if doc['split'] not in {'development','calibration','test'}:raise ValueError('invalid_split')
        for table,key in ((families,doc['group_id']),(hashes,doc['sha256'])):
            if key in table and table[key]!=doc['split']:raise ValueError('dataset_split_leakage')
            table[key]=doc['split']
        if not doc.get('annotators') or not doc.get('fully_annotated_fields'):raise ValueError('human_annotation_required')
    report={}
    for layer in ['extracted','automatic','after_human']:
        counts=Counter(expected=0,correct=0,false_positive=0,omissions=0,wrong_relation_or_value=0,evidence_errors=0,
                       objects_expected=0,objects_complete=0,relations_expected=0,relations_correct=0,relations_false_positive=0)
        for doc in manifest['documents']:
            if doc['split']!=split:continue
            gold=[f for f in doc['facts'] if f.get('legibility','legible')=='legible']
            for f in gold:
                if not f.get('evidence_pages'):raise ValueError('gold_evidence_required')
            counts['expected']+=len(gold)
            counts['objects_expected']+=len(doc.get('complete_objects',[]))
            gold_relations=doc.get('relations',[])
            counts['relations_expected']+=len(gold_relations)
            result=predictions.get(doc['id'])
            if result is None:counts['omissions']+=len(gold);continue
            result=result if isinstance(result,CanonicalResult) else CanonicalResult.model_validate(result)
            if result.source_sha256!=doc['sha256']:raise ValueError('prediction_hash_mismatch')
            mapping={};used=set()
            for ge in doc['entities']:
                eligible=[]
                for entity in result.entities:
                    if entity.kind!=ge['kind'] or entity.id in used:continue
                    facts=[a for a in result.assertions if a.subject_id==entity.id and a.acceptance_status!='rejected']
                    if not ge['identity']:continue
                    if all(any(a.predicate==k and a.typed_value==v for a in facts) for k,v in ge['identity'].items()):eligible.append(entity.id)
                if len(eligible)==1:mapping[eligible[0]]=ge['gold_id'];used.add(eligible[0])
            selected=[a for a in result.assertions if a.predicate in doc['fully_annotated_fields'] and a.acceptance_status!='rejected'
                and (layer=='extracted' or a.acceptance_status=='accepted_automatic' or layer=='after_human' and a.acceptance_status=='accepted_human')]
            evidence={e.id:e for e in result.evidence}
            matched=set()
            for a in selected:
                candidates=[(i,f) for i,f in enumerate(gold) if i not in matched and f['subject_id']==mapping.get(a.subject_id) and f['predicate']==a.predicate
                            and f['value']==a.typed_value and f.get('scope',{})=={k:mapping.get(v,v) for k,v in a.scope.items()}]
                hit=next(((i,f) for i,f in candidates if set(f['evidence_pages'])<= {evidence[e].page for e in a.evidence_ids}),None)
                if hit:matched.add(hit[0]);counts['correct']+=1
                else:
                    counts['false_positive']+=1
                    counts['evidence_errors' if candidates else 'wrong_relation_or_value']+=1
            counts['omissions']+=len(gold)-len(matched)
            for subject in doc.get('complete_objects',[]):
                required={i for i,f in enumerate(gold) if f['subject_id']==subject}
                extra=[a for a in selected if mapping.get(a.subject_id)==subject and not any(
                    f['predicate']==a.predicate and f['value']==a.typed_value for f in gold if f['subject_id']==subject)]
                if required and required<=matched and not extra:counts['objects_complete']+=1
            relation_matches=set()
            for relation in result.relations:
                if relation.predicate not in doc.get('fully_annotated_relations',[]) or relation.status=='rejected':continue
                # This pilot never automatically approves identity/role relations.
                if layer=='automatic' or layer=='after_human' and relation.status!='resolved':continue
                hit=next((i for i,g in enumerate(gold_relations) if i not in relation_matches
                    and g['subject_id']==mapping.get(relation.subject_id) and g['object_id']==mapping.get(relation.object_id)
                    and g['predicate']==relation.predicate and g.get('role')==relation.role
                    and g.get('evidence_pages') and set(g['evidence_pages'])<={evidence[e].page for e in relation.evidence_ids}),None)
                if hit is None:counts['relations_false_positive']+=1
                else:relation_matches.add(hit);counts['relations_correct']+=1
        tp,fp,n=counts['correct'],counts['false_positive'],counts['expected']
        report[layer]={**counts,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/n if n else None}
    documents=[]
    for doc in manifest['documents']:
        if doc['split']!=split:continue
        raw=predictions.get(doc['id'])
        if raw is None:documents.append({'id':doc['id'],'status':'not_processed'});continue
        result=raw if isinstance(raw,CanonicalResult) else CanonicalResult.model_validate(raw)
        documents.append({'id':doc['id'],'coverage':result.coverage,'metrics':result.metrics,
            'review_required':sum(a.acceptance_status=='needs_review' for a in result.assertions),
            'facts':len(result.assertions),'conflicts':sum(a.resolution_status=='conflict' for a in result.assertions)})
    report['operational']={'documents':documents,'sample_size':len(documents),
        'performance_quantiles':None,'note':'Raw per-document measurements; no population claims from this sample.'}
    return report
