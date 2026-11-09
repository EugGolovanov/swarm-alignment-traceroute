from little_pom.cli import main
from little_pom.cli import model_smoke as module
from little_pom.runtime import MockBackend


def test_smoke_requires_explicit_live_and_key_path(tmp_path, capsys):
    assert main(['--run-dir', str(tmp_path), 'model-smoke']) == 2
    assert 'requires --live' in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_smoke_has_durable_single_sample_resume(tmp_path, monkeypatch, capsys):
    backend = MockBackend(responses=[{
        'status': 'no_claim', 'claim': '', 'evidence_ids': [],
        'decision_relevance_text': 'Synthetic transport test only.'}])
    monkeypatch.setattr(module, 'AdaptionBackend', lambda path: backend)
    args = ['--run-dir', str(tmp_path), '--live', '--secret-path', 'unused', 'model-smoke']
    assert main(args) == 0
    assert main(args) == 0
    capsys.readouterr()
    assert len(backend.calls) == 1
    assert backend.calls[0]['request']['output_budget'] == 1024


def test_smoke_does_not_retry_ambiguous_execution(tmp_path, monkeypatch, capsys):
    backend = MockBackend(scenario='timeout')
    monkeypatch.setattr(module, 'AdaptionBackend', lambda path: backend)
    args = ['--run-dir', str(tmp_path), '--live', '--secret-path', 'unused', 'model-smoke']
    assert main(args) == 2
    assert main(args) == 2
    capsys.readouterr()
    assert len(backend.calls) == 1
