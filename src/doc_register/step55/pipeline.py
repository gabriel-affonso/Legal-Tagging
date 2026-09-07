"""Versioned, resumable orchestration of the complete local pilot."""
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import time
import uuid
from .canonical import CanonicalResult,SCHEMA_VERSION,stable_id
from .document_map import Observation,split_observations,map_documents
from .extraction import extract_document,RULE_VERSION
from .recognition import inventory,ocr
from .storage import atomic_json,digest_file,locked,runtime,index_result
from .resolution import reconcile
from .tasks import plan_tasks
from .models import LocalModels,PROMPT_VERSION


@dataclass(frozen=True)
class PipelineConfig:
    max_pages:int=200
    max_file_bytes:int=200*1024*1024
    native_min_chars:int=12
    ocr_enabled:bool=True
    ocr_language:str='por+eng'
    ocr_timeout:int=90
    ocr_dpi:int=220
    ocr_max_pixels:int=8_000_000
    rules_enabled:bool=True
    plan_llm_tasks:bool=True
    text_enabled:bool=False
    text_model:str='qwen3:8b'
    model_revision:str='unverified'
    ollama_url:str='http://127.0.0.1:11434'
    max_model_calls:int=6
    max_model_failures:int=2
    max_llm_seconds:int=480
    max_document_seconds:int=600
    context_tokens:int=4096
    memory_budget_bytes:int=10*1024*1024*1024

    def __post_init__(self):
        for key,value in asdict(self).items():
            default=self.__dataclass_fields__[key].default
            if type(value) is not type(default):raise ValueError(key+':invalid_config_type')
            if type(value) is int and value<1:raise ValueError(key+':must_be_positive')
        if self.ocr_dpi<72:raise ValueError('ocr_dpi_too_low')

    @classmethod
    def read(cls,path):return cls() if path is None else cls(**json.loads(Path(path).read_text()))


def staging_projection(result):
    from .export import staging_projection as project
    return project(result)


def _memory():
    try:
        import resource,sys
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*(1 if sys.platform=='darwin' else 1024)
    except ImportError:return None


class Pipeline:
    def __init__(self,output,config=PipelineConfig()):
        self.output=Path(output).resolve();self.config=config

    def process(self,source,*,force=False):
        source=Path(source).resolve()
        if source.suffix.lower()!='.pdf':raise ValueError('pdf_required')
        if source.stat().st_size>self.config.max_file_bytes:raise ValueError('file_size_budget_exceeded')
        digest=digest_file(source);root=self.output/digest
        with locked(root/'.lock'):
            original=root/'original.pdf'
            if not original.exists():
                temporary=root/'original.copying';shutil.copyfile(source,temporary)
                if digest_file(temporary)!=digest:raise ValueError('input_changed_during_ingestion')
                temporary.replace(original)
            elif digest_file(original)!=digest:raise ValueError('immutable_original_hash_mismatch')
            provenance=runtime()
            fingerprint=stable_id(asdict(self.config),provenance['code_sha256'],provenance['dependencies'],SCHEMA_VERSION,RULE_VERSION,PROMPT_VERSION)
            cache=root/fingerprint
            latest=cache/'latest.json'
            reusable=not self.config.text_enabled or self.config.model_revision!='unverified'
            if latest.exists() and reusable and not force:
                path=Path(json.loads(latest.read_text())['result'])
                if path.exists():
                    CanonicalResult.model_validate_json(path.read_text())
                    return path
            run=cache/('run-'+uuid.uuid4().hex)
            started=time.monotonic();deadline=started+self.config.max_document_seconds
            from . import recognition
            from ..step50 import recognition as crop_implementation
            recognition_key=stable_id({k:v for k,v in asdict(self.config).items() if k.startswith('ocr_') or k=='native_min_chars'},
                digest_file(Path(recognition.__file__)),digest_file(Path(crop_implementation.__file__)),provenance['dependencies'])
            recognition_started=time.monotonic()
            observations,pages,issues=inventory(original,self.config,root/('recognition-'+recognition_key),deadline,ocr_reader=ocr,source_hash=digest)
            recognition_seconds=time.monotonic()-recognition_started
            result=self.analyze(observations,pages,digest,str(original),run,deadline,issues)
            result.provenance.update(provenance)
            result.provenance.update(config=asdict(self.config),fingerprint=fingerprint,started_at=datetime.now(timezone.utc).isoformat(),
                                     rule_version=RULE_VERSION,prompt_version=PROMPT_VERSION)
            result.metrics['elapsed_seconds']=time.monotonic()-started
            result.metrics['recognition_seconds']=recognition_seconds
            result.metrics['semantic_seconds']=result.metrics['elapsed_seconds']-recognition_seconds
            result.metrics['python_peak_rss_bytes']=_memory()
            result.provenance['memory_scope']='Python peak RSS; Ollama server memory not included'
            destination=self.save(result,run)
            # Partial recognition/model failures remain retryable, not cached as complete.
            semantic_complete=not any(v=='technical_error' for v in result.coverage.values()) and all(
                t.status in {'completed','not_needed'} or t.status=='deferred' and not self.config.text_enabled for t in result.tasks)
            if reusable and semantic_complete and all(p['status']=='processed' and not p['issues'] for p in pages):
                atomic_json(latest,{'result':str(destination)})
            return destination

    def analyze(self,observations,pages,digest,source_path,run,deadline,issues=None):
        selected={rid for p in pages for rid in p.get('selected_region_ids',p.get('region_ids',[]))}
        fragments=split_observations([o for o in observations if o.id in selected])
        documents=map_documents(fragments)
        # Preserve native/OCR observations that were not selected too.
        all_obs={o.id:o for o in observations}
        all_obs.update({o.id:o for o in fragments})
        evidence,entities,assertions,relations={},{},{},{}
        rejected=[]
        for doc in documents:
            if time.monotonic()>=deadline:
                rejected.append({'reason':'semantic_budget_exhausted','document_id':doc.id});continue
            current_memory=_memory()
            if current_memory and current_memory>self.config.memory_budget_bytes:
                rejected.append({'reason':'memory_budget_exhausted','document_id':doc.id});continue
            try:
                ev,en,aa,rr,errors=extract_document([o for o in fragments if o.id in doc.region_ids],doc)
                for target,items in ((evidence,ev),(entities,en),(assertions,aa),(relations,rr)):
                    target.update({x.id:x for x in items})
                rejected.extend(errors)
            except Exception as exc:
                rejected.append({'reason':'extraction_error:'+(str(exc) or type(exc).__name__).splitlines()[0][:200],'document_id':doc.id})
        result=CanonicalResult(source_sha256=digest,source_path=source_path,run_id=run.name,logical_documents=documents,
            observations=[asdict(o) for o in all_obs.values()],pages=pages,evidence=list(evidence.values()),entities=list(entities.values()),
            assertions=list(assertions.values()),relations=list(relations.values()),tasks=[],coverage={},issues=issues or [],
            provenance={'rejected_candidates':rejected,'migration':'none'})
        if not self.config.rules_enabled:
            result.assertions=[];result.relations=[]
        reconcile(result)
        if self.config.plan_llm_tasks:result.tasks=plan_tasks(result)
        models=LocalModels(self.config)
        for task in result.tasks:
            if self.config.text_enabled:
                circuit=self.output/'ollama-circuit.json'
                try:
                    with locked(self.output/'.ollama.lock',timeout=deadline-time.monotonic()):
                        if circuit.exists():task.status='blocked';task.reason+=';ollama_circuit_open'
                        else:
                            models.run(task,result,run/'model-checkpoints',deadline)
                            if 'timeout_server_cancellation_unconfirmed' in task.reason:
                                atomic_json(circuit,{'reason':'timeout_server_cancellation_unconfirmed','run_id':result.run_id,
                                    'resume':'Restart/verify local Ollama, then reset-model-circuit for this output directory.'})
                except TimeoutError:task.status='blocked';task.reason+=';model_queue_budget_exhausted'
            else:models.run(task,result,run/'model-checkpoints',deadline)
            atomic_json(run/'tasks'/f'{task.id}.json',task.model_dump(mode='json'))
        # Stable id deduplication before final strict validation.
        result.assertions=list({a.id:a for a in result.assertions}.values())
        reconcile(result)
        result.coverage={f'page:{p["number"]}':p['status'] for p in pages}
        for doc in documents:
            if doc.source_type in {'identity_document','privacy_notice','site_plan','land_registry_record'}:
                result.coverage[doc.id]='not_applicable_to_pilot_extraction'
            elif doc.source_type=='unknown':result.coverage[doc.id]='classification_requires_review'
            else:result.coverage[doc.id]='extracted_needs_review'
        for task in result.tasks:result.coverage[f'{task.logical_document_id}:{task.task_type}']=task.status
        from .tasks import GROUPS
        from .values import document_predicates
        for doc in documents:
            doc_entities={e.id for e in result.entities if e.logical_document_id==doc.id}
            groups=['parts','properties','temporal','financial'] if doc.source_type=='lease_contract' else ['properties'] if doc.source_type=='cadastral_record' else ['financial'] if doc.source_type in {'payment_receipt','payment_correspondence','bank_payment_confirmation'} else []
            for group in groups:
                task=next((t for t in result.tasks if t.logical_document_id==doc.id and t.task_type==group),None)
                for predicate in GROUPS[group]:
                    if predicate not in document_predicates(doc.source_type):
                        result.coverage[f'{doc.id}:field:{predicate}']='not_applicable'
                        continue
                    facts=[a for a in result.assertions if a.subject_id in doc_entities and a.predicate==predicate]
                    state='conflict' if any(a.resolution_status=='conflict' for a in facts) else 'evaluated_needs_review' if facts else 'not_found_by_rules'
                    if not facts and task:state='not_processed' if task.status in {'deferred','planned'} else 'technical_error' if task.status=='technical_error' else 'blocked_by_dependency' if task.status=='blocked' else 'evaluated_unresolved'
                    result.coverage[f'{doc.id}:field:{predicate}']=state
        for rejection in rejected:
            if rejection.get('document_id'):result.coverage[rejection['document_id']]='technical_error'
        result.metrics.update(model_calls=models.calls,model_failures=models.failures,llm_seconds=models.elapsed,
            assertions=len(result.assertions),entities=len(result.entities),planned_tasks=len(result.tasks),
            observations=len(result.observations),pages=len(pages),conflicts=sum(a.resolution_status=='conflict' for a in result.assertions))
        return CanonicalResult.model_validate(result.model_dump())

    def save(self,result,run):
        from .review import write_review
        from .field_map import field_catalog
        from .rules import rule_catalog
        from .models import SYSTEM
        run.mkdir(parents=True,exist_ok=True)
        destination=run/'result.json'
        if destination.exists():raise FileExistsError('immutable_result_already_exists')
        atomic_json(destination,result.model_dump(mode='json'))
        atomic_json(run/'staging-projection.json',staging_projection(result))
        atomic_json(run/'field-map.json',field_catalog())
        atomic_json(run/'schema.json',CanonicalResult.model_json_schema())
        atomic_json(run/'rules-manifest.json',rule_catalog())
        (run/'prompt-system.txt').write_text(SYSTEM,encoding='utf-8')
        write_review(result,run/'review.html')
        index_result(self.output/'index.sqlite3',result)
        return destination

    def replay(self,legacy_path,*,source=None):
        """Read old observations only; no promotion of old candidates or human decisions."""
        path=Path(legacy_path).resolve();old=json.loads(path.read_text())
        if 'pages' not in old:raise ValueError('legacy_observations_required')
        source=Path(source or old.get('document',{}).get('original_path',old.get('source_path','')))
        expected=old.get('document',{}).get('sha256',old.get('source_sha256'))
        if not source.is_file() or digest_file(source)!=expected:raise ValueError('legacy_original_hash_mismatch')
        root=self.output/expected
        with locked(root/'.lock'):
            original=root/'original.pdf'
            if not original.exists():shutil.copyfile(source,original)
            if digest_file(original)!=expected:raise ValueError('immutable_original_hash_mismatch')
            observations,pages=[],[]
            if old.get('observations'):
                observations=[Observation(**{**o,'bbox':tuple(o['bbox']) if o.get('bbox') else None}) for o in old['observations']]
                pages=old['pages']
            else:
                for page in old['pages']:
                    regions=page.get('regions',[])
                    for r in regions:
                        if r.get('text'):
                            observations.append(Observation(r['id'],page['number'],r['text'],tuple(r['bbox']) if r.get('bbox') else None,r.get('method','ocr')))
                    native=[r['id'] for r in regions if r.get('method')=='native' and r.get('text')]
                    selected=native or [r['id'] for r in regions if r.get('text') and r.get('method')=='ocr']
                    pages.append({'number':page['number'],'status':'processed' if selected else 'not_processed','issues':page.get('issues',[]),
                                  'region_ids':[r['id'] for r in regions], 'selected_region_ids':selected})
            run=root/'replay'/('run-'+uuid.uuid4().hex)
            result=self.analyze(observations,pages,expected,str(original),run,time.monotonic()+self.config.max_document_seconds)
            result.provenance.update(runtime());result.provenance.update(migration='observations_only',legacy_result=str(path),
                human_decisions_migration='requires_explicit_reassociation',legacy_decisions=old.get('human_review_events',[]))
            return self.save(result,run)
