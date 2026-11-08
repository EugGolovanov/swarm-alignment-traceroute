"""Explicit external D0 evidence import; this never executes or selects a task."""
from pathlib import Path
import hashlib

from little_pom.contracts import validate


def validate_d0_result(result, task_id, evidence_root):
    validate('ServiceResult', result)
    if result['status'] != 'ok' or not isinstance(result['data'], dict):
        raise ValueError('A completed external D0 result is required')
    data = result['data']
    if data.get('task_id') != task_id:
        raise ValueError('External D0 result must name the explicitly designated separate task')
    artifacts = data.get('evidence_artifacts')
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError('External D0 result requires nonempty hashed evidence_artifacts')
    root = Path(evidence_root).resolve()
    for relative, digest in artifacts.items():
        candidate = root / relative
        path = candidate.resolve()
        if not path.is_relative_to(root) or candidate.is_symlink() or not path.is_file():
            raise ValueError('D0 evidence must be a regular file within the explicit evidence root')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('External D0 evidence digest mismatch')
    return result
