"""Content-addressed evidence with independently verified, worker-local provenance."""
from __future__ import annotations

import json
import copy
import re
from pathlib import Path
from threading import RLock
from typing import Callable

from little_pom.contracts import canonical_json, evidence_id, sha256_bytes, validate
from little_pom.runtime.storage import ArtifactStore


class EvidenceRegistry:
    """Verify exact UTF-8 fragments, deduplicate identity, and persist provenance.

    ``source_root`` is an explicitly allowed local input tree. Sandboxed callers
    bind a separate read callback per worker instead; model text is never a source.
    Generated files are evidence about an artifact, not proof of its assertions.
    Registration returns the current observation's source and kind, even when
    the content identity already exists. ``evidence()`` contains one canonical
    representative per identity; worker-local observations live in provenance.
    """

    def __init__(self, root, task_id: str, source_root=None):
        self.store = ArtifactStore(root)
        self.root = self.store.root
        self.task_id = task_id
        self.source_root = Path(source_root).resolve() if source_root is not None else None
        self._readers: dict[str, Callable[[str], bytes]] = {}
        self._generated: set[tuple[str, str]] = set()
        self._lock = RLock()

    def bind_worker(self, worker_id: str, reader: Callable[[str], bytes]):
        with self._lock:
            if worker_id in self._readers:
                raise ValueError("Worker source reader is already bound")
            self._readers[worker_id] = reader

    def unbind_worker(self, worker_id: str):
        with self._lock:
            self._readers.pop(worker_id, None)

    def mark_generated(self, worker_id: str, source: str):
        with self._lock:
            self._generated.add((worker_id, self._source_path(source)))

    @staticmethod
    def _source_path(source: str) -> str:
        if not isinstance(source, str) or not source or Path(source).is_absolute() or ".." in Path(source).parts:
            raise ValueError("Evidence source must be an allowed relative path")
        return Path(source).as_posix()

    def _read_source(self, source: str, worker_id: str) -> bytes:
        source = self._source_path(source)
        if worker_id in self._readers:
            data = self._readers[worker_id](source)
        else:
            if self.source_root is None:
                raise ValueError("No verified source reader for this worker")
            candidate = self.source_root / source
            if candidate.is_symlink():
                raise ValueError("Symlink evidence sources are not allowed")
            path = candidate.resolve(strict=True)
            if not path.is_relative_to(self.source_root) or not path.is_file():
                raise ValueError("Evidence source escapes allowed inputs")
            data = path.read_bytes()
        if not isinstance(data, bytes):
            raise ValueError("Source readers must return exact bytes")
        return data

    def register(self, source: str, fragment: str, worker_id: str) -> dict:
        if not isinstance(fragment, str) or not fragment:
            raise ValueError("Evidence fragment must be nonempty text")
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("Evidence provenance requires a worker identity")
        source = self._source_path(source)
        with self._lock:
            raw = self._read_source(source, worker_id)
            text = raw.decode("utf-8", errors="strict")
            if fragment not in text:
                raise ValueError("Evidence fragment does not occur exactly in its source")
            source_hash = sha256_bytes(raw)
            snapshot = self.root / "sources" / source_hash
            try:
                self.store.put_once(f"sources/{source_hash}", raw)
            except FileExistsError:
                if sha256_bytes(snapshot.read_bytes()) != source_hash:
                    raise ValueError("Persisted evidence source snapshot has changed")
            fragment_hash = sha256_bytes(fragment.encode("utf-8"))
            identifier = evidence_id(source_hash, fragment_hash)
            kind = "generated" if (worker_id, source) in self._generated else "source"
            obj = {"schema_version": "1.0", "id": identifier, "task_id": self.task_id,
                   "evidence_id": identifier, "source": source, "source_sha256": source_hash,
                   "fragment": fragment, "fragment_sha256": fragment_hash, "kind": kind}
            validate("Evidence", obj)
            destination = self.root / "objects" / f"{identifier}.json"
            try:
                self._create(destination, obj)
            except FileExistsError:
                representative = json.loads(destination.read_text(encoding="utf-8"))
                validate("Evidence", representative)
                identity_fields = ("id", "evidence_id", "source_sha256", "fragment_sha256", "fragment", "task_id")
                if any(representative[key] != obj[key] for key in identity_fields):
                    raise ValueError("Persisted evidence object has changed")
            # Observation identity includes its source/kind, unlike evidence identity.
            key = sha256_bytes(canonical_json([worker_id, identifier, source, kind]))
            provenance = {"schema_version": "1.0", "id": key, "task_id": self.task_id,
                          "worker_id": worker_id, "evidence_id": identifier, "source": source,
                          "source_sha256": source_hash, "kind": kind}
            path = self.root / "provenance" / f"{key}.json"
            try:
                self._create(path, provenance)
            except FileExistsError:
                if path.read_bytes() != canonical_json(provenance):
                    raise ValueError("Persisted evidence provenance has changed")
            return json.loads(canonical_json(obj))

    def _create(self, path: Path, value: dict):
        self.store.put_once(path.relative_to(self.root), value)

    def evidence(self) -> list[dict]:
        with self._lock:
            return [json.loads(path.read_text(encoding="utf-8"))
                    for path in sorted((self.root / "objects").glob("*.json"))]

    def provenance(self, worker_id: str | None = None) -> list[dict]:
        with self._lock:
            records = [json.loads(path.read_text(encoding="utf-8"))
                       for path in sorted((self.root / "provenance").glob("*.json"))]
            return [record for record in records if worker_id is None or record["worker_id"] == worker_id]


class EvidenceReferenceError(ValueError):
    """A technical citation contract failure, never a scientific judgment."""


class EvidenceScope:
    """Request-local aliases over an explicitly authorized canonical evidence set.

    Ordering depends only on content identity, never condition, defect, worker,
    input order, or target membership. The host retains the mapping; the model
    selects [[ev:1]], [[ev:2]], ... . Full Evidence records retain source identity; lightweight
    projections must originate from verified worker/public boundaries.
    """

    def __init__(self, evidence, *, provenance=()):
        self.objects = {}
        for item in evidence:
            if not isinstance(item, dict):
                raise EvidenceReferenceError("Evidence must be an object")
            identifier = item.get("evidence_id")
            fragment = item.get("fragment", item.get("text"))
            if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
                raise EvidenceReferenceError("Authorized evidence requires a canonical identity")
            if not isinstance(fragment, str) or not fragment:
                raise EvidenceReferenceError("Authorized evidence requires an exact fragment")
            if "source_sha256" in item:
                validate("Evidence", item)
            if identifier in self.objects:
                previous = self.objects[identifier]
                if previous.get("fragment", previous.get("text")) != fragment:
                    raise EvidenceReferenceError("Conflicting fragments for one evidence identity")
            else:
                self.objects[identifier] = copy.deepcopy(item)
        self.aliases = {f"[[ev:{index}]]": identifier
                        for index, identifier in enumerate(sorted(self.objects), 1)}
        self.reverse = {identifier: alias for alias, identifier in self.aliases.items()}
        self.provenance = [copy.deepcopy(row) for row in provenance
                           if row["evidence_id"] in self.objects]

    def resolve(self, references):
        if not isinstance(references, list) or any(not isinstance(ref, str) for ref in references):
            raise EvidenceReferenceError("Evidence references must be a list of short IDs")
        if len(set(references)) != len(references):
            raise EvidenceReferenceError("Duplicate evidence reference")
        if any(ref not in self.aliases for ref in references):
            raise EvidenceReferenceError("Unknown or inaccessible evidence reference")
        return [self.aliases[ref] for ref in references]

    def _translate(self, value, replacements):
        if isinstance(value, dict):
            return {replacements.get(key, key): (
                        copy.deepcopy(item) if "evidence_id" in value and key in ("fragment", "text")
                        else self._translate(item, replacements))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._translate(item, replacements) for item in value]
        if isinstance(value, str) and replacements:
            pattern = r"(?<![A-Za-z0-9_])(?:" + "|".join(
                re.escape(key) for key in sorted(replacements, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
            return re.sub(pattern, lambda match: replacements[match.group()], value)
        return copy.deepcopy(value)

    def project(self, value):
        """Copy canonical host input into the model's request-local namespace."""
        return self._translate(value, self.reverse)

    def restore(self, value):
        """Restore known aliases in prose after validating structured citations."""
        return self._translate(value, self.aliases)

    def decode(self, value, *, field="evidence_ids"):
        if not isinstance(value, dict):
            raise EvidenceReferenceError("Citing output must be an object")
        resolved = self.resolve(value.get(field))
        result = self.restore(value)
        result[field] = resolved
        return result

    def manifest(self):
        """Private audit record; never include this mapping in a model packet."""
        return {"version": "short-evidence-v1", "aliases": dict(self.aliases),
                "evidence": [copy.deepcopy(self.objects[eid]) for eid in sorted(self.objects)],
                "provenance": copy.deepcopy(self.provenance)}
