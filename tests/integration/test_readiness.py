import copy
import hashlib
import json

from little_pom.cli import main
from little_pom.cli.readiness import evaluate_readiness
from little_pom.contracts import sha256_hash
from little_pom.contracts.config import default_config_path, load_config


def readiness_fixture(tmp_path):
    config = {'snapshot': {'sha256': 'a' * 64}, 'models': {
        'swarm': {'model': 'qwen3.8-27b', 'mode': 'provider_default'},
        'astra': {'model': 'gpt6-astra', 'mode': 'subscription-mode'},
        'terra': {'model': 'Terra', 'mode': 'subscription-mode'}}}
    evidence = tmp_path / 'evidence.json'
    evidence.write_text('{"explicit_offline_test_fixture":true}')
    record = {'passed': True, 'path': evidence.name, 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}
    attestation = {'config_hash': sha256_hash(config), 'snapshot_sha256': config['snapshot']['sha256'],
                   'models': copy.deepcopy(config['models']), 'checks': {key: dict(record) for key in
                   ['model_access', 'quota_and_budget', 'structured_output', 'subscription_context_isolation', 'task:runtime', 'task:oracle']}}
    environment = {'docker': {'available': True, 'inspected': True}, 'tasks': [
        {'task_id': 'task', 'ram_sufficient': True, 'cpu_sufficient': True,
         'image': {'state': 'present', 'provenance_matches': True}, 'blockers': []}]}
    return config, environment, attestation


def test_readiness_requires_explicit_intact_evidence_and_resources(tmp_path):
    config, environment, attestation = readiness_fixture(tmp_path)
    assert not evaluate_readiness(config, environment, None, tmp_path, ['task'])['ready']
    assert evaluate_readiness(config, environment, attestation, tmp_path, ['task'])['ready']
    # D0 completion is deliberately NOT a model/runtime prerequisite: no circular gate.
    assert 'd0' not in attestation
    environment['tasks'][0]['ram_sufficient'] = False
    assert not evaluate_readiness(config, environment, attestation, tmp_path, ['task'])['ready']
    environment['tasks'][0]['ram_sufficient'] = True
    (tmp_path / 'evidence.json').write_text('changed')
    assert not evaluate_readiness(config, environment, attestation, tmp_path, ['task'])['ready']


def test_d0_fix_final_manifests_are_distinct_and_do_not_execute_tasks(tmp_path, capsys):
    config = load_config(default_config_path())
    config['mode'] = 'live'
    config['d0_task_id'] = 'explicit-separate-development-task'
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    run = tmp_path / 'run'
    base = ['--run-dir', str(run), '--config', str(path), '--live']
    assert main(base + ['--phase', 'd0', '--d0-task', config['d0_task_id'], 'freeze']) == 0
    assert main(base + ['--phase', 'd0', 'workers']) == 2
    assert 'task data and adapter' in capsys.readouterr().err
    evidence = tmp_path / 'external-trace.json'
    evidence.write_text('{"test_fixture_only":true}')
    result = {'status': 'ok', 'data': {'task_id': config['d0_task_id'],
              'evidence_artifacts': {evidence.name: hashlib.sha256(evidence.read_bytes()).hexdigest()}}, 'error': None}
    result_path = tmp_path / 'result.json'
    result_path.write_text(json.dumps(result))
    assert main(base + ['--phase', 'fix', '--responses', str(result_path), 'freeze']) == 0
    assert main(base + ['preflight']) == 0
    assert main(base + ['--phase', 'final', 'freeze']) == 0
    assert all((run / 'manifests' / (phase + '.json')).is_file()
               for phase in ('freeze-d0', 'freeze-fix', 'freeze-final'))
    assert not list(run.glob('workers/*'))
    assert json.loads((run / 'final_freeze_manifest.json').read_text())['consumed_task_ids'] == [config['d0_task_id']]
    from little_pom.cards.fixtures import subscription_response
    frozen = json.loads((run / 'stages' / 'freeze-final.json').read_text())
    responses = {packet['id']: subscription_response(packet) for packet in frozen['applicability_packets']}
    responses_path = tmp_path / 'applicability.json'
    responses_path.write_text(json.dumps(responses))
    command = base + ['--card-phase', 'applicability', '--responses', str(responses_path), 'cards-import']
    assert main(command) == 0
    assert main(command) == 0
    first = next(iter(responses))
    responses[first]['trace'] = 'Changed accepted response'
    responses_path.write_text(json.dumps(responses))
    assert main(command) == 2
    assert 'Frozen applicability response changed' in capsys.readouterr().err
    capsys.readouterr()


def test_d0_never_selects_a_pilot_task(tmp_path, capsys):
    assert main(['--run-dir', str(tmp_path / 'run'), '--phase', 'd0', 'freeze']) == 2
    assert 'separate D0 task' in capsys.readouterr().err
    assert not (tmp_path / 'run' / 'd0_freeze_manifest.json').exists()
