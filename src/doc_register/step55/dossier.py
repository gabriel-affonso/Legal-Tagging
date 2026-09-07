"""Cross-file proposals, preserving each source and never aggregating payments."""
from pathlib import Path
from .canonical import CanonicalResult, stable_id
from .resolution import propose_links
from .storage import atomic_json, digest_file


def link_results(paths, destination):
    destination=Path(destination)
    if destination.exists():raise FileExistsError('dossier_destination_exists')
    results=[];sources=[];seen=set()
    for name in paths:
        path=Path(name).resolve()
        result=CanonicalResult.model_validate_json(path.read_text())
        if result.source_sha256 in seen:raise ValueError('choose_one_result_version_per_source')
        seen.add(result.source_sha256);results.append(result)
        sources.append({'sha256':result.source_sha256,'run_id':result.run_id,'result_path':str(path),
            'result_sha256':digest_file(path),'original_path':result.source_path})
    if not results:raise ValueError('dossier_sources_required')
    # A temporary union is used only for matching, never saved as a single-source result.
    union=results[0].model_copy(deep=True)
    for field in ('entities','assertions','evidence','relations','logical_documents','observations'):
        setattr(union,field,[item for result in results for item in getattr(result,field)])
    original_ids={r.id for r in union.relations}
    propose_links(union)
    links=[r.model_dump(mode='json') for r in union.relations if r.id not in original_ids]
    issues=[{'assertion_id':a.id,'issues':a.validation_issues} for a in union.assertions if 'reference_namespace_or_value_mismatch' in a.validation_issues]
    entity_source={e.id:r.source_sha256 for r in results for e in r.entities}
    payload={'version':'5.5.1','id':stable_id(sources),'sources':sources,'entity_source':entity_source,
        'links':links,'issues':issues,'publication_status':'review_required','payment_aggregation':'disabled'}
    atomic_json(destination,payload)
    return destination
