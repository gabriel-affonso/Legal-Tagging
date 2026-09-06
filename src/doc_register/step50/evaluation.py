"""Fact/relation evaluation. Human gold only; omitted facts remain false negatives."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
from statistics import mean

from .schema import CRITICAL, name_key


def fact_key(fact):
    value = fact['value']
    if fact['field'] in {'name', 'bank_account_holder'} and isinstance(value, str):
        value = name_key(value)
    return (fact['logical_document'], name_key(fact['entity']), fact['field'], json.dumps(value, sort_keys=True, ensure_ascii=False))


def ratios(counts):
    tp, fp, expected = counts['correct'], counts['false_positive'], counts['expected']
    recall = tp / expected if expected else None
    precision = tp / (tp + fp) if tp + fp else None
    return {**counts, 'recall': recall, 'precision': precision,
            'f1': 2 * tp / (expected + tp + fp) if expected + tp + fp else None,
            'coverage': counts.get('decided_expected_slots', 0) / expected if expected else None}


def validate_manifest(manifest):
    groups = {}
    ids = set()
    for doc in manifest['documents']:
        if doc['id'] in ids:
            raise ValueError('duplicate_document_id')
        ids.add(doc['id'])
        group, split = doc['group_id'], doc['split']
        if split not in {'development', 'calibration', 'test'}:
            raise ValueError('invalid_split')
        if group in groups and groups[group] != split:
            raise ValueError('contract_group_split_leakage')
        groups[group] = split
        if len(doc['sha256']) != 64 or not doc.get('annotators') or not doc.get('fully_annotated_fields'):
            raise ValueError('human_annotation_provenance_required')
        for fact in doc['facts']:
            if fact['field'] not in doc['fully_annotated_fields'] or fact['legibility'] not in {'legible', 'illegible', 'absent'}:
                raise ValueError('invalid_fact_annotation')
            if fact['legibility'] == 'legible' and (not fact.get('evidence_pages') or fact.get('value') is None):
                raise ValueError('gold_evidence_required')
        if len({(f['logical_document'], f['entity'], f['field'], json.dumps(f.get('value'), sort_keys=True)) for f in doc['facts']}) != len(doc['facts']):
            raise ValueError('duplicate_gold_fact')
    hashes = {}
    for doc in manifest['documents']:
        if doc['sha256'] in hashes and hashes[doc['sha256']] != doc['split']:
            raise ValueError('duplicate_file_split_leakage')
        hashes[doc['sha256']] = doc['split']


def evaluate(manifest, predictions, *, split='test', human=False):
    validate_manifest(manifest)
    totals = Counter()
    strata = defaultdict(Counter)
    per_document = []
    grouped = defaultdict(list)
    for doc in manifest['documents']:
        if doc['split'] != split:
            continue
        result = predictions.get(doc['id'])
        if result and result['document']['sha256'] != doc['sha256']:
            raise ValueError('prediction_document_hash_mismatch')
        scope = set(doc['fully_annotated_fields'])
        gold = [f for f in doc['facts'] if f['legibility'] == 'legible']
        ignored = {(f['logical_document'], name_key(f['entity']), f['field']) for f in doc['facts'] if f['legibility'] == 'illegible'}
        absent = {(f['logical_document'], name_key(f['entity']), f['field']) for f in doc['facts'] if f['legibility'] == 'absent'}
        predicted = [f for f in (result or {}).get('fields', []) if f['status'] == 'accepted' and
                     (human or f.get('acceptance') == 'automatic') and f['field'] in scope]
        ignored_predictions = [f for f in predicted if fact_key(f)[:3] in ignored]
        predicted = [f for f in predicted if fact_key(f)[:3] not in ignored]
        counts = Counter(expected=len(gold), correct=0, false_positive=0, omissions=0, wrong_value_or_relation=0,
                         evidence_errors=0, false_absent=0, decided_expected_slots=0,
                         illegible=sum(f['legibility'] == 'illegible' for f in doc['facts']),
                         published_on_illegible=len(ignored_predictions), absent=len(absent),
                         review_fields=sum(f['status'] != 'accepted' for f in (result or {}).get('fields', [])))
        pred_map = defaultdict(list)
        for p in predicted:
            pred_map[fact_key(p)].append(p)
        gold_keys = {fact_key(g) for g in gold}
        matched = set()
        evidence_correct = set()
        for g in gold:
            key = fact_key(g)
            matches = pred_map.get(key, [])
            field_counts = Counter(expected=1)
            if any(fact_key(p)[:3] == key[:3] for p in predicted):
                counts['decided_expected_slots'] += 1
                field_counts['decided_expected_slots'] += 1
            correct = False
            if matches:
                p = matches.pop(0)
                regions = {r['id']: r for page in result['pages'] for r in page['regions']}
                candidates = {c['id']: c for c in result['candidates']}
                for cid in p.get('candidate_ids', []):
                    c = candidates.get(cid, {})
                    r = regions.get(c.get('region_id'), {})
                    # Localized evidence is required as well as equal field/entity/value.
                    if c.get('value') == p['value'] and r.get('page') in g['evidence_pages'] and c.get('quote') and c['quote'] in r.get('text', ''):
                        correct = True
                if human and p.get('acceptance') == 'human':
                    correct = correct or any(e.get('field') == p['field'] and e.get('entity') == p['entity'] and
                                            e.get('logical_document') == p['logical_document'] and e.get('page') in g['evidence_pages']
                                            for e in result.get('human_review_events', []))
                if not correct:
                    counts['evidence_errors'] += 1
            if correct:
                counts['correct'] += 1
                field_counts['correct'] += 1
                matched.add(key)
                evidence_correct.add(key)
            else:
                counts['omissions'] += 1
                field_counts['omissions'] += 1
            strata['field:' + g['field']].update(field_counts)
        counts['false_positive'] = len(predicted) - counts['correct']
        counts['false_absent'] = sum(fact_key(p)[:3] in absent for p in predicted)
        counts['wrong_value_or_relation'] = sum(fact_key(p) not in gold_keys for p in predicted)
        for p in predicted:
            # Extras, wrong values and duplicate predictions count per field too.
            key = fact_key(p)
            if key in matched:
                matched.remove(key)
            else:
                strata['field:' + p['field']]['false_positive'] += 1
        critical = [g for g in gold if g['field'] in CRITICAL]
        critical_predictions = [p for p in predicted if p['field'] in CRITICAL]
        counts['critical_expected'] = len(critical)
        counts['critical_correct'] = sum(fact_key(g) in evidence_correct for g in critical)
        counts['critical_false_positive'] = len(critical_predictions) - counts['critical_correct']
        counts['critical_documents'] = int(bool(critical))
        counts['critical_documents_correct'] = int(bool(critical) and counts['critical_correct'] == len(critical)
                                                   and counts['critical_false_positive'] == 0)
        for tag in doc.get('tags', []):
            strata['tag:' + tag].update(counts)
        totals.update(counts)
        grouped[doc['group_id']].append(counts)
        per_document.append({'id': doc['id'], **ratios(counts)})
    # Cluster bootstrap keeps all documents of a contractual group together.
    interval = None
    groups = list(grouped.values())
    if len(groups) >= 2:
        rng = random.Random(50)
        samples = []
        for _ in range(2000):
            sample = [c for group in rng.choices(groups, k=len(groups)) for c in group]
            denominator = sum(c['expected'] for c in sample)
            if denominator:
                samples.append(sum(c['correct'] for c in sample) / denominator)
        if samples:
            samples.sort()
            interval = [samples[int(.025*(len(samples)-1))], samples[int(.975*(len(samples)-1))]]
    report = {'schema_version': '5.0', 'evaluation_mode': 'after_human_review' if human else 'automatic',
              'split': split, 'documents': len(per_document), 'contract_groups': len(groups),
              'micro': ratios(totals), 'macro_document_recall': mean([d['recall'] for d in per_document if d['recall'] is not None]) if any(d['recall'] is not None for d in per_document) else None,
              'recall_cluster_bootstrap_95_interval': interval,
              'breakdown': {k: ratios(v) for k, v in sorted(strata.items())}, 'per_document': per_document,
              'precision_coverage_curve': None, 'curve_reason': 'candidate_probabilities_not_calibrated',
              'target_95_measured': None if not totals['expected'] else totals['correct']/totals['expected'] >= .95,
              'target_claim': 'not_established: independent representative corpus and calibrated critical precision required'}
    report['critical_precision'] = totals['critical_correct'] / (totals['critical_correct'] + totals['critical_false_positive']) if totals['critical_correct'] + totals['critical_false_positive'] else None
    report['critical_document_exact_rate'] = totals['critical_documents_correct'] / totals['critical_documents'] if totals['critical_documents'] else None
    return report


def annotation_template(result):
    """Blank values by design. Model predictions are never exported as gold answers."""
    return {'schema_version': '5.0', 'documents': [{'id': result['document']['id'], 'sha256': result['document']['sha256'],
            'group_id': 'REPLACE_WITH_CONTRACT_GROUP', 'split': 'development', 'annotators': [], 'tags': [],
            'fully_annotated_fields': [], 'facts': [],
            'annotation_instructions': 'Annotate from original.pdf independently; list every fact, entity, logical_document, canonical value, legibility and evidence_pages. Complete all occurrences of each fully_annotated_fields entry.'}]}
