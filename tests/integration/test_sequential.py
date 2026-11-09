import hashlib
import json

import pytest

from little_pom.cli import main
from little_pom.cli.pipeline import Pipeline
from little_pom.cli.sequential import SequentialPipeline
from little_pom.contracts import ConfigFreeze, ContractError, default_config_path, load_config


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_exploratory_freeze_never_claims_d0_and_cannot_change_order(tmp_path):
    config = load_config(default_config_path())
    store = ConfigFreeze(tmp_path)
    manifest = store.freeze_exploratory(config)
    assert manifest['d0_completed'] is False
    assert manifest['task_order'] == config['task_ids']
    assert manifest['measurement_sampling'] == 'global_after_all_reviews'
    assert not (tmp_path / 'd0_result.json').exists()
    assert store.verify('exploratory', config)
    config['task_ids'].reverse()
    with pytest.raises(ContractError):
        store.freeze_exploratory(config)


def test_sequential_mock_full_run_and_immutable_resume(tmp_path, capsys):
    run = tmp_path / 'run'
    args = ['--run-dir', str(run), '--phase', 'exploratory', 'sequential-run']
    assert main(args) == 0
    capsys.readouterr()
    config = load_config(default_config_path())
    for previous, current in zip(config['task_ids'], config['task_ids'][1:]):
        manifest = json.loads((run / 'manifests' / f'{current}--workers.json').read_text())
        assert f'{previous}--reviews' in manifest['dependencies']
    report = json.loads((run / 'report.json').read_text())
    assert report['protocol_phase'] == 'exploratory'
    assert report['task_order'] == config['task_ids']
    assert report['d0_completed'] is False
    assert not report['independent_confirmation']
    sample = json.loads((run / 'measurement/private/manifest.json').read_text())
    assert len(sample['packet_ids']['calibration_a']) == 36
    assert len(sample['packet_ids']['primary']) == 72
    before = inventory(run)
    assert main(args) == 0
    capsys.readouterr()
    assert inventory(run) == before


def test_cannot_skip_to_second_task_or_measure_early(tmp_path, capsys):
    base = ['--run-dir', str(tmp_path / 'run'), '--phase', 'exploratory']
    assert main(base + ['preflight']) == 0
    assert main(base + ['freeze']) == 0
    assert main(base + ['--task', 'nanoindentation-property-extraction', 'workers']) == 2
    assert 'dna-storage-codec--reviews' in capsys.readouterr().err
    assert main(base + ['eval-export']) == 2
    assert not (tmp_path / 'run' / 'measurement').exists()
    assert main(base + ['workers']) == 2


def test_live_sequence_pauses_for_real_json_before_any_model_call(tmp_path, monkeypatch, capsys):
    config = load_config(default_config_path())
    config['mode'] = 'live'
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    original = Pipeline.preflight
    def test_preflight(self):
        # Explicit local fixture: no claims about scientific readiness in production.
        report = original(self)
        report['live_ready'] = True
        return report
    monkeypatch.setattr(Pipeline, 'preflight', test_preflight)
    def forbidden_client(self, stage):
        raise AssertionError('No model call before actual applicability import')
    monkeypatch.setattr(SequentialPipeline, '_client', forbidden_client)
    run = tmp_path / 'run'
    base = ['--run-dir', str(run), '--config', str(path), '--live', '--phase', 'exploratory']
    assert main(base + ['sequential-run']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['error']['code'] == 'sequence_paused'
    assert result['active_task'] is None  # all applicability decisions precede any workers
    assert len(result['exchange']['pending']) == 3
    packet = result['exchange']['pending'][0]
    template = json.loads(open(packet['response_template_path']).read())
    assert template[packet['packet_id']]['trace'] is None
    assert set(template[packet['packet_id']]['applicability'].values()) == {None}
    assert not (run / 'workers').exists()
    assert not (run / 'd0_result.json').exists()
    assert main(base + ['subscription-export']) == 0


def test_live_exchange_resumes_all_tasks_without_resampling(tmp_path, monkeypatch, capsys):
    from little_pom import sandbox as sandbox_module
    from little_pom.cards.fixtures import subscription_response as card_response
    from little_pom.cli.mock import model_response, subscription_response as evaluation_response, SYNTHETIC_INPUT
    from little_pom.runtime import Ledger, MockBackend, SwarmClient
    config = load_config(default_config_path())
    config['mode'] = 'live'
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    original_preflight, original_sandbox = Pipeline.preflight, sandbox_module.Sandbox
    def test_preflight(self):
        report = original_preflight(self)
        report['live_ready'] = True
        return report
    monkeypatch.setattr(Pipeline, 'preflight', test_preflight)
    monkeypatch.setattr(sandbox_module, 'Sandbox', lambda **kw: original_sandbox(
        backend='fake', fake_inputs={'input.txt': SYNTHETIC_INPUT}))
    calls = []
    def test_client(self, stage):
        backend = MockBackend(responses=model_response)
        calls.append(backend)
        return SwarmClient(Ledger(self.root), backend)
    monkeypatch.setattr(SequentialPipeline, '_client', test_client)
    run = tmp_path / 'run'
    responses_path = tmp_path / 'responses.json'
    responses = {}
    base = ['--run-dir', str(run), '--config', str(path), '--live', '--phase', 'exploratory',
            '--responses', str(responses_path), 'sequential-run']
    for _ in range(12):
        responses_path.write_text(json.dumps(responses))
        code = main(base)
        captured = capsys.readouterr()
        assert not captured.err, captured.err
        result = json.loads(captured.out)
        if code == 0:
            break
        assert result['error']['code'] == 'sequence_paused'
        assert result['exchange']['pending']
        for item in result['exchange']['pending']:
            packet = json.loads(open(item['request_path']).read())
            responses[item['packet_id']] = (card_response(packet) if 'stage' in packet else evaluation_response(packet))
    else:
        pytest.fail('Sequence did not finish after all explicit response bundles')
    assert len(result['report']['missingness']['tasks']) == 3
    # Resume checks bind the original subscription output, not its canonicalized citations.
    from little_pom.contracts import sha256_hash
    for valid_path in (run / 'cards').glob('*/valid.json'):
        accepted = json.loads(valid_path.read_text())
        supplied = responses[accepted['packet_id']]
        assert supplied['card']['evidence_ids']
        assert accepted['response']['card']['evidence_ids'] != supplied['card']['evidence_ids']
        assert accepted['raw_response_sha256'] == sha256_hash(supplied)
    first = json.loads((run / 'manifests/dna-storage-codec--workers.json').read_text())
    assert all(f'{task_id}--applicability-import' in first['dependencies'] for task_id in config['task_ids'])
    count = sum(len(backend.calls) for backend in calls)
    before = inventory(run)
    assert main(base) == 0
    capsys.readouterr()
    assert sum(len(backend.calls) for backend in calls) == count
    assert inventory(run) == before


def test_unready_live_run_cannot_freeze_exploratory(tmp_path, capsys):
    config = load_config(default_config_path())
    config['mode'] = 'live'
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    run = tmp_path / 'run'
    assert main(['--run-dir', str(run), '--config', str(path), '--live',
                 '--phase', 'exploratory', 'sequential-run']) == 2
    assert 'readiness must pass' in capsys.readouterr().err
    assert not (run / 'exploratory_freeze_manifest.json').exists()
