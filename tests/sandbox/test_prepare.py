import json
from types import SimpleNamespace

import pytest

from little_pom.sandbox.prepare import inspect_oracle, prepare


def trial(tmp_path):
    task = tmp_path / 'task'
    result = tmp_path / 'job' / 'trial' / 'result.json'
    result.parent.mkdir(parents=True)
    body = {'config': {'task': {'path': str(task)}, 'agent': {'name': 'oracle'}},
            'exception_info': None, 'finished_at': '2026-11-08T00:00:00Z',
            'verifier_result': {'rewards': {'reward': 1.0}}}
    result.write_text(json.dumps(body))
    return task, result, body


def test_oracle_requires_actual_trial_reward_not_process_exit(tmp_path):
    task, path, body = trial(tmp_path)
    observed = inspect_oracle(path.parent.parent, task)
    assert observed['verified']
    assert len(observed['trial_result_sha256']) == 64
    assert 'overlay' in observed['scope']
    body['verifier_result']['rewards']['reward'] = 0.0
    path.write_text(json.dumps(body))
    assert not inspect_oracle(path.parent.parent, task)['verified']


@pytest.mark.parametrize('mutation', [
    lambda b: b.update(exception_info={'exception_type': 'Failed'}),
    lambda b: b['config']['agent'].update(name='nop'),
    lambda b: b['config']['task'].update(path='/different-task'),
    lambda b: b.update(finished_at=None),
    lambda b: b['verifier_result']['rewards'].update(reward=True),
])
def test_oracle_rejects_failed_or_mismatched_evidence(tmp_path, mutation):
    task, path, body = trial(tmp_path)
    mutation(body)
    path.write_text(json.dumps(body))
    assert not inspect_oracle(path.parent.parent, task)['verified']


def test_oracle_requires_exactly_one_trial(tmp_path):
    task, path, body = trial(tmp_path)
    other = path.parent.parent / 'second' / 'result.json'
    other.parent.mkdir()
    other.write_text(json.dumps(body))
    assert not inspect_oracle(path.parent.parent, task)['verified']


def test_preparation_cannot_overwrite_previous_observation(tmp_path):
    (tmp_path / 'preparation.json').write_text('{}')
    with pytest.raises(ValueError, match='already exists'):
        prepare(SimpleNamespace(output=tmp_path))
