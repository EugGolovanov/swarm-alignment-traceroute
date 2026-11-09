"""Offline subscription boundary tests; never authenticate or dispatch to a provider."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from little_pom.runtime import AdaptionBackend, Ledger, MockBackend, OmpSubscriptionBackend, SwarmClient
from little_pom.runtime import subscription


MODEL = {'id': 'gpt-6-luna', 'provider': 'openai-codex', 'api': 'openai-codex-responses',
         'baseUrl': 'https://chatgpt.com/backend-api', 'maxTokens': 128000,
         'thinkingLevelMap': {'medium': 'medium'}}


def request():
    return {'model': subscription.LUNA_MODEL, 'config': {'reasoning_effort': 'medium'},
            'instructions': 'Return a WorkerTurn JSON object.', 'packet': {'task_id': 'one'},
            'history': [], 'output_budget': 256, 'task_id': 'one', 'stage': 'workers'}


@pytest.fixture
def installed(tmp_path, monkeypatch):
    agent = tmp_path / '.pi/agent'
    release = agent / 'install/releases/1.5.0'
    ai = release / 'node_modules/@earendil-works/pi-ai'
    coding_agent = release / 'node_modules/@earendil-works/pi-coding-agent'
    (ai / 'dist/providers/data').mkdir(parents=True)
    coding_agent.mkdir(parents=True)
    for package in (ai, coding_agent):
        (package / 'package.json').write_text(json.dumps({'version': '1.5.0'}))
    (agent / 'install/current-version').write_text('1.5.0\n')
    (ai / 'dist/providers/data/openai-codex.json').write_text(json.dumps({
        'openai-codex-responses': {
            f'chat:{selector.split("/", 1)[1]}': MODEL | {'id': selector.split("/", 1)[1]}
            for selector in subscription.SUPPORTED_MODELS}}))
    node = tmp_path / 'node'
    node.write_text('synthetic executable fingerprint')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(subscription.shutil, 'which', lambda name: str(node))
    return agent, ai, node


def test_explicit_model_selection_rejects_cross_model_dispatch(installed, monkeypatch, tmp_path):
    monkeypatch.setattr(subscription.subprocess, 'run', lambda *_a, **_k: pytest.fail('must not dispatch'))
    backend = OmpSubscriptionBackend(model='openai-codex/gpt-6.1-terra')
    assert backend.configuration()['catalog_model']['id'] == 'gpt-6.1-terra'
    client = SwarmClient(Ledger(tmp_path / 'ledger'), backend)
    assert client.call(request(), 'WorkerTurn', 'wrong-model')['error']['code'] == 'invalid_request'
    with pytest.raises(ValueError, match='Unsupported explicit'):
        OmpSubscriptionBackend(model='openai/gpt-6.1-terra')


def envelope(**overrides):
    return {'status': 'completed', 'output_text': '{"result":"complete"}', 'usage': None,
            'provider_events': [{'type': 'response.completed', 'response': {'status': 'completed'}}],
            '_transport_metadata': {'bridge_protocol': subscription.BRIDGE_PROTOCOL}, **overrides}


def test_constructor_freezes_local_runtime_without_auth_or_process(installed, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('construction must not execute a bridge or read credentials')
    monkeypatch.setattr(subscription.subprocess, 'run', forbidden)
    backend = OmpSubscriptionBackend(timeout=10)
    config = backend.configuration()
    assert config['backend'] == 'omp_subscription'
    assert config['mode'] == 'subscription'
    assert config['model'] == subscription.LUNA_MODEL
    assert config['parameters'] == {'reasoning_effort': 'medium'}
    assert config['max_retries'] == 0 and config['transport'] == 'sse'
    assert config['generation_dispatch_limit'] == 1
    assert config['output_limit_field'] == 'max_output_tokens'
    assert config['sdk_version'] == '1.5.0'
    assert not (installed[0] / 'auth.json').exists()
    config['catalog_model']['id'] = 'tampered'
    assert backend.configuration()['catalog_model']['id'] == 'gpt-6-luna'


def test_subprocess_isolated_single_send_preserves_observations(installed, monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit')
    monkeypatch.setenv('NODE_OPTIONS', '--require=ambient-tool-loader')
    calls = []
    raw = envelope(usage={'output_tokens': 29}, pi_message={'content': [{'type': 'text', 'text': '{}'}]})
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        payload = json.loads(kwargs['input'])
        assert payload['request'] == request()
        assert payload['timeout_ms'] == 10000
        assert payload['auth_file'] == str(installed[0] / 'auth.json')
        assert not list(Path(kwargs['cwd']).iterdir())
        assert 'OPENAI_API_KEY' not in kwargs['env'] and 'NODE_OPTIONS' not in kwargs['env']
        assert kwargs['timeout'] == 40
        return SimpleNamespace(returncode=0, stdout=json.dumps(raw).encode(), stderr=b'secret never relayed')
    monkeypatch.setattr(subscription.subprocess, 'run', run)
    backend = OmpSubscriptionBackend(timeout=10)
    ledger = Ledger(tmp_path / 'ledger')
    client = SwarmClient(ledger, backend, validator=lambda *_: None)
    result = client.call(request(), 'WorkerTurn', 'one')
    assert result['status'] == 'ok'
    assert client.call(request(), 'WorkerTurn', 'one') == result
    assert len(calls) == 1
    assert len(calls[0][0]) == 2  # No prompt, token, or request body in argv.
    assert ledger.lookup('one')['response']['raw_response'] == raw
    assert ledger.lookup('one')['response']['usage_raw'] == {'output_tokens': 29}


@pytest.mark.parametrize('change', [
    {'model': 'qwen3.5-397b'}, {'stage': 'author'}, {'stage': 'reviews'},
    {'config': {}}, {'config': {'reasoning_effort': 'high'}},
    {'config': {'reasoning_effort': 'medium', 'temperature': 0}},
    {'output_budget': 128001}, {'output_budget': True},
])
def test_invalid_luna_boundary_never_dispatches(installed, monkeypatch, tmp_path, change):
    monkeypatch.setattr(subscription.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('dispatched'))
    backend = OmpSubscriptionBackend()
    payload = request() | change
    client = SwarmClient(Ledger(tmp_path / 'ledger'), backend)
    assert client.call(payload, 'WorkerTurn', 'bad')['error']['code'] == 'invalid_request'
    assert client.ledger.lookup('bad')['intent'] is None


def test_qwen_cannot_substitute_for_a_worker(tmp_path):
    backend = MockBackend()
    client = SwarmClient(Ledger(tmp_path), backend)
    assert client.call(request() | {'model': 'qwen3.5-397b'}, 'WorkerTurn', 'bad')['error']['code'] == 'invalid_request'
    assert backend.calls == []


def test_adaption_cannot_be_used_as_a_paid_worker_fallback(tmp_path):
    backend = AdaptionBackend(tmp_path / 'missing-key')
    payload = request() | {'config': {}}
    with pytest.raises(ValueError, match='restricted to Qwen'):
        backend.send(payload, 'WorkerTurn', 'one')


@pytest.mark.parametrize('raw', [
    envelope(status='incomplete', incomplete_details={'reason': 'max_output_tokens'}),
    envelope(status='incomplete', error={'code': 'unsupported_max_output_tokens', 'http_status': 400}),
    envelope(status='incomplete', incomplete_details={'reason': 'subscription_stream_incomplete'}),
    envelope(output_text='{malformed'),
])
def test_failed_outputs_are_durable_and_never_repaired(installed, monkeypatch, tmp_path, raw):
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=0, stdout=json.dumps(raw).encode())
    monkeypatch.setattr(subscription.subprocess, 'run', run)
    client = SwarmClient(Ledger(tmp_path / 'ledger'), OmpSubscriptionBackend())
    result = client.call(request(), 'WorkerTurn', 'one')
    assert result['status'] == 'incomplete'
    assert client.call(request(), 'WorkerTurn', 'one') == result
    assert len(calls) == 1
    assert client.ledger.lookup('one')['response']['raw_response'] == raw


def test_process_failure_is_ambiguous_and_blocks_resume(installed, monkeypatch, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired('private command', 1, stderr=b'secret')
    monkeypatch.setattr(subscription.subprocess, 'run', run)
    client = SwarmClient(Ledger(tmp_path / 'ledger'), OmpSubscriptionBackend(timeout=1))
    result = client.call(request(), 'WorkerTurn', 'one')
    assert result['error']['code'] == 'ambiguous_remote_execution'
    assert client.call(request(), 'WorkerTurn', 'one') == result
    assert len(calls) == 1
    assert 'secret' not in json.dumps(result)


def test_runtime_drift_blocks_send(installed, monkeypatch):
    backend = OmpSubscriptionBackend()
    installed[2].write_text('changed executable')
    monkeypatch.setattr(subscription.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('dispatched'))
    with pytest.raises(ValueError, match='runtime changed'):
        backend.send(request(), 'WorkerTurn', 'one')


def test_unreviewed_pi_version_fails_closed(installed):
    (installed[0] / 'install/current-version').write_text('future-version')
    with pytest.raises(ValueError, match='reviewed Pi'):
        OmpSubscriptionBackend()


def test_node_bridge_offline_behaviors():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for the subscription bridge tests')
    test_file = Path(__file__).with_name('subscription_bridge_test.mjs')
    # The Node tests use injected fetch, never credentials or a network request.
    process = subprocess.run([node, '--test', str(test_file)], capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize('stage,schema_id,model', [
    (stage, policy['schema'], model)
    for stage, policy in subscription.ROLE_SCHEMA_POLICY.items() for model in policy['models']
])
def test_all_subscription_roles_single_dispatch_and_resume(installed, monkeypatch, tmp_path, stage, schema_id, model):
    calls = []
    def run(*args, **kwargs):
        calls.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope()).encode())
    monkeypatch.setattr(subscription.subprocess, 'run', run)
    payload = request() | {'stage': stage, 'model': model}
    if stage == 'scientific-judge':
        payload.update(instructions='Preserved upstream grading guidance.\n\n', packet={}, history=[], upstream_messages=[
            {'role': 'user', 'content': [{'type': 'text', 'text': 'Preserved upstream grading guidance.\n\n## Rubric criteria\nOriginal rubric and artifact.'}]}])
    client = SwarmClient(Ledger(tmp_path), OmpSubscriptionBackend(model=model), validator=lambda *_: None)
    result = client.call(payload, schema_id, 'role')
    assert result['status'] == 'ok'
    assert client.call(payload, schema_id, 'role') == result
    assert len(calls) == 1
    assert calls[0]['schema_id'] == schema_id
    assert calls[0]['model']['id'] == model.split('/', 1)[1]
    assert calls[0]['timeout_ms'] is None


@pytest.mark.parametrize('stage,schema_id,model', [
    ('author', 'ProposalCard', 'openai-codex/gpt-6-luna'),
    ('reviews', 'Review', 'openai-codex/gpt-6.1-sol'),
    ('validator', 'SubscriptionCardResponse', 'openai-codex/gpt-6.1-terra'),
    ('workers', 'WorkerTurn', 'openai-codex/gpt-6-astra'),
    ('author', 'Review', 'openai-codex/gpt-6.1-terra'),
    ('validator', 'EvalLabel', 'openai-codex/gpt-6-astra'),
    ('evaluation', 'SubscriptionCardResponse', 'openai-codex/gpt-6.1-sol'),
    ('evaluation', 'EvalLabel', 'qwen3.5-397b'),
    ('unknown', 'WorkerTurn', 'openai-codex/gpt-6.1-terra'),
])
def test_model_role_schema_mismatch_has_no_intent_or_dispatch(tmp_path, stage, schema_id, model):
    backend = MockBackend()
    client = SwarmClient(Ledger(tmp_path), backend)
    result = client.call(request() | {'stage': stage, 'model': model}, schema_id, 'bad')
    assert result['error']['code'] == 'invalid_request'
    assert client.ledger.lookup('bad')['intent'] is None
    assert backend.calls == []


@pytest.mark.parametrize('stage,schema_id,model', [
    ('author', 'ProposalCard', 'openai-codex/gpt-6.1-terra'),
    ('reviews', 'Review', 'openai-codex/gpt-6.1-terra'),
    ('reviews', 'Review', 'openai-codex/gpt-6-luna'),
    ('validator', 'SubscriptionCardResponse', 'openai-codex/gpt-6-astra'),
    ('evaluation', 'EvalLabel', 'openai-codex/gpt-6.1-sol'),
    ('evaluation', 'EvalLabel', 'openai-codex/gpt-6.1-terra'),
])
@pytest.mark.parametrize('failure', ['timeout', 'invalid_json', 'truncation'])
def test_new_roles_never_retry_or_repair(tmp_path, stage, schema_id, model, failure):
    backend = MockBackend(scenario=failure)
    client = SwarmClient(Ledger(tmp_path), backend)
    payload = request() | {'stage': stage, 'model': model}
    first = client.call(payload, schema_id, 'failure')
    assert first['status'] == 'incomplete'
    assert client.call(payload, schema_id, 'failure') == first
    assert len(backend.calls) == 1


@pytest.mark.parametrize('stage,schema_id,model,response', [
    ('validator', 'SubscriptionCardResponse', 'openai-codex/gpt-6-astra', {
        'model': 'openai-codex/gpt-6-astra', 'mode': 'subscription',
        'validated_at': '2026-11-08T12:00:00Z', 'trace': 'provider supplied trace',
        'applicable': True, 'reason': 'Stage fields remain downstream validated'}),
    ('evaluation', 'EvalLabel', 'openai-codex/gpt-6.1-sol', {
        'schema_version': '1.0', 'id': 'original-packet-id', 'significant_claim': False,
        'normalized_type': None, 'evidence_support': False, 'decision_relevance': False,
        'uncertain': False}),
])
def test_external_response_metadata_and_original_text_are_preserved(tmp_path, stage, schema_id, model, response):
    text = json.dumps(response, indent=2)
    backend = MockBackend(raw_responses=[envelope(output_text=text)])
    client = SwarmClient(Ledger(tmp_path), backend)
    result = client.call(request() | {'stage': stage, 'model': model}, schema_id, 'external')
    assert result == {'status': 'ok', 'data': response, 'error': None}
    assert client.ledger.lookup('external')['response']['raw_response']['output_text'] == text


@pytest.mark.parametrize('change', [
    {'model': 'gpt6-astra'}, {'mode': 'api'}, {'trace': ''}, {'validated_at': ''},
])
def test_card_response_requires_actual_subscription_provenance(tmp_path, change):
    response = {'model': 'openai-codex/gpt-6-astra', 'mode': 'subscription',
                'validated_at': '2026-11-08', 'trace': 'original'} | change
    backend = MockBackend(responses=[response])
    client = SwarmClient(Ledger(tmp_path), backend)
    payload = request() | {'stage': 'validator', 'model': 'openai-codex/gpt-6-astra'}
    first = client.call(payload, 'SubscriptionCardResponse', 'bad-card')
    assert first['error']['code'] == 'schema_invalid'
    assert client.call(payload, 'SubscriptionCardResponse', 'bad-card') == first
    assert len(backend.calls) == 1
