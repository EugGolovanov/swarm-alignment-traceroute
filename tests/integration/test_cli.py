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


def routing_pipeline(tmp_path):
    from types import SimpleNamespace
    from little_pom.cli.pipeline import Pipeline
    args = SimpleNamespace(run_dir=tmp_path, config=None,
        snapshot_root=Path('data/terminal-bench-science'), live=False,
        secret_path=None, readiness=None, stage='workers')
    return Pipeline(args)


def test_mock_routing_never_opens_subscription_or_adaption(tmp_path, monkeypatch):
    from little_pom import runtime
    def forbidden(**kwargs):
        raise AssertionError('Mock routing cannot initialize a live transport')
    monkeypatch.setattr(runtime, 'OmpSubscriptionBackend', forbidden)
    monkeypatch.setattr(runtime, 'AdaptionBackend', forbidden)
    pipeline = routing_pipeline(tmp_path)
    for stage in ('workers', 'author', 'reviews'):
        assert isinstance(pipeline._client(stage).backend, runtime.MockBackend)


def test_live_routing_uses_subscription_workers_and_separate_adaption_roles(tmp_path, monkeypatch):
    from little_pom import runtime
    pipeline = routing_pipeline(tmp_path)
    pipeline.mock = False
    pipeline.stages.run('preflight', (), lambda: {'status': 'ok', 'live_ready': True})
    calls = []
    worker_backend, swarm_backend = object(), object()
    def subscription(**kwargs):
        calls.append(('subscription', kwargs))
        return worker_backend
    def adaption(**kwargs):
        calls.append(('adaption', kwargs))
        return swarm_backend
    monkeypatch.setattr(runtime, 'OmpSubscriptionBackend', subscription)
    monkeypatch.setattr(runtime, 'AdaptionBackend', adaption)
    assert pipeline._client('workers').backend is worker_backend
    for stage in ('author', 'reviews'):
        with pytest.raises(StageError, match='author/reviewer'):
            pipeline._client(stage)
    assert len(calls) == 1
    pipeline.args.secret_path = tmp_path / 'unused-secret'
    for stage in ('author', 'reviews'):
        assert pipeline._client(stage).backend is swarm_backend
    assert all(kind == 'adaption' for kind, kwargs in calls[1:])
    assert all(kwargs['secret_path'] == pipeline.args.secret_path for kind, kwargs in calls[1:])


def test_live_terra_swarm_routes_connected_subscription_without_secret(tmp_path, monkeypatch):
    from little_pom import runtime
    pipeline = routing_pipeline(tmp_path)
    pipeline.mock = False
    pipeline.config['models']['swarm'].update(
        model='openai-codex/gpt-6.1-terra', transport='omp_subscription', mode='subscription',
        parameters={'reasoning_effort': 'medium'})
    pipeline.swarm_model = pipeline.config['models']['swarm']['model']
    pipeline.stages.run('preflight', (), lambda: {'status': 'ok', 'live_ready': True})
    calls = []
    backend = object()
    def subscription(**kwargs):
        calls.append(kwargs)
        return backend
    def forbidden(**kwargs):
        raise AssertionError('Terra cannot fall back to paid Adaption')
    monkeypatch.setattr(runtime, 'OmpSubscriptionBackend', subscription)
    monkeypatch.setattr(runtime, 'AdaptionBackend', forbidden)
    assert pipeline.args.secret_path is None
    for stage in ('author', 'reviews'):
        assert pipeline._client(stage).backend is backend
    assert calls == [{'model': pipeline.swarm_model, 'timeout': pipeline.config['budgets']['timeout_seconds']}] * 2


def test_subscription_routing_cannot_bypass_scientific_readiness(tmp_path, monkeypatch):
    from little_pom import runtime
    pipeline = routing_pipeline(tmp_path)
    pipeline.mock = False
    pipeline.stages.run('preflight', (), lambda: {'status': 'ok', 'live_ready': False})
    def forbidden(**kwargs):
        raise AssertionError('No transport may initialize before scientific readiness')
    monkeypatch.setattr(runtime, 'OmpSubscriptionBackend', forbidden)
    monkeypatch.setattr(runtime, 'AdaptionBackend', forbidden)
    for stage in ('workers', 'author', 'reviews'):
        with pytest.raises(StageError, match='scientific preflight'):
            pipeline._client(stage)


def test_worker_batch_budget_preflight_precedes_client_creation(tmp_path, monkeypatch):
    from little_pom.cli.pipeline import Pipeline
    from little_pom.contracts import default_config_path, load_config, sha256_bytes

    pipeline = object.__new__(Pipeline)
    pipeline.root = tmp_path
    pipeline.config = load_config(default_config_path())
    pipeline.config["budgets"]["author_input_bytes"] = 1
    pipeline.swarm_model = pipeline.config["models"]["swarm"]["model"]
    pipeline.model_config = pipeline.config["models"]["swarm"]["parameters"]
    task = {"schema_version": "1.0", "id": "task", "task_id": "task", "goal": "Inspect.",
            "allowed_inputs": [], "constraints": [], "snapshot_hash": sha256_bytes(b"input")}
    monkeypatch.setattr(pipeline, "_tasks", lambda: {"task": task})
    def unexpected_client(stage):
        pytest.fail("Worker client must not be created before budget planning")
    monkeypatch.setattr(pipeline, "_client", unexpected_client)
    with pytest.raises(StageError, match="budget planning rejected"):
        pipeline.workers()
    reports = [json.loads(path.read_text()) for path in (tmp_path / "author" / "preflight").glob("*.json")]
    assert len(reports) == 1
    assert reports[0]["kind"] == "author_budget_plan"
    assert not reports[0]["accepted"]


def test_cli_preflight_exposes_allocation_risk_without_changing_defaults(tmp_path, capsys):
    assert main(["--run-dir", str(tmp_path), "preflight"]) == 0
    result = json.loads((tmp_path / "stages" / "preflight.json").read_text())
    for plan in result["author_budget_plans"].values():
        assert plan["input_budget_bytes"] == 32768
        assert plan["allocation_risk"]
        assert plan["exact_request_bytes"] is None
        assert (tmp_path / plan["report_path"]).is_file()
    capsys.readouterr()
