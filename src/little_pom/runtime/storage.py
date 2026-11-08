"""Durable, append-only host storage for model executions and stage freezes."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from little_pom.contracts import canonical_json, sha256_hash


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_path(root: Path, relative: str | Path) -> Path:
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise ValueError('Artifact path must be relative and remain inside its root')
    target = root / relative
    for ancestor in [target, *target.parents]:
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise ValueError('Symlinks are not permitted in artifact paths')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Artifact path escapes its root')
    return target


def _mkdir_durable(path: Path, root: Path) -> None:
    if path == root:
        if not path.is_dir():
            raise NotADirectoryError(path)
        return
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(path)
        # Another constructor can observe a directory between mkdir and its
        # creator's fsync. Flush that link before treating it as durable.
        _sync_directory(path.parent)
        return
    _mkdir_durable(path.parent, root)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not path.is_dir():
            raise NotADirectoryError(path)
    _sync_directory(path.parent)


class ArtifactStore:
    """Publish complete files atomically without ever overwriting an artifact."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        # The storage root itself is part of the durable intent chain: syncing
        # only descendants would lose the entire ledger after a power failure.
        _mkdir_durable(self.root, Path(self.root.anchor))

    def put_once(self, path: str | Path, payload: object) -> Path:
        destination = _safe_path(self.root, path)
        _mkdir_durable(destination.parent, self.root)
        if isinstance(payload, bytes):
            content = payload
        elif isinstance(payload, str):
            content = payload.encode('utf-8')
        else:
            content = canonical_json(payload)
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fchmod(stream.fileno(), 0o400)
                os.fsync(stream.fileno())
            # Unlike replace/rename, link fails atomically if destination exists.
            os.link(temporary, destination)
            _sync_directory(destination.parent)
        finally:
            os.unlink(temporary)
        return destination

    def read(self, path: str | Path) -> object:
        return json.loads(_safe_path(self.root, path).read_bytes())


class Ledger:
    """One locked intent/response/result chain per opaque execution identity."""

    def __init__(self, root: str | Path):
        self.store = ArtifactStore(root)
        self.root = self.store.root
        _mkdir_durable(self.root / 'locks', self.root)

    @staticmethod
    def key(execution_id: str) -> str:
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError('execution_id must be a nonempty string')
        return hashlib.sha256(execution_id.encode('utf-8')).hexdigest()

    @contextmanager
    def lock(self, execution_id: str) -> Iterator[None]:
        path = _safe_path(self.root, f'locks/{self.key(execution_id)}.lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def lookup(self, execution_id: str) -> dict:
        key = self.key(execution_id)
        records = {}
        for kind in ('intent', 'response', 'result'):
            path = f'{kind}/{key}.json'
            try:
                records[kind] = self.store.read(path)
            except FileNotFoundError:
                records[kind] = None
        result = records['result']
        records['status'] = result['status'] if result else (
            'response_received' if records['response'] else
            'incomplete' if records['intent'] else 'absent'
        )
        return records

    def write_record(self, kind: str, execution_id: str, record: dict) -> None:
        if kind not in ('intent', 'response', 'result'):
            raise ValueError('Unknown ledger record kind')
        self.store.put_once(f'{kind}/{self.key(execution_id)}.json', record)
        self.audit({'event': kind, 'execution_id': execution_id,
                    'request_hash': record.get('request_hash'),
                    'status': record.get('status')})

    def audit(self, event: dict) -> None:
        # Independent immutable event files are an append-only audit stream;
        # there is no shared JSONL tail that a process crash can truncate.
        import uuid
        self.store.put_once(f'audit/{uuid.uuid4().hex}.json', event)


class FreezeStore:
    """Freeze canonical manifests whose file hashes cover exact artifact bytes."""

    def __init__(self, root: str | Path):
        self.store = ArtifactStore(root)
        self.root = self.store.root

    @staticmethod
    def _validate(manifest: dict) -> None:
        if not isinstance(manifest, dict) or manifest.get('schema_version') != '1.0':
            raise ValueError('Freeze manifest requires schema_version 1.0')
        if not isinstance(manifest.get('id'), str) or not manifest['id']:
            raise ValueError('Freeze manifest requires an opaque id')
        if not isinstance(manifest.get('files'), dict) or not manifest['files']:
            raise ValueError('Freeze manifest requires nonempty files mapping')
        for path, digest in manifest['files'].items():
            if not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64:
                raise ValueError('Invalid freeze file/hash')
            if any(character not in '0123456789abcdef' for character in digest):
                raise ValueError('Invalid SHA-256 digest')
        canonical_json(manifest)

    def _matches_files(self, manifest: dict) -> bool:
        return all(hashlib.sha256(_safe_path(self.root, path).read_bytes()).hexdigest() == digest
                   for path, digest in manifest['files'].items())

    def _path(self, manifest: dict) -> str:
        return f'.freezes/{hashlib.sha256(manifest["id"].encode()).hexdigest()}.json'

    def create(self, manifest: dict) -> dict:
        self._validate(manifest)
        if not self._matches_files(manifest):
            raise ValueError('Freeze manifest does not match artifact bytes')
        self.store.put_once(self._path(manifest), manifest)
        return json.loads(canonical_json(manifest))

    def verify(self, manifest: dict) -> bool:
        try:
            self._validate(manifest)
            frozen = self.store.read(self._path(manifest))
            return sha256_hash(frozen) == sha256_hash(manifest) and self._matches_files(manifest)
        except (OSError, ValueError, TypeError, KeyError):
            return False
