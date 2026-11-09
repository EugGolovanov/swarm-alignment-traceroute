import pytest

from little_pom.sandbox import _policy
from little_pom.sandbox.resources import runtime_requirements
from little_pom.tasks import RESOURCE_REQUIREMENTS


def test_only_nano_has_explicit_runtime_reduction():
    for task_id, declared in RESOURCE_REQUIREMENTS.items():
        effective = runtime_requirements(task_id)
        assert effective['memory_mb'] == (7168 if task_id == 'nanoindentation-property-extraction' else declared['memory_mb'])
        assert effective['cpus'] == declared['cpus']
    assert RESOURCE_REQUIREMENTS['nanoindentation-property-extraction']['memory_mb'] == 8192


def test_nano_policy_enforces_amended_limit_without_mutating_source():
    task_id = 'nanoindentation-property-extraction'
    task = {'task_id': task_id, 'resource_requirements': dict(RESOURCE_REQUIREMENTS[task_id])}
    assert _policy(task, {})['memory_mb'] == 7168
    with pytest.raises(ValueError, match='below task requirements'):
        _policy(task, {'memory_mb': 7167})
    assert _policy(task, {'memory_mb': 8192})['memory_mb'] == 8192
    assert task['resource_requirements']['memory_mb'] == 8192
