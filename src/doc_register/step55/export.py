"""New-file staging exports with referential integrity and literal cell text."""
import json
from pathlib import Path
from .canonical import CanonicalResult
from .storage import locked


def staging_projection(result):
    entities={e.id:e for e in result.entities}
    rows=[]
    for a in result.assertions:
        eligible=a.acceptance_status.startswith('accepted_') and a.resolution_status=='resolved'
        rows.append({'subject_type':entities[a.subject_id].kind,'subject_id':a.subject_id,'subject_label':entities[a.subject_id].label,
            'predicate':a.predicate,'value':a.typed_value,'scope':a.scope,'origin_kind':a.origin_kind,
            'acceptance_status':a.acceptance_status,'resolution_status':a.resolution_status,'evidence_ids':a.evidence_ids,
            'eligible_for_staging':eligible})
    return {'kind':'step55_staging_projection','source_sha256':result.source_sha256,'run_id':result.run_id,
            'export_blocked':True,'reason':'Operational publication requires a separate reviewed projection policy',
            'rows':rows,'unresolved_relations':[r.model_dump() for r in result.relations if r.status!='resolved']}


def export_staging(paths,destination):
    from openpyxl import Workbook
    destination=Path(destination).resolve()
    results=[CanonicalResult.model_validate_json(Path(path).read_text()) for path in paths]
    if len({r.source_sha256 for r in results})!=len(results):raise ValueError('choose_one_version_per_source_for_staging')
    with locked(destination.with_suffix('.lock')):
        if destination.exists():raise FileExistsError('staging_destination_already_exists')
        book=Workbook();book.remove(book.active)
        sheets={
            'stg.Sources':['run_id','source_sha256','source_path','schema_version'],
            'stg.Documents':['run_id','document_id','type','pages','parent_id'],
            'stg.Entities':['run_id','entity_id','kind','label','identity_status','document_id'],
            'stg.Facts':['run_id','assertion_id','subject_id','predicate','value_json','scope_json','origin','acceptance','source_authority','acceptance_basis','evidence_ids'],
            'stg.Relations':['run_id','id','subject','predicate','object','status','role','reason','evidence_ids'],
            'stg.Derivations':['run_id','id','subject','operation','inputs','value','status','reason'],
            'audit.Candidates':['run_id','id','subject','predicate','value','status','issues','evidence_ids'],
            'audit.Evidence':['run_id','id','page','region','start','end','quote','method'],
        }
        for name,headers in sheets.items():book.create_sheet(name).append(headers)
        js=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True)
        for result in results:
            book['stg.Sources'].append([result.run_id,result.source_sha256,result.source_path,result.schema_version])
            for d in result.logical_documents:book['stg.Documents'].append([result.run_id,d.id,d.source_type,js(d.page_numbers),d.parent_id])
            for e in result.entities:book['stg.Entities'].append([result.run_id,e.id,e.kind,e.label,e.identity_status,e.logical_document_id])
            for a in result.assertions:
                book['audit.Candidates'].append([result.run_id,a.id,a.subject_id,a.predicate,js(a.typed_value),a.acceptance_status,js(a.validation_issues),js(a.evidence_ids)])
                if a.acceptance_status.startswith('accepted_') and a.resolution_status=='resolved':
                    book['stg.Facts'].append([result.run_id,a.id,a.subject_id,a.predicate,js(a.typed_value),js(a.scope),a.origin_kind,a.acceptance_status,a.source_authority,a.acceptance_basis,js(a.evidence_ids)])
            for r in result.relations:book['stg.Relations'].append([result.run_id,r.id,r.subject_id,r.predicate,r.object_id,r.status,r.role,r.reason,js(r.evidence_ids)])
            for d in result.derivations:book['stg.Derivations'].append([result.run_id,d.id,d.subject_id,d.operation,js(d.input_ids),js(d.value),d.status,d.reason])
            for e in result.evidence:book['audit.Evidence'].append([result.run_id,e.id,e.page,e.region_id,e.start,e.end,e.quote,e.reading_method])
        for sheet in book:
            sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value,str):cell.data_type='s'
        destination.parent.mkdir(parents=True,exist_ok=True)
        import tempfile
        with tempfile.TemporaryDirectory(dir=destination.parent,prefix='.step55-export-') as temp:
            target=Path(temp)/'staging.xlsx';book.save(target);target.replace(destination)
        book.close()
    return destination
