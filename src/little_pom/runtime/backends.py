"""Explicit offline and Adaption transports. Construction never reads credentials."""
from __future__ import annotations

import copy
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path
import threading
from typing import Callable
from urllib.parse import urlsplit

from little_pom.contracts import canonical_json


SENSITIVE_FIELDS = frozenset({'api_key', 'api-key', 'apikey', 'authorization',
                              'access_token', 'access-token', 'accesstoken',
                              'secret', 'secret_path', 'password', 'credential', 'credentials'})


class Redactor:
    def __init__(self):
        self._secrets: list[str] = []

    def add(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def clean(self, value: object) -> object:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, dict):
            return {str(self.clean(key)): '[REDACTED]' if key.lower() in SENSITIVE_FIELDS
                    else self.clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        return value


class SimulatedCrash(BaseException):
    """Offline abrupt termination which deliberately bypasses exception handling."""


class MockBackend:
    """Deterministic external responses, with no SDK or credentials required.

    responses may be a sequence, execution-id mapping, or a callable accepting
    (request, schema_id, execution_id). Each entry is a parsed output dictionary.
    raw_responses uses the same selection forms but returns provider envelopes.
    """

    def __init__(self, scenario: str = 'success', responses=None, *, raw_responses=None):
        if scenario not in {'success', 'invalid_json', 'truncation', 'crash', 'timeout', 'empty'}:
            raise ValueError('Unknown mock scenario')
        if responses is not None and raw_responses is not None:
            raise ValueError('Select parsed responses or raw_responses, not both')
        self.scenario = scenario
        self.responses = responses
        self.raw_responses = raw_responses
        self.calls: list[dict] = []
        self.redactor = Redactor()
        self._lock = threading.Lock()

    def configuration(self) -> dict:
        return {'backend': 'mock', 'max_retries': 0}

    @staticmethod
    def _select(source, index, request, schema_id, execution_id):
        if callable(source):
            return source(request, schema_id, execution_id)
        if isinstance(source, dict):
            return copy.deepcopy(source[execution_id])
        return copy.deepcopy(source[index])

    def send(self, request: dict, schema_id: str, execution_id: str) -> dict:
        with self._lock:
            index = len(self.calls)
            self.calls.append(copy.deepcopy({'request': request, 'schema_id': schema_id,
                                             'execution_id': execution_id}))
            if self.scenario == 'crash':
                raise SimulatedCrash('Mock crash after possible send')
            if self.scenario == 'timeout':
                raise TimeoutError('Mock ambiguous timeout')
            if self.raw_responses is not None:
                return self._select(self.raw_responses, index, request, schema_id, execution_id)
            output = (self._select(self.responses, index, request, schema_id, execution_id)
                      if self.responses is not None else
                      {'schema_version': '1.0', 'id': execution_id, 'result': 'mock success'})
            text = canonical_json(output).decode('utf-8')
            if self.scenario == 'invalid_json':
                text = '{not-json'
            elif self.scenario == 'empty':
                text = ''
            return {'id': 'mock-' + execution_id, 'model': request['model'],
                    'status': 'incomplete' if self.scenario == 'truncation' else 'completed',
                    'incomplete_details': {'reason': 'max_output_tokens'} if self.scenario == 'truncation' else None,
                    'output_text': text, 'usage': None, 'sdk_version': None}


class AdaptionBackend:
    """Host-side synchronous Responses API, explicitly without transport retries."""

    def __init__(self, secret_path: str | Path, *,
                 base_url: str = 'https://api.prod.adaptionlabs.ai/api/v1',
                 timeout: float = 120.0, client_factory: Callable | None = None):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a finite positive number')
        endpoint = urlsplit(base_url)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username
                or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError('Adaption endpoint must be an HTTPS URL without credentials or query')
        self._secret_path = Path(secret_path)
        self.base_url = base_url
        self.timeout = float(timeout)
        self._client_factory = client_factory
        self.redactor = Redactor()

    def configuration(self) -> dict:
        return {'backend': 'adaption', 'base_url': self.base_url,
                'timeout': self.timeout, 'max_retries': 0, 'store': False}

    def validate_request(self, request: dict) -> None:
        # Adaption 0.19's Responses API exposes temperature, but not top_p,
        # reasoning, or provider JSON-schema options. Orchestration settings do
        # not belong in SDK kwargs and must not become an ambiguous sent call.
        if set(request['config']) - {'temperature'}:
            raise ValueError('Unsupported Adaption inference configuration')
        if 'temperature' in request['config']:
            value = request['config']['temperature']
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('temperature must be a finite nonnegative number')

    def send(self, request: dict, schema_id: str, execution_id: str) -> dict:
        # No environment fallback, path discovery, or credential in the request.
        key = self._secret_path.read_text(encoding='utf-8').strip()
        if not key or len(key.split()) != 1:
            raise ValueError('Expected one nonempty API key without whitespace')

        self.redactor.add(key)
        factory = self._client_factory
        if factory is None:
            from adaption import Adaption
            factory = Adaption
        try:
            sdk_version = version('adaption')
        except PackageNotFoundError:
            sdk_version = None
        input_text = canonical_json({'packet': request['packet'],
                                     'history': request['history']}).decode('utf-8')
        with factory(api_key=key, base_url=self.base_url,
                     timeout=self.timeout, max_retries=0) as client:
            response = client.responses.create(
                model=request['model'], instructions=request['instructions'],
                input=input_text, max_output_tokens=request['output_budget'],
                store=False, **request['config'])
            # Preserve every serialized provider field before any output parsing.
            raw = response.model_dump(mode='json')
            if not isinstance(raw, dict):
                raise TypeError('SDK response must serialize to an object')
            raw = copy.deepcopy(raw)
            # output_text can be an SDK-only convenience property.
            if 'output_text' not in raw and getattr(response, 'output_text', None) is not None:
                raw['output_text'] = response.output_text
            raw['_transport_metadata'] = {'sdk_version': sdk_version}
            return raw
