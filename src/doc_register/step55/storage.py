"""Immutable runs, cross-platform locking and an expendable SQLite index."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import time
from ..step50.storage import atomic_json, digest_file


@contextmanager
def locked(path,timeout=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        deadline=None if timeout is None else time.monotonic()+max(0,timeout)
        def wait():
            if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('lock_budget_exhausted')
            time.sleep(.05)
        if os.name == 'nt':
            import msvcrt
            handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
            while True:
                try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1); break
                except OSError: wait()
        else:
            import fcntl
            while True:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:wait()
        try:yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def runtime():
    code=hashlib.sha256()
    root=Path(__file__).resolve().parents[1]
    for path in sorted(root.rglob('*.py')):
        code.update(str(path.relative_to(root)).encode()); code.update(path.read_bytes())
    memory=None
    try:memory=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')
    except (ValueError,OSError,AttributeError):pass
    return {'code_sha256':code.hexdigest(),'package_path':str(root),'platform':platform.platform(),
            'processor':platform.processor(),'cpu_count':os.cpu_count(),'memory_total_bytes':memory,
            'python':platform.python_version(), 'dependencies':{name:importlib.metadata.version(name) for name in ['pydantic','PyMuPDF','openpyxl']}}


def index_result(path,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, source_hash TEXT, result_json TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS assertions (run_id TEXT, id TEXT, subject TEXT, predicate TEXT, payload TEXT, PRIMARY KEY(run_id,id))')
        db.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?)',(result.run_id,result.source_sha256,result.model_dump_json()))
        db.executemany('INSERT OR IGNORE INTO assertions VALUES (?,?,?,?,?)',[(result.run_id,a.id,a.subject_id,a.predicate,a.model_dump_json()) for a in result.assertions])
