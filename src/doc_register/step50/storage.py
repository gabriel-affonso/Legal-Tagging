"""Atomic local artifacts and document-scoped locks (macOS/Linux)."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def digest_file(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def runtime() -> dict:
    from .. import __version__
    root = Path(__file__).resolve().parents[1]
    code = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        code.update(str(path.relative_to(root)).encode())
        code.update(path.read_bytes())
    try:
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {'version': __version__, 'package_path': str(root), 'code_sha256': code.hexdigest(),
            'commit': commit, 'python': platform.python_version(), 'platform': platform.platform(),
            'dependencies': {p: importlib.metadata.version(p) for p in ['PyMuPDF', 'pypdf', 'openpyxl']}}
