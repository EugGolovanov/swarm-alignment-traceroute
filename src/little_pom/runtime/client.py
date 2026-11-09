"""Single-sample model execution: durable intent, raw response, then validation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import time
from typing import Callable

from little_pom.contracts import canonical_json, get_schema, request_hash as compute_request_hash, sha256_hash, validate
from .backends import Redactor, SENSITIVE_FIELDS
from .storage import Ledger
from .subscription import validate_role_schema, validate_upstream_messages


def _incomplete(code: str, message: str) -> dict:
    return {'status': 'incomplete', 'data': None,
            'error': {'code': code, 'message': message}}


def _validate_request(request: dict, schema_id: str) -> None:
    if not isinstance(request, dict) or not isinstance(schema_id, str) or not schema_id:
        raise ValueError('Request must be an object and schema_id a nonempty string')
    allowed = {'model', 'config', 'instructions', 'packet', 'history', 'output_budget',
               'task_id', 'stage', 'run_id', 'schema_version', 'id', 'upstream_messages'}
    if set(request) - allowed:
        raise ValueError('Unknown request fields')
    for name in ('model', 'instructions'):
        if not isinstance(request.get(name), str) or (
                not request[name].strip() and not (name == 'instructions' and request.get('stage') == 'scientific-judge')):
            raise ValueError('Missing request text')
    validate_role_schema(request, schema_id, allow_adaption=True)
    validate_upstream_messages(request)
    if not isinstance(request.get('packet'), dict) or not isinstance(request.get('config'), dict):
        raise ValueError('packet and config must be objects')
    if not isinstance(request.get('history'), list):
        raise ValueError('history must be a local JSON list')
    budget = request.get('output_budget')
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ValueError('output_budget must be a positive integer')
    if request.get('schema_version', '1.0') != '1.0':
        raise ValueError('Unsupported request schema_version')
    reserved = {'model', 'instructions', 'input', 'max_output_tokens', 'store',
                'previous_response_id', 'max_retries', 'timeout', 'base_url',
                'extra_headers', 'extra_body', 'extra_query', 'stream',
                'agentic_memory', 'tools', 'tool_choice'}
    if set(request['config']) & reserved:
        raise ValueError('Transport and server-history configuration cannot be overridden')

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or key.lower() in SENSITIVE_FIELDS:
                    raise ValueError('Credential fields are forbidden in requests')
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError('Request must contain only JSON values')
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Nonfinite JSON number')
    check(request)
    canonical_json(request)


def _strict_json(text: str) -> dict:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('Nonfinite JSON constant')
    parsed = json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    if not isinstance(parsed, dict):
        raise ValueError('Model output must be a JSON object')
    return parsed


def _output_text(raw: dict) -> str | None:
    if isinstance(raw.get('output_text'), str):
        return raw['output_text']
    pieces = []
    items = raw.get('output')
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        contents = item.get('content')
        if not isinstance(contents, list):
            continue
        for content in contents:
            if isinstance(content, dict) and content.get('type') == 'output_text':
                text = content.get('text')
                if isinstance(text, str):
                    pieces.append(text)
    return ''.join(pieces) if pieces else None


class SwarmClient:
    """Synchronous client with process-safe resume and no automatic resampling.

    validator(schema_id, output_dict) may raise on invalid output. A fault_hook
    accepting a checkpoint name enables explicit crash testing; checkpoints are
    before_send, after_send, and after_response. None are retried.
    """

    def __init__(self, ledger: Ledger, backend, validator: Callable | None = None,
                 *, fault_hook: Callable[[str], None] | None = None):
        self.ledger = ledger
        self.backend = backend
        self.validator = validator or validate
        self.fault_hook = fault_hook
        self.redactor = getattr(backend, 'redactor', Redactor())

    def _checkpoint(self, name: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(name)

    def call(self, request: dict, schema_id: str, execution_id: str) -> dict:
        try:
            return self._call(request, schema_id, execution_id)
        except (OSError, ValueError, TypeError, KeyError):
            # Local persistence/corruption failures must not expose filesystem
            # paths or secret-bearing exception strings through service results.
            return _incomplete('ledger_failure', 'Execution could not be safely persisted or recovered')

    def _call(self, request: dict, schema_id: str, execution_id: str) -> dict:
        try:
            _validate_request(request, schema_id)
            self.ledger.key(execution_id)
            # Roundtrip snapshots caller-owned mutable packet/history before locking.
            request = json.loads(canonical_json(request))
            backend_validation = getattr(self.backend, 'validate_request', None)
            if backend_validation is not None:
                backend_validation(request)
            configuration = self.backend.configuration()
            request_hash = compute_request_hash(request, schema_id, configuration)
        except Exception:
            return _incomplete('invalid_request', 'Request failed boundary validation')
        with self.ledger.lock(execution_id):
            existing = self.ledger.lookup(execution_id)
            intent = existing['intent']
            if intent and intent.get('request_hash') != request_hash:
                return _incomplete('request_hash_conflict', 'Execution identity already belongs to a different request')
            if existing['response']:
                return self._parse_saved(existing, schema_id, execution_id, request_hash)
            if intent:
                return _incomplete('ambiguous_remote_execution', 'Durable intent has no response; resampling is forbidden')
            if existing['result']:
                return _incomplete('ledger_corrupt', 'Result exists without an intent or response')
            record = {'schema_version': '1.0', 'id': execution_id,
                      'execution_id': execution_id, 'task_id': request.get('task_id'),
                      'stage': request.get('stage'), 'run_id': request.get('run_id'),
                      'request_hash': request_hash, 'schema_id': schema_id,
                      'model': request['model'], 'request': request,
                      'transport': configuration,
                      'timestamp': datetime.now(timezone.utc).isoformat()}
            self.ledger.write_record('intent', execution_id, record)
            self._checkpoint('before_send')
            started = time.monotonic_ns()
            try:
                raw = self.backend.send(request, schema_id, execution_id)
            except Exception as exc:
                # Never stringify provider exceptions: they may contain credentials,
                # request bodies, or transport headers. Even DNS errors are not retried.
                # Numeric HTTP status is safe and distinguishes rejected requests from
                # unknown transport failures without persisting secret-bearing bodies.
                status = getattr(exc, 'status_code', None)
                status = status if type(status) is int and 100 <= status <= 599 else None
                self.ledger.audit({'event': 'transport_incomplete', 'execution_id': execution_id,
                                   'request_hash': request_hash, 'http_status': status,
                                   'code': 'ambiguous_remote_execution'})
                return _incomplete('ambiguous_remote_execution', 'Durable intent has no response; resampling is forbidden')
            latency_ms = (time.monotonic_ns() - started) / 1_000_000
            self._checkpoint('after_send')
            raw = self.redactor.clean(raw)
            response = {'schema_version': '1.0', 'id': execution_id,
                        'execution_id': execution_id, 'task_id': request.get('task_id'),
                        'request_hash': request_hash, 'response_hash': sha256_hash(raw),
                        'raw_response': raw, 'latency_ms': latency_ms,
                        'usage_raw': raw.get('usage') if isinstance(raw, dict) else None,
                        'model_metadata': {field: raw.get(field) for field in
                                           ('id', 'model', 'system_fingerprint', 'service_tier')}
                                          if isinstance(raw, dict) else None,
                        'sdk_version': (raw.get('_transport_metadata') or {}).get('sdk_version')
                                       if isinstance(raw, dict) else None,
                        'timestamp': datetime.now(timezone.utc).isoformat()}
            self.ledger.write_record('response', execution_id, response)
            self._checkpoint('after_response')
            return self._parse_saved({'intent': record, 'response': response, 'result': None},
                                     schema_id, execution_id, request_hash)

    def _parse_saved(self, existing: dict, schema_id: str, execution_id: str,
                     request_hash: str) -> dict:
        response = existing['response']
        raw = response.get('raw_response')
        if (not existing['intent'] or response.get('request_hash') != request_hash
                or response.get('response_hash') != sha256_hash(raw)):
            return _incomplete('ledger_corrupt', 'Saved response failed integrity verification')
        if existing['result']:
            result_record = existing['result']
            if (result_record.get('request_hash') != request_hash or
                    result_record.get('response_hash') != response['response_hash'] or
                    result_record.get('result_hash') != sha256_hash(result_record.get('service_result'))):
                return _incomplete('ledger_corrupt', 'Saved result failed integrity verification')
            return result_record['service_result']
        if not isinstance(raw, dict):
            result = _incomplete('invalid_response', 'Provider response was not an object')
        elif (raw.get('status') not in (None, 'completed') or raw.get('incomplete_details')
              or raw.get('error')
              or raw.get('finish_reason') in ('length', 'max_tokens', 'content_filter')):
            result = _incomplete('truncated_or_failed_output', 'Provider did not complete the response')
        else:
            text = _output_text(raw)
            if not text or not text.strip():
                result = _incomplete('empty_output', 'Provider returned no output text')
            else:
                try:
                    output = _strict_json(text)
                except (ValueError, TypeError):
                    result = _incomplete('invalid_json', 'Provider output was not a valid JSON object')
                else:
                    try:
                        try:
                            properties = get_schema(schema_id)['properties']
                        except ValueError:
                            properties = {}
                        trusted = {'schema_version': '1.0',
                                   'id': sha256_hash({'domain': 'model_output',
                                                      'execution_id': execution_id}),
                                   'task_id': existing['intent'].get('task_id')}
                        for key, value in trusted.items():
                            if key in properties and existing['intent'].get('stage') not in ('validator', 'evaluation'):
                                output[key] = value
                        self.validator(schema_id, output)
                    except Exception:
                        result = _incomplete('schema_invalid', 'Provider output failed the frozen schema')
                    else:
                        result = {'status': 'ok', 'data': output, 'error': None}
        record = {'schema_version': '1.0', 'id': execution_id,
                  'task_id': existing['intent'].get('task_id'), 'request_hash': request_hash,
                  'response_hash': response['response_hash'], 'status': result['status'],
                  'result_hash': sha256_hash(result), 'service_result': result}
        self.ledger.write_record('result', execution_id, record)
        return result
