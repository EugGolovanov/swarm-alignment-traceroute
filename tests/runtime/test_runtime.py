"""Offline behavioral coverage for the single-sample runtime contract."""
import hashlib
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from little_pom.runtime import (AdaptionBackend, ArtifactStore, FreezeStore, Ledger,
                               MockBackend, SimulatedCrash, SwarmClient)
from little_pom.contracts import canonical_json, sha256_hash


def request():
    return {'model': 'qwen3.5-397b', 'config': {}, 'instructions': 'Return JSON.',
            'packet': {'schema_version': '1.0', 'id': 'opaque-packet'},
            'history': [], 'output_budget': 256, 'task_id': 'task-one', 'stage': 'author'}


def output():
    return {'schema_version': '1.0', 'id': 'output-one', 'result': 'complete'}


def validator(schema_id, value):
    assert schema_id == 'TestOutput'
    if set(value) != {'schema_version', 'id', 'result'} or value['result'] != 'complete':
        raise ValueError('Invalid TestOutput')
    return value


def client(root, backend=None, **kwargs):
    return SwarmClient(Ledger(root), backend or MockBackend(responses=[output()]),
                       validator=validator, **kwargs)


def test_success_is_cached_with_raw_usage_and_full_request_hash(tmp_path):
    backend = MockBackend(raw_responses=[{
        'output_text': json.dumps(output()), 'status': 'completed',
        'usage': {'input_tokens': 31, 'output_tokens': 12}, 'model': 'returned-model',
        'id': 'provider-response', 'extra_provider_field': {'kept': True}}])
    instance = client(tmp_path, backend)
    first = instance.call(request(), 'TestOutput', 'same')
    resumed = client(tmp_path, MockBackend(scenario='crash'))
    assert first == resumed.call(request(), 'TestOutput', 'same')
    assert first == {'status': 'ok', 'data': output(), 'error': None}
    assert len(backend.calls) == 1
    assert resumed.backend.calls == []
    record = instance.ledger.lookup('same')
    assert record['response']['usage_raw'] == {'input_tokens': 31, 'output_tokens': 12}
    assert record['response']['model_metadata']['model'] == 'returned-model'
    assert record['response']['sdk_version'] is None
    assert record['response']['latency_ms'] >= 0
    assert record['response']['raw_response']['extra_provider_field'] == {'kept': True}
    assert record['intent']['request_hash'] == sha256_hash({
        'schema_version': '1.0', 'schema_id': 'TestOutput', 'request': request(),
        'transport': backend.configuration()})


@pytest.mark.parametrize('changed', ['instructions', 'config', 'packet', 'history', 'output_budget'])
def test_execution_identity_rejects_changed_context(tmp_path, changed):
    instance = client(tmp_path)
    instance.call(request(), 'TestOutput', 'same')
    modified = request()
    modified[changed] = {'instructions': 'Different instructions', 'config': {'temperature': 0.1},
                         'packet': {'id': 'different'}, 'history': [{'role': 'user', 'content': 'new'}],
                         'output_budget': 512}[changed]
    result = instance.call(modified, 'TestOutput', 'same')
    assert result['error']['code'] == 'request_hash_conflict'
    assert len(instance.backend.calls) == 1


@pytest.mark.parametrize('checkpoint,sent,has_response', [
    ('before_send', 0, False), ('after_send', 1, False), ('after_response', 1, True)])
def test_crash_resume_never_resamples(tmp_path, checkpoint, sent, has_response):
    def crash(point):
        if point == checkpoint:
            raise SimulatedCrash(point)
    instance = client(tmp_path, fault_hook=crash)
    with pytest.raises(SimulatedCrash):
        instance.call(request(), 'TestOutput', 'crashed')
    assert len(instance.backend.calls) == sent
    assert bool(instance.ledger.lookup('crashed')['response']) == has_response
    recovery = client(tmp_path, MockBackend(scenario='crash'))
    result = recovery.call(request(), 'TestOutput', 'crashed')
    assert result['status'] == ('ok' if has_response else 'incomplete')
    assert recovery.backend.calls == []


@pytest.mark.parametrize('scenario,code', [
    ('invalid_json', 'invalid_json'), ('truncation', 'truncated_or_failed_output'),
    ('empty', 'empty_output'), ('timeout', 'ambiguous_remote_execution')])
def test_failure_scenarios_are_incomplete_without_repair(tmp_path, scenario, code):
    backend = MockBackend(scenario=scenario, responses=[output()])
    instance = client(tmp_path, backend)
    result = instance.call(request(), 'TestOutput', 'failure')
    assert result['error']['code'] == code
    assert instance.call(request(), 'TestOutput', 'failure') == result
    assert len(backend.calls) == 1


def test_backend_crash_leaves_intent_and_blocks_resume(tmp_path):
    instance = client(tmp_path, MockBackend(scenario='crash'))
    with pytest.raises(SimulatedCrash):
        instance.call(request(), 'TestOutput', 'crash')
    recovery = client(tmp_path)
    assert recovery.call(request(), 'TestOutput', 'crash')['status'] == 'incomplete'
    assert not recovery.backend.calls


def test_raw_response_is_durable_before_schema_validation(tmp_path):
    ledger = Ledger(tmp_path)
    def checking_validator(schema_id, parsed):
        saved = ledger.lookup('execution')['response']
        assert saved['raw_response']['output_text'] == canonical_json(output()).decode()
        raise ValueError('Refuse schema')
    backend = MockBackend(responses=[output()])
    instance = SwarmClient(ledger, backend, checking_validator)
    assert instance.call(request(), 'Anything', 'execution')['error']['code'] == 'schema_invalid'
    assert len(backend.calls) == 1


@pytest.mark.parametrize('text', ['{"result":1,"result":2}', '{"result":NaN}', '[]', 'null'])
def test_noncanonical_model_json_is_rejected_without_repair(tmp_path, text):
    backend = MockBackend(raw_responses=[{'output_text': text, 'status': 'completed'}])
    assert client(tmp_path, backend).call(request(), 'TestOutput', 'bad')['error']['code'] == 'invalid_json'
    assert len(backend.calls) == 1


def test_default_contract_validator_accepts_real_review(tmp_path):
    review = {'schema_version': '1.0', 'id': 'review-one', 'status': 'no_claim',
              'claim': '', 'evidence_ids': [], 'decision_relevance_text': ''}
    result = SwarmClient(Ledger(tmp_path), MockBackend(responses=[review])).call(request(), 'Review', 'review')
    assert result['status'] == 'ok'
    assert result['data'] == {**review, 'id': sha256_hash({'domain': 'model_output', 'execution_id': 'review'})}


def _concurrent_call(root, counter, queue):
    class CountingBackend(MockBackend):
        def send(self, request, schema_id, execution_id):
            with counter.get_lock():
                counter.value += 1
            return super().send(request, schema_id, execution_id)
    result = client(root, CountingBackend(responses=[output()])).call(request(), 'TestOutput', 'shared')
    queue.put(result)


def test_interprocess_lock_allows_only_one_sample(tmp_path):
    context = multiprocessing.get_context('spawn')
    counter = context.Value('i', 0)
    queue = context.Queue()
    processes = [context.Process(target=_concurrent_call, args=(str(tmp_path), counter, queue))
                 for _ in range(4)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert counter.value == 1
    assert all(result['status'] == 'ok' for result in results)


def test_immutable_artifacts_and_freeze_tampering(tmp_path):
    store = ArtifactStore(tmp_path)
    path = store.put_once('data/config.json', {'seed': 5})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        store.put_once('data/config.json', {'seed': 9})
    assert path.read_bytes() == original
    manifest = {'schema_version': '1.0', 'id': 'stage-one',
                'files': {'data/config.json': hashlib.sha256(original).hexdigest()}}
    freezes = FreezeStore(tmp_path)
    assert freezes.create(manifest) == manifest
    assert freezes.verify(manifest)
    with pytest.raises(FileExistsError):
        freezes.create(manifest)
    path.chmod(0o600)
    path.write_text('{"seed":9}')
    assert not freezes.verify(manifest)
    assert not freezes.verify({**manifest, 'id': 'another-stage'})


def test_artifact_paths_reject_traversal_and_symlinks(tmp_path):
    root = tmp_path / 'root'
    store = ArtifactStore(root)
    for name in ('../outside', '/absolute'):
        with pytest.raises(ValueError):
            store.put_once(name, 'no')
    (root / 'link').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        store.put_once('link/outside', 'no')


def test_mock_missing_usage_is_null_not_synthetic(tmp_path):
    instance = client(tmp_path)
    instance.call(request(), 'TestOutput', 'usage')
    assert instance.ledger.lookup('usage')['response']['usage_raw'] is None


class FakeSDK:
    def __init__(self, captured, raw, **kwargs):
        captured['constructor'] = kwargs
        self.captured = captured
        self.raw = raw
        self.responses = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def create(self, **kwargs):
        self.captured['request'] = kwargs
        if isinstance(self.raw, Exception):
            raise self.raw
        return SimpleNamespace(model_dump=lambda **_: self.raw,
                               output_text=self.raw.get('output_text'))


def test_adaption_is_lazy_disables_retries_and_redacts_full_response(tmp_path, caplog):
    secret_path = tmp_path / 'not-created-yet'
    captured = {}
    key = 'synthetic-unit-test-secret-12345'
    raw = {'status': 'completed', 'output_text': json.dumps(output()),
           'model': 'returned-alias', 'usage': None,
           'authorization': key, 'provider_diagnostic': f'echo {key}'}
    backend = AdaptionBackend(secret_path, client_factory=lambda **kwargs: FakeSDK(captured, raw, **kwargs))
    assert not captured
    secret_path.write_text(key)
    ledger_root = tmp_path / 'ledger'
    result = client(ledger_root, backend).call(request(), 'TestOutput', 'live-fake')
    assert result['status'] == 'ok'
    assert captured['constructor']['base_url'] == 'https://api.prod.adaptionlabs.ai'
    assert captured['constructor']['timeout'] is None
    assert captured['constructor']['max_retries'] == 0
    assert captured['constructor']['api_key'] == key
    assert captured['request']['store'] is False
    assert 'previous_response_id' not in captured['request']
    assert json.loads(captured['request']['input']) == {'packet': request()['packet'], 'history': []}
    for path in ledger_root.rglob('*'):
        if path.is_file():
            assert key.encode() not in path.read_bytes()
    assert key not in caplog.text
    # A saved response can be resumed with the credential file removed.
    secret_path.unlink()
    assert client(ledger_root, backend).call(request(), 'TestOutput', 'live-fake') == result


def test_exception_secrets_never_reach_errors_or_audit(tmp_path, caplog):
    key = 'synthetic-exception-secret-67890'
    secret_path = tmp_path / 'key'
    secret_path.write_text(key)
    captured = {}
    backend = AdaptionBackend(secret_path, client_factory=lambda **kwargs: FakeSDK(
        captured, RuntimeError(f'Authorization: {key}'), **kwargs))
    root = tmp_path / 'ledger'
    result = client(root, backend).call(request(), 'TestOutput', 'failure')
    assert result['error']['code'] == 'ambiguous_remote_execution'
    assert key not in json.dumps(result) + caplog.text
    assert all(key.encode() not in path.read_bytes() for path in root.rglob('*') if path.is_file())


@pytest.mark.parametrize('field', ['api_key', 'authorization', 'secret_path'])
def test_credentials_rejected_before_hash_or_intent(tmp_path, field):
    payload = request()
    payload['config'][field] = 'not-a-real-secret'
    instance = client(tmp_path)
    assert instance.call(payload, 'TestOutput', 'credentials')['error']['code'] == 'invalid_request'
    assert instance.ledger.lookup('credentials')['intent'] is None
    assert not instance.backend.calls


def test_response_tampering_does_not_trigger_resample(tmp_path):
    instance = client(tmp_path)
    instance.call(request(), 'TestOutput', 'tampered')
    response_path = tmp_path / 'response' / (Ledger.key('tampered') + '.json')
    saved = json.loads(response_path.read_bytes())
    saved['raw_response']['output_text'] = '{}'
    response_path.chmod(0o600)
    response_path.write_text(json.dumps(saved))
    assert instance.call(request(), 'TestOutput', 'tampered')['error']['code'] == 'ledger_corrupt'
    assert len(instance.backend.calls) == 1


@pytest.mark.parametrize('configuration', [
    {'concurrency': 2}, {'top_p': 0.9}, {'reasoning': {'effort': 'medium'}},
    {'previous_response_id': 'remote'}, {'max_retries': 2}, {'temperature': True}])
def test_unsupported_adaption_config_rejected_before_secret_read(tmp_path, configuration):
    backend = AdaptionBackend(tmp_path / 'nonexistent-secret')
    instance = client(tmp_path / 'ledger', backend)
    payload = request()
    payload['config'] = configuration
    assert instance.call(payload, 'TestOutput', 'bad-config')['error']['code'] == 'invalid_request'
    assert instance.ledger.lookup('bad-config')['intent'] is None


def test_trusted_provider_ids_override_missing_and_forged_identity(tmp_path):
    review = {'status': 'no_claim', 'claim': '', 'evidence_ids': [],
              'decision_relevance_text': '', 'id': 'forged'}
    instance = SwarmClient(Ledger(tmp_path), MockBackend(responses=[review]))
    result = instance.call(request(), 'Review', 'trusted')
    assert result['status'] == 'ok'
    assert result['data']['schema_version'] == '1.0'
    assert result['data']['id'] == sha256_hash({'domain': 'model_output', 'execution_id': 'trusted'})
    assert 'task_id' not in result['data']
    raw = instance.ledger.lookup('trusted')['response']['raw_response']
    assert json.loads(raw['output_text'])['id'] == 'forged'


def _exit_at_checkpoint(root, checkpoint):
    import os
    def abrupt(point):
        if point == checkpoint:
            os._exit(23)
    client(root, fault_hook=abrupt).call(request(), 'TestOutput', 'process-crash')


@pytest.mark.parametrize('checkpoint', ['before_send', 'after_send', 'after_response'])
def test_actual_process_exit_preserves_resume_decision(tmp_path, checkpoint):
    context = multiprocessing.get_context('spawn')
    process = context.Process(target=_exit_at_checkpoint, args=(str(tmp_path), checkpoint))
    process.start()
    process.join(timeout=20)
    assert process.exitcode == 23
    recovery = client(tmp_path, MockBackend(scenario='crash'))
    result = recovery.call(request(), 'TestOutput', 'process-crash')
    assert result['status'] == ('ok' if checkpoint == 'after_response' else 'incomplete')
    assert recovery.backend.calls == []


def test_installed_adaption_sdk_with_in_memory_http_transport(tmp_path):
    adaption = pytest.importorskip('adaption')
    httpx = pytest.importorskip('httpx')
    captured = []
    key_path = tmp_path / 'synthetic-key'
    key_path.write_text('sdk-test-key-only')
    def respond(http_request):
        captured.append(http_request)
        return httpx.Response(200, json={
            'id': 'provider-opaque', 'object': 'response', 'status': 'completed',
            'model': 'qwen3.5-397b', 'output': [], 'output_text': json.dumps(output()),
            'usage': None, 'incomplete_details': None, 'error': None})
    def factory(**kwargs):
        return adaption.Adaption(**kwargs, http_client=httpx.Client(
            transport=httpx.MockTransport(respond)))
    backend = AdaptionBackend(key_path, client_factory=factory)
    instance = client(tmp_path / 'ledger', backend)
    payload = request()
    payload['config'] = {'temperature': 0}
    assert instance.call(payload, 'TestOutput', 'sdk')['status'] == 'ok'
    assert len(captured) == 1
    assert captured[0].method == 'POST'
    assert all(value is None for value in captured[0].extensions['timeout'].values())
    assert str(captured[0].url) == 'https://api.prod.adaptionlabs.ai/api/v1/responses'
    body = json.loads(captured[0].content)
    assert body['store'] is False and body['temperature'] == 0
    assert body['max_output_tokens'] == 256
    assert captured[0].headers['authorization'] == 'Bearer sdk-test-key-only'
    saved = instance.ledger.lookup('sdk')['response']
    assert saved['raw_response']['object'] == 'response'
    assert saved['sdk_version'] is not None


def test_installed_adaption_sdk_catalog_uses_data():
    adaption = pytest.importorskip('adaption')
    httpx = pytest.importorskip('httpx')
    captured = []

    def respond(http_request):
        captured.append(http_request)
        return httpx.Response(200, json={
            'object': 'list', 'data': [
                {'id': 'qwen3.5-397b', 'created': 0,
                 'object': 'model', 'owned_by': 'test'}]})

    backend = AdaptionBackend('unused-secret')
    with adaption.Adaption(
            api_key='sdk-test-key-only', base_url=backend.base_url, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as sdk:
        response = sdk.models.list(type='hosted')
        available = {model.id for model in response.data}
    assert available == {'qwen3.5-397b'}
    assert len(captured) == 1
    assert captured[0].method == 'GET'
    assert str(captured[0].url) == 'https://api.prod.adaptionlabs.ai/api/v1/models?type=hosted'


def test_missing_convenience_text_uses_response_output_parts(tmp_path):
    raw = {'status': 'completed', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(output())}]}]}
    instance = client(tmp_path, MockBackend(raw_responses=[raw]))
    assert instance.call(request(), 'TestOutput', 'parts')['data'] == output()


def test_unwritable_intent_never_dispatches(tmp_path, monkeypatch):
    instance = client(tmp_path)
    def fail_write(*args, **kwargs):
        raise OSError('synthetic storage failure')
    monkeypatch.setattr(instance.ledger.store, 'put_once', fail_write)
    result = instance.call(request(), 'TestOutput', 'disk-failure')
    assert result['error']['code'] == 'ledger_failure'
    assert instance.backend.calls == []


def test_credentials_in_url_or_packet_are_not_hashed_or_persisted(tmp_path):
    with pytest.raises(ValueError):
        AdaptionBackend(tmp_path / 'unused', base_url='https://user:password@example.invalid/api')
    payload = request()
    payload['packet']['text'] = 'Bearer synthetic-credential'
    instance = client(tmp_path)
    result = instance.call(payload, 'TestOutput', 'credential-text')
    assert result['error']['code'] == 'invalid_request'
    assert instance.ledger.lookup('credential-text')['intent'] is None


def test_new_ledger_ancestors_are_synced_before_model_dispatch(tmp_path, monkeypatch):
    from little_pom.runtime import storage
    events = []
    original_mkdir = Path.mkdir
    original_sync = storage._sync_directory
    def mkdir(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        events.append(('mkdir', path))
        return result
    def sync(path):
        original_sync(path)
        events.append(('sync', path))
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    monkeypatch.setattr(storage, '_sync_directory', sync)
    root = tmp_path / 'new-parent' / 'new-run' / 'ledger'
    class OrderingBackend(MockBackend):
        def send(self, payload, schema_id, execution_id):
            events.append(('send', None))
            return super().send(payload, schema_id, execution_id)
    instance = client(root, OrderingBackend(responses=[output()]))
    assert instance.call(request(), 'TestOutput', 'durable-root')['status'] == 'ok'
    sent_at = events.index(('send', None))
    for directory in (root.parent.parent, root.parent, root, root / 'intent'):
        created_at = events.index(('mkdir', directory))
        synced_at = events.index(('sync', directory.parent), created_at + 1)
        assert created_at < synced_at < sent_at
    # Parent links must be durable before their descendant directories appear.
    for parent, child in ((root.parent.parent, root.parent), (root.parent, root)):
        created_at = events.index(('mkdir', parent))
        assert events.index(('sync', parent.parent), created_at + 1) < events.index(('mkdir', child))
