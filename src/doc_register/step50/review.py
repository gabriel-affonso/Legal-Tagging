"""Offline review report and immutable human decisions, separate from automatic facts."""
from copy import deepcopy
from datetime import datetime, timezone
import html
import json
from pathlib import Path

from .schema import normalize, identity
from .storage import atomic_json, locked


def write_review(result, path):
    candidates = {c['id']: c for c in result['candidates']}
    regions = {r['id']: r for p in result['pages'] for r in p['regions']}
    esc = lambda value: html.escape(str(value))
    rows = []
    for f in result['fields']:
        evidence = []
        for cid in f['candidate_ids']:
            c = candidates[cid]
            r = regions[c['region_id']]
            link = Path(result['document']['original_path']).as_uri() + '#page=' + str(r['page'])
            crop = r.get('image', {}).get('path')
            evidence.append(f'<p><a href="{esc(link)}">Página {r["page"]}</a> — {esc(c["method"])} — {esc(c["raw_value"])}</p><pre>{esc(c["quote"])}</pre>' + (f'<a href="{esc(Path(crop).as_uri())}">Recorte original</a>' if crop else ''))
        rows.append('<tr><td>' + esc(f['logical_document']) + '<br>' + esc(f['entity']) + '</td><td>' + esc(f['field']) + '</td><td>' + esc(f['status']) + '<br>' + esc(f['reasons']) + '</td><td>' + ''.join(evidence) + '</td></tr>')
    page_issues = [{'page': p['number'], 'status': p['status'], 'issues': p['issues'], 'regions': [{'id': r['id'], 'issues': r['issues']} for r in p['regions'] if r['issues']]} for p in result['pages']]
    path.write_text('<!doctype html><html lang="pt"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src file: data:"><title>Revisão Step 5.0</title><style>body{font:15px system-ui;margin:32px}td,th{border:1px solid #bbb;padding:12px;vertical-align:top}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;max-width:700px}</style><h1>Revisão documental — Step 5.0</h1><p>Os valores automáticos permanecem separados das decisões humanas.</p><pre>' + esc(result['issues']) + '\n' + esc(page_issues) + '</pre><table><tr><th>Entidade / documento</th><th>Campo</th><th>Estado</th><th>Evidência</th></tr>' + ''.join(rows) + '</table></html>', encoding='utf-8')


def apply_decisions(result_path: Path, decisions_path: Path, reviewer: str) -> Path:
    if not reviewer.strip():
        raise ValueError('reviewer_required')
    result = json.loads(result_path.read_text())
    decisions = json.loads(decisions_path.read_text())
    if decisions.get('run_id') != result['run_id'] or decisions.get('sha256') != result['document']['sha256']:
        raise ValueError('review_document_or_run_mismatch')
    by_id = {c['id']: c for c in result['candidates']}
    reviewed = deepcopy(result)
    events = list(reviewed.get('human_review_events', []))
    for decision in decisions['decisions']:
        matches = [f for f in reviewed['fields'] if all(f[k] == decision[k] for k in ['field', 'entity', 'logical_document'])]
        if len(matches) != 1 or not decision.get('reason'):
            raise ValueError('review_field_or_reason_invalid')
        target = matches[0]
        selected = decision.get('candidate_id')
        if selected:
            if selected not in target['candidate_ids']:
                raise ValueError('candidate_does_not_belong_to_field')
            candidate = by_id[selected]
            if candidate['value'] is None or 'unanchored_evidence' in candidate['issues']:
                raise ValueError('invalid_candidate_cannot_be_accepted')
            value = candidate['value']
        else:
            # Corrections require a reproducing source page, quote and explicit reviewer rationale.
            page = decision.get('page')
            if not isinstance(page, int) or not 1 <= page <= result['document']['page_count'] or not decision.get('evidence'):
                raise ValueError('correction_requires_page_and_evidence')
            value, _ = normalize(decision['field'], decision['value'])
        target.update(value=value, status='accepted', acceptance='human', reasons=[decision['reason']])
        events.append({**decision, 'reviewer': reviewer, 'at': datetime.now(timezone.utc).isoformat()})
    reviewed['human_review_events'] = events
    reviewed['relationships'] = [
        {'subject': f['entity'], 'predicate': f['field'], 'object': f['value'],
         'candidate_ids': f['candidate_ids'], 'status': f['status'], 'logical_document': f['logical_document']}
        for f in reviewed['fields'] if f['status'] == 'accepted' and f['field'] in {'role', 'owner', 'represented_by', 'amends'}
    ]
    reviewed['automatic_result'] = result.get('automatic_result', str(result_path.resolve()))
    # Content-derived event key makes importing the same decisions idempotent.
    destination = result_path.parent / 'reviews' / (identity(decisions, reviewer) + '.json')
    with locked(result_path.parent / '.review.lock'):
        if not destination.exists():
            atomic_json(destination, reviewed)
    return destination
