"""Immutable stage manifests are the sole authority for resumable execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from little_pom.contracts import canonical_json, sha256_hash
from little_pom.runtime import ArtifactStore


class StageError(ValueError):
    """A stage is out of order, modified, or incomplete."""


class StageStore:
    def __init__(self, root: str | Path, config: dict):
        self.root = Path(root)
        self.config = config
        self.config_hash = sha256_hash(config)
        self.artifacts = ArtifactStore(self.root)

    def load(self, stage: str) -> dict:
        path = self.root / 'manifests' / f'{stage}.json'
        if not path.is_file():
            raise StageError(f'Required stage has no manifest: {stage}')
        manifest = json.loads(path.read_text())
        if manifest['config_hash'] != self.config_hash:
            raise StageError(f'Frozen configuration changed: {stage}')
        for name, digest in manifest['outputs'].items():
            artifact = self.root / name
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                raise StageError(f'Stage artifact changed: {name}')
        for dependency, digest in manifest['dependencies'].items():
            if sha256_hash(self.load(dependency)) != digest:
                raise StageError(f'Stage dependency changed: {dependency}')
        return manifest

    def data(self, stage: str) -> dict:
        manifest = self.load(stage)
        return json.loads((self.root / manifest['result']).read_text())

    def run(self, stage: str, dependencies: tuple[str, ...], action: Callable[[], dict], *, inputs: dict | None = None) -> dict:
        dependency_hashes = {}
        for dependency in dependencies:
            manifest = self.load(dependency)
            if manifest['status'] != 'ok':
                raise StageError(f'Required stage is incomplete: {dependency}')
            dependency_hashes[dependency] = sha256_hash(manifest)
        input_hash = sha256_hash(inputs or {})
        path = self.root / 'manifests' / f'{stage}.json'
        if path.exists():
            manifest = self.load(stage)
            if manifest['input_hash'] != input_hash or manifest['dependencies'] != dependency_hashes:
                raise StageError(f'Resume inputs changed: {stage}')
            return self.data(stage)
        intent = {'schema_version': '1.0', 'id': sha256_hash([self.config_hash, stage]), 'stage': stage,
                  'config_hash': self.config_hash, 'input_hash': input_hash, 'dependencies': dependency_hashes}
        intent_path = f'intents/{stage}.json'
        if (self.root / intent_path).exists():
            if json.loads((self.root / intent_path).read_text()) != intent:
                raise StageError(f'Stage intent changed: {stage}')
        else:
            self.artifacts.put_once(intent_path, intent)
        result = action()
        if not isinstance(result, dict):
            raise StageError('Stage actions must return JSON objects')
        result_path = f'stages/{stage}.json'
        payload = canonical_json(result)
        existing = self.root / result_path
        if existing.exists():
            if existing.read_bytes() != payload:
                raise StageError(f'Orphaned stage result changed: {stage}')
        else:
            self.artifacts.put_once(result_path, payload)
        claimed = set()
        for previous in (self.root / 'manifests').glob('*.json'):
            claimed.update(json.loads(previous.read_text()).get('outputs', {}))
        outputs = {result_path: hashlib.sha256(payload).hexdigest()}
        for artifact in self.root.rglob('*'):
            if not artifact.is_file():
                continue
            relative = artifact.relative_to(self.root).as_posix()
            if relative in claimed or relative.startswith('manifests/') or artifact.suffix == '.lock':
                continue
            outputs[relative] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest = {**intent, 'status': result.get('status', 'ok'), 'result': result_path, 'outputs': outputs}
        self.artifacts.put_once(f'manifests/{stage}.json', manifest)
        return result
