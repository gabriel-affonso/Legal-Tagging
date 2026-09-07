"""Incremental native/OCR inventory, preserving missing and failed pages."""
from dataclasses import asdict
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import pymupdf as fitz
from .document_map import Observation
from .canonical import stable_id
from .storage import atomic_json
from ..step50.recognition import render_crop


def ocr(image,*,language,timeout):
    executable=shutil.which('tesseract')
    if not executable:raise RuntimeError('tesseract_unavailable')
    with tempfile.TemporaryDirectory(prefix='step55-ocr-') as temp:
        source=Path(temp)/'crop.png'; source.write_bytes(image)
        completed=subprocess.run([executable,str(source),'stdout','-l',language,'--psm','3','tsv'],capture_output=True,timeout=timeout,check=False)
        if completed.returncode:raise RuntimeError('tesseract_failed:'+completed.stderr.decode(errors='replace')[:150])
        rows=list(csv.DictReader(io.StringIO(completed.stdout.decode('utf-8')),delimiter='\t'))
        text,words,last_line='',[],None
        for row in rows:
            token=row.get('text','').strip()
            if not token:continue
            line=tuple(row.get(k) for k in ('page_num','block_num','par_num','line_num'))
            if text:text+='\n' if line != last_line else ' '
            start=len(text); text+=token
            words.append({'start':start,'end':len(text),'bbox_pixels':[int(row[k]) for k in ('left','top','width','height')], 'engine_confidence':float(row['conf'])})
            last_line=line
        return {'text':text,'words':words}


def inventory(source,config,cache,deadline,ocr_reader=ocr,source_hash=''):
    observations,pages,issues=[],[],[]
    with fitz.open(source) as pdf:
        if pdf.needs_pass:raise ValueError('protected_document')
        if not len(pdf):raise ValueError('empty_document')
        for index in range(len(pdf)):
            number=index+1
            record={'number':number,'status':'not_processed','issues':[],'region_ids':[],'selected_region_ids':[]}
            if number>config.max_pages or time.monotonic()>=deadline:
                record['issues']=['page_budget_exhausted']; pages.append(record); continue
            checkpoint=cache/f'{number}.json'
            if checkpoint.exists():
                previous=json.loads(checkpoint.read_text())
                if previous['page']['status']=='processed':
                    pages.append(previous['page'])
                    issues.extend(f'page_{number}:{issue}' for issue in previous['page']['issues'])
                    observations.extend(Observation(**{**o,'bbox':tuple(o['bbox']) if o['bbox'] else None}) for o in previous['observations'])
                    continue
            page=pdf[index]; rotation=page.rotation; page.set_rotation(0)
            record.update(width=page.rect.width,height=page.rect.height,rotation=rotation)
            page_observations=[]
            try:
                blocks=[b for b in page.get_text('blocks',sort=True) if b[6]==0 and b[4].strip()]
                native_chars=sum(len(b[4].strip()) for b in blocks)
                for b in blocks:
                    rid='region-'+stable_id(source_hash,number,'native',b[:4],b[4])
                    page_observations.append(Observation(rid,number,b[4],tuple(map(float,b[:4])),'native'))
                    if native_chars>=config.native_min_chars:record['selected_region_ids'].append(rid)
                needs_ocr=native_chars<config.native_min_chars or bool(page.get_images())
                if needs_ocr and config.ocr_enabled:
                    image,recipe=render_crop(page,list(page.rect),dpi=config.ocr_dpi,max_pixels=config.ocr_max_pixels)
                    crop=cache/'crops'/f'{number}.png'; crop.parent.mkdir(parents=True,exist_ok=True); crop.write_bytes(image)
                    timeout=min(config.ocr_timeout,max(.1,deadline-time.monotonic()))
                    if time.monotonic()>=deadline:raise TimeoutError('recognition_budget_exhausted')
                    reading=ocr_reader(image,language=config.ocr_language,timeout=timeout)
                    reading={'text':reading,'words':[]} if isinstance(reading,str) else reading
                    if reading['text'].strip():
                        rid='region-'+stable_id(source_hash,number,'ocr',recipe['sha256'],reading['text'])
                        page_observations.append(Observation(rid,number,reading['text'],tuple(map(float,page.rect)),'ocr'))
                        record['ocr']={**recipe,'words':reading['words'],'crop_path':str(crop.resolve())}
                        if native_chars<config.native_min_chars:record['selected_region_ids']=[rid]
                        else:record['issues'].append('hybrid_ocr_requires_comparison')
                    else:record['issues'].append('ocr_empty')
                elif needs_ocr:record['issues'].append('ocr_disabled')
                record['status']='processed' if record['selected_region_ids'] else 'low_text'
            except Exception as exc:
                record['issues'].append('ocr_failure:'+str(exc)[:160])
                record['status']='partial' if record['selected_region_ids'] else 'technical_error'
            finally:page.set_rotation(rotation)
            record['region_ids']=[o.id for o in page_observations]
            observations.extend(page_observations); pages.append(record)
            issues.extend(f'page_{number}:{issue}' for issue in record['issues'])
            atomic_json(checkpoint,{'page':record,'observations':[asdict(o) for o in page_observations]})
    return observations,pages,issues
