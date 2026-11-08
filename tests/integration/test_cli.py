import hashlib
import json
from pathlib import Path

import pytest

from little_pom.cli import main
from little_pom.cli.stages import StageError, StageStore


def inventory(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_real_composed_mock_e2e_and_manifest_resume(tmp_path, capsys):
    root = tmp_path / 'run'
    assert main(['--run-dir', str(root), 'mock-e2e']) == 0
    report = json.loads((root / 'report.json').read_text())
    assert report['mode'] == 'mock'
    assert report['scientific_readiness'] is False
    assert report['full_pom_go_gates_applied'] is False
    assert len(report['coverage']) == 3
    assert len(report['panel_rows']) == 36
    assert sum(row['n_reviewers_observed'] for row in report['panel_rows']) == 72
    assert report['measurement_quality']['human_validated'] is False
    before = inventory(root)
    assert main(['--run-dir', str(root), 'mock-e2e']) == 0
    assert inventory(root) == before
    assert len(list((root / 'manifests').glob('*.json'))) == 10
    capsys.readouterr()


def test_stage_order_missing_manifest_and_tamper(tmp_path):
    stages = StageStore(tmp_path, {'policy': 'frozen'})
    with pytest.raises(StageError, match='no manifest'):
        stages.run('reviews', ('panels',), lambda: {'status': 'ok'})
    # A random artifact is not evidence that a stage completed.
    (tmp_path / 'panels.json').write_text('{}')
    with pytest.raises(StageError, match='no manifest'):
        stages.load('panels')
    stages.run('panels', (), lambda: {'status': 'ok', 'value': 1})
    (tmp_path / 'stages' / 'panels.json').chmod(0o600)
    (tmp_path / 'stages' / 'panels.json').write_text('{"status":"ok","value":2}')
    with pytest.raises(StageError, match='artifact changed'):
        stages.load('panels')


def test_incomplete_stage_does_not_rerun_or_unlock_successor(tmp_path):
    stages = StageStore(tmp_path, {'policy': 'frozen'})
    calls = []
    def fail():
        calls.append(1)
        return {'status': 'incomplete', 'error': {'code': 'ambiguous_remote_execution'}}
    assert stages.run('workers', (), fail)['status'] == 'incomplete'
    assert stages.run('workers', (), fail)['status'] == 'incomplete'
    assert calls == [1]
    with pytest.raises(StageError, match='incomplete'):
        stages.run('cards', ('workers',), lambda: {'status': 'ok'})


def test_changed_frozen_policy_is_not_resume(tmp_path):
    StageStore(tmp_path, {'threshold': .7}).run('freeze', (), lambda: {'status': 'ok'})
    with pytest.raises(StageError, match='configuration changed'):
        StageStore(tmp_path, {'threshold': .6}).load('freeze')


def test_live_and_mock_are_not_accidentally_combined(tmp_path, capsys):
    assert main(['--run-dir', str(tmp_path), '--live', 'mock-e2e']) == 2
    assert 'explicitly live configuration' in capsys.readouterr().err
    assert not list(tmp_path.rglob('*.json'))


def test_review_boundary_rejects_metadata_leak():
    from little_pom.reviews import ReviewerPacketBuilder
    with pytest.raises(ValueError):
        ReviewerPacketBuilder().build({'text': 'Task', 'condition': 'RoutedHistory'}, {'text': ''}, {'text': 'Card'})


def test_failed_episode_is_retained_without_replacement(tmp_path, monkeypatch, capsys):
    from little_pom.cli import mock
    original = mock.model_response
    def response(request, schema_id, execution_id):
        if request.get('task_id') == 'dna-storage-codec' and schema_id == 'WorkerTurn':
            return {'invalid': 'Explicit failure injection'}
        return original(request, schema_id, execution_id)
    monkeypatch.setattr(mock, 'model_response', response)
    assert main(['--run-dir', str(tmp_path), 'mock-e2e']) == 0
    report = json.loads((tmp_path / 'report.json').read_text())
    assert len(report['panel_rows']) == 24
    assert report['missingness']['panels_not_created'] == 12
    assert len(report['missingness']['tasks']['dna-storage-codec']['worker_errors']) == 8
    assert report['coverage']['dna-storage-codec']['private_def']['missing'] is True
    assert 'dna-storage-codec' in report['paired_episode_analysis']['detection']['missing_episode_ids']
    capsys.readouterr()


def test_finalize_missing_measurements_keeps_inconclusive_report(tmp_path, capsys):
    from types import SimpleNamespace
    from little_pom.cli.pipeline import Pipeline
    for stage in ('preflight', 'freeze', 'workers', 'cards-export', 'cards-import', 'panels', 'reviews', 'eval-export'):
        assert main(['--run-dir', str(tmp_path), stage]) == 0
    responses = tmp_path.parent / 'empty-responses.json'
    responses.write_text('{}')
    args = SimpleNamespace(run_dir=tmp_path, config=None, snapshot_root=Path('data/terminal-bench-science'),
        responses=responses, live=False, secret_path=None, inspect_docker=False, phase='mock', d0_task=None,
        readiness=None, card_phase='valid', finalize_incomplete=False, stage='eval-import')
    pipeline = Pipeline(args)
    pipeline.mock = False  # Explicit external subscription input, not synthetic responses.
    with pytest.raises(StageError, match='responses still required'):
        pipeline.eval_import()
    assert not (tmp_path / 'manifests' / 'eval-import.json').exists()
    args.finalize_incomplete = True
    result = pipeline.eval_import()
    assert result['measurement_status'] == 'incomplete'
    assert result['labels']['missingness']
    assert result['labels']['fp_report_ready'] is False
    from little_pom.analysis import Analysis
    policy = {**pipeline.config['analysis_policy'], 'equivalence_map': pipeline.config['equivalence_map']}
    report = Analysis().compute(pipeline.stages.data('reviews')['outcomes'], result['labels'],
                                pipeline.stages.data('panels')['mechanisms'], policy)
    assert report['pilot_status'] == 'measurement_inconclusive'
    assert report['metrics']['RoutedHistory']['complete']['detection']['denominator'] == 0
    capsys.readouterr()
