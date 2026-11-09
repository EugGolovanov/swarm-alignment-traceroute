"""Operator-approved exploratory runtime amendment; source requirements stay intact.

Only nanoindentation is reduced from 8192 to 7168 MiB. The 512 MiB VM
headroom and no-swap container policy remain. This source is included in freezes.
"""
import hashlib
from pathlib import Path

from little_pom.tasks import RESOURCE_REQUIREMENTS

HEADROOM_MB = 512
MEMORY_AMENDMENTS = {
    'nanoindentation-property-extraction': {
        'memory_mb': 7168,
        'reason': 'operator_requested_lower_ram_for_second_task_exploratory_run',
    },
}


def runtime_requirements(task_id):
    requirements = dict(RESOURCE_REQUIREMENTS[task_id])
    amendment = MEMORY_AMENDMENTS.get(task_id)
    if amendment:
        requirements['memory_mb'] = amendment['memory_mb']
    return requirements


def sci_overlay_hash():
    """Pin the owned SCI isolation overlay in addition to the upstream snapshot."""
    return hashlib.sha256(Path(__file__).with_name('Dockerfile.sci').read_bytes()).hexdigest()
