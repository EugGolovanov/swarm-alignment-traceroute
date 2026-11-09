"""Single-dispatch subscription transport using the installed Pi provider, not an agent."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from little_pom.contracts import canonical_json
from .backends import Redactor


LUNA_MODEL = 'openai-codex/gpt-6-luna'
_POLICY_PATH = Path(__file__).with_name('subscription_policy.json')
ROLE_SCHEMA_POLICY = json.loads(_POLICY_PATH.read_text(encoding='utf-8'))
SUPPORTED_MODELS = frozenset(model for policy in ROLE_SCHEMA_POLICY.values() for model in policy['models'])
PI_VERSION = '1.5.0'
BRIDGE_PROTOCOL = 'little-pom-pi-subscription/1'


def validate_role_schema(request: dict, schema_id: str, *, allow_adaption: bool = False) -> None:
    """Reject role/schema/model mismatches before persisting dispatch intent."""
    policy = ROLE_SCHEMA_POLICY.get(request.get('stage'))
    legacy = (allow_adaption and request.get('model') == 'qwen3.5-397b'
              and (request.get('stage') in ('author', 'reviews')
                   or (request.get('stage') == 'transport-smoke' and schema_id == 'Review')))
    # Historical Adaption callers may supply custom local validators/schemas.
    if legacy:
        return
    if policy is None or schema_id != policy['schema']:
        raise ValueError('Schema does not match the frozen model role')
    if request.get('model') not in policy['models']:
        raise ValueError('Model does not match the frozen role')
    if request.get('config') != {'reasoning_effort': 'medium'}:
        raise ValueError('Subscription roles require exactly medium reasoning effort')


def validate_upstream_messages(request: dict) -> None:
    """Permit exact saved Anthropic user blocks only for the scientific judge."""
    messages = request.get('upstream_messages')
    if request.get('stage') != 'scientific-judge':
        if 'upstream_messages' in request:
            raise ValueError('Upstream messages are restricted to scientific grading')
        return
    instructions = request.get('instructions')
    if not isinstance(instructions, str) or not instructions.strip() or request.get('packet') != {} or request.get('history') != []:
        raise ValueError('Scientific grading requires exact upstream guidance and no extra packet/history')
    if not isinstance(messages, list) or len(messages) != 1:
        raise ValueError('Exactly one preserved upstream user message is required')
    message = messages[0]
    if not isinstance(message, dict) or set(message) != {'role', 'content'} or message['role'] != 'user':
        raise ValueError('Unsupported upstream message')
    if not isinstance(message['content'], list) or not message['content']:
        raise ValueError('Nonempty upstream content is required')
    for block in message['content']:
        if not isinstance(block, dict):
            raise ValueError('Invalid upstream content block')
        if block.get('type') == 'text':
            if set(block) != {'type', 'text'} or not isinstance(block['text'], str):
                raise ValueError('Invalid upstream text block')
        elif block.get('type') == 'image':
            source = block.get('source')
            if (set(block) != {'type', 'source'} or not isinstance(source, dict)
                    or set(source) != {'type', 'media_type', 'data'} or source['type'] != 'base64'
                    or source['media_type'] not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
                    or not isinstance(source['data'], str) or not source['data']):
                raise ValueError('Unsupported upstream image source')
            try:
                decoded = base64.b64decode(source['data'], validate=True)
            except (ValueError, binascii.Error):
                raise ValueError('Invalid upstream base64 image') from None
            if not decoded or base64.b64encode(decoded).decode('ascii') != source['data']:
                raise ValueError('Noncanonical upstream base64 image')
        else:
            raise ValueError('Unsupported upstream content block')
    marker = '## Rubric criteria\n'
    first = message['content'][0]
    if (first['type'] != 'text' or marker in instructions
            or first['text'].count(marker) != 1 or not first['text'].startswith(instructions + marker)):
        raise ValueError('Scientific guidance must be the exact unambiguous upstream prefix before the rubric')


def _digest(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _installation_manifest(release: Path) -> str:
    # Freeze executable JS and provider data, including transitive dependencies.
    # Source maps/docs do not execute and are deliberately excluded.
    entries = []
    for path in sorted((release / 'node_modules').rglob('*')):
        if path.is_file() and not path.is_symlink() and path.suffix in {'.js', '.mjs', '.cjs', '.json', '.node', '.wasm'}:
            entries.append([path.relative_to(release).as_posix(), _digest(path)])
    if not entries:
        raise ValueError('Installed Pi runtime is unavailable')
    return hashlib.sha256(canonical_json(entries)).hexdigest()


class OmpSubscriptionBackend:
    """Use the connected Pi OAuth login; never consult paid API-key variables.

    Construction inspects only local executable/package/catalog metadata. OAuth
    credentials are opened in the child only after SwarmClient persists intent.
    No CLI agent, tools, extension discovery, session, repair, or fallback runs.
    """

    def __init__(self, *, timeout: float | None = None, model: str = LUNA_MODEL):
        if model not in SUPPORTED_MODELS:
            raise ValueError('Unsupported explicit subscription model')
        self.model = model
        model_id = model.split('/', 1)[1]
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('timeout must be None or a finite positive number')
        self.timeout = None if timeout is None else float(timeout)
        self.redactor = Redactor()
        self._agent_dir = Path.home() / '.pi' / 'agent'
        installed = (self._agent_dir / 'install' / 'current-version').read_text().strip()
        if installed != PI_VERSION:
            raise ValueError('Subscription bridge requires the reviewed Pi 1.5.0 installation')
        self._release = self._agent_dir / 'install' / 'releases' / installed
        self._ai = self._release / 'node_modules' / '@earendil-works' / 'pi-ai'
        self._agent = self._release / 'node_modules' / '@earendil-works' / 'pi-coding-agent'
        for package in (self._ai, self._agent):
            if json.loads((package / 'package.json').read_text())['version'] != PI_VERSION:
                raise ValueError('Installed Pi package version does not match the reviewed bridge')
        executable = shutil.which('node')
        if not executable:
            raise ValueError('The installed subscription bridge requires Node >=22.19.0')
        self._node = Path(executable).resolve()
        self._bridge = Path(__file__).with_name('omp_subscription_bridge.mjs')
        catalog = json.loads((self._ai / 'dist/providers/data/openai-codex.json').read_text())
        self._model = catalog['openai-codex-responses'][f'chat:{model_id}']
        if (self._model.get('id') != model_id or self._model.get('provider') != 'openai-codex'
                or self._model.get('baseUrl') != 'https://chatgpt.com/backend-api'
                or self._model.get('api') != 'openai-codex-responses'
                or self._model.get('thinkingLevelMap', {}).get('medium') != 'medium'):
            raise ValueError('The exact subscription model is not in the installed catalog')
        self._configuration = {
            'backend': 'omp_subscription', 'mode': 'subscription', 'model': self.model,
            'parameters': {'reasoning_effort': 'medium'}, 'timeout': self.timeout,
            'max_retries': 0, 'generation_dispatch_limit': 1, 'transport': 'sse',
            'store': False, 'tools': [], 'ambient_context': False,
            'output_limit_field': 'max_output_tokens', 'output_limit_policy': 'reject_if_unsupported',
            'credential_source': 'connected_pi_oauth', 'credential_provider': 'openai-codex',
            'bridge_protocol': BRIDGE_PROTOCOL, 'bridge_sha256': _digest(self._bridge),
            'role_schema_policy': copy.deepcopy(ROLE_SCHEMA_POLICY),
            'role_schema_policy_sha256': _digest(_POLICY_PATH),
            'sdk_package': '@earendil-works/pi-ai', 'sdk_version': PI_VERSION,
            'runtime_executable': str(self._node), 'runtime_sha256': _digest(self._node),
            'installed_release': str(self._release),
            'installation_sha256': _installation_manifest(self._release),
            'catalog_model': copy.deepcopy(self._model),
            'raw_observation': 'parsed_provider_sse_events_and_pi_messages_not_wire_bytes',
        }

    def configuration(self) -> dict:
        return copy.deepcopy(self._configuration)

    def validate_request(self, request: dict) -> None:
        if request.get('model') != self._configuration['model']:
            raise ValueError('Subscription model must match the frozen selection')
        policy = ROLE_SCHEMA_POLICY.get(request.get('stage'))
        validate_role_schema(request, policy['schema'] if policy else '')
        validate_upstream_messages(request)
        if request.get('upstream_messages') and any(
                block['type'] == 'image' for block in request['upstream_messages'][0]['content']):
            if 'image' not in self._model.get('input', []):
                raise ValueError('Selected subscription model cannot receive images')
        budget = request.get('output_budget')
        if type(budget) is not int or not 1 <= budget <= self._model['maxTokens']:
            raise ValueError('Output budget exceeds the subscription model catalog limit')

    def send(self, request: dict, schema_id: str, execution_id: str) -> dict:
        self.validate_request(request)
        validate_role_schema(request, schema_id)
        if (_digest(self._bridge) != self._configuration['bridge_sha256']
                or _digest(_POLICY_PATH) != self._configuration['role_schema_policy_sha256']
                or _digest(self._node) != self._configuration['runtime_sha256']
                or _installation_manifest(self._release) != self._configuration['installation_sha256']):
            raise ValueError('Frozen subscription runtime changed before dispatch')
        payload = {
            'protocol': BRIDGE_PROTOCOL, 'request': request, 'schema_id': schema_id,
            'execution_id': execution_id, 'model': self._model,
            'pi_ai_dir': str(self._ai), 'pi_agent_dir': str(self._agent),
            'auth_file': str(self._agent_dir / 'auth.json'),
            'timeout_ms': None if self.timeout is None else max(1, math.ceil(self.timeout * 1000)),
        }
        # Do not inherit NODE_OPTIONS, provider keys, proxy overrides, extensions,
        # or project cwd. Neither prompts nor credentials appear in argv.
        environment = {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
        with tempfile.TemporaryDirectory(prefix='little-pom-subscription-') as cwd:
            process = subprocess.run(
                [str(self._node), str(self._bridge)], input=canonical_json(payload),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=environment,
                timeout=None if self.timeout is None else self.timeout + 30, check=False)
        # Never relay stderr or parse failure bodies into operator-visible errors.
        if process.returncode != 0:
            raise RuntimeError('Subscription bridge terminated without a durable response')
        try:
            result = json.loads(process.stdout)
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError('Subscription bridge returned an invalid envelope') from None
        if not isinstance(result, dict) or result.get('_transport_metadata', {}).get('bridge_protocol') != BRIDGE_PROTOCOL:
            raise RuntimeError('Subscription bridge protocol mismatch')
        return self.redactor.clean(result)
