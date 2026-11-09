"""Verified, worker-safe adapters for TBS and the five public SCI examples.

``TASK_PATHS`` retains the original three-task TBS selection. SCI is a separate
complete corpus, not an extension of a TBS snapshot. Manifest hashes cover the
original manifest without normalization; solver projections contain only the
instruction and public agent-image descriptors. SCI supplies no upstream
resource declaration, so its resource requirements are explicit local policy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid

from little_pom.contracts import sha256_hash, validate

TASK_PATHS = {
    "dna-storage-codec": "tasks/mathematical-sciences/applied-mathematics/dna-storage-codec",
    "finite-free-stam": "tasks/mathematical-sciences/formal-mathematics/finite-free-stam",
    "nanoindentation-property-extraction": "tasks/physical-sciences/materials-science/nanoindentation-property-extraction",
}
SCI_TASK_PATHS = {
    "bioinformatics-error-diagnosis-01": "tasks/bioinformatics/bioinformatics-error-diagnosis-01",
    "chemistry-result-interpretation-02": "tasks/chemistry/chemistry-result-interpretation-02",
    "materials-science-literature-synthesis-02": "tasks/materials-science/materials-science-literature-synthesis-02",
    "mathematics-code-generation-02": "tasks/mathematics/mathematics-code-generation-02",
    "physics-simulation-tool-orchestration-01": "tasks/physics-simulation/physics-simulation-tool-orchestration-01",
}
SCI_TASK_IDS = tuple(SCI_TASK_PATHS)
ALL_TASK_PATHS = {**TASK_PATHS, **SCI_TASK_PATHS}
RESOURCE_REQUIREMENTS = {
    "dna-storage-codec": {"cpus": 1, "memory_mb": 4096, "storage_mb": 10240},
    "finite-free-stam": {"cpus": 4, "memory_mb": 8192, "storage_mb": 20480},
    "nanoindentation-property-extraction": {"cpus": 4, "memory_mb": 8192, "storage_mb": 10240},
    **{task_id: {"cpus": 1, "memory_mb": 4096, "storage_mb": 10240} for task_id in SCI_TASK_IDS},
}
IMAGE_INPUTS = {
    "dna-storage-codec": [],
    "finite-free-stam": [{"path": "/app", "kind": "directory", "source": "agent_image"}],
    "nanoindentation-property-extraction": [{"path": "/workspace/data", "kind": "directory", "source": "agent_image"}],
    **{task_id: [{"path": "/home/agent/data", "kind": "directory", "source": "agent_image"}]
       for task_id in SCI_TASK_IDS},
}


def ok(data: dict) -> dict:
    return {"status": "ok", "data": data, "error": None}


def incomplete(code: str, message: str, data: dict | None = None) -> dict:
    return {"status": "incomplete", "data": data, "error": {"code": code, "message": message}}


def safe_file(root: Path, relative: str) -> Path:
    """Reject traversal and every symlink component, including in-root links."""
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or ".." in parts.parts or "\\" in relative:
        raise ValueError("Unsafe inventory path")
    candidate = root
    for part in parts.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("Symlinks are not accepted in snapshot inventory")
    if not candidate.is_file():
        raise ValueError("Inventory entry is not a regular file")
    return candidate


def inventory_file(root: Path, relative: str, inventory: dict) -> Path:
    """Resolve inventoried root metadata links only; task paths remain link-free.

    The frozen corpus intentionally has CLAUDE.md -> AGENTS.md. Every hop must
    be another inventoried root-level file, so links cannot enter private task
    material, escape the snapshot, or traverse untracked paths.
    """
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or ".." in parts.parts or "\\" in relative:
        raise ValueError("Unsafe inventory path")
    if len(parts.parts) != 1:
        return safe_file(root, relative)
    current = parts.as_posix()
    visited = set()
    while (root / current).is_symlink():
        if current in visited:
            raise ValueError("Repository metadata symlink cycle")
        visited.add(current)
        target = (root / current).readlink().as_posix()
        target_parts = PurePosixPath(target)
        if (target_parts.is_absolute() or len(target_parts.parts) != 1
                or ".." in target_parts.parts or "\\" in target):
            raise ValueError("Repository metadata symlink must target a contained root file")
        current = target_parts.as_posix()
        if current not in inventory:
            raise ValueError("Repository metadata symlink target is not inventoried")
    return safe_file(root, current)


def snapshot_task_ids(manifest: dict) -> tuple[str, ...]:
    """Return the complete selected corpus without rewriting its manifest.

    TBS uses ``tasks`` descriptors; the public SCI extract uses ``task_ids``.
    Mixed, duplicate, partial, and unknown selections are not accepted.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Snapshot manifest must be an object")
    if "task_ids" in manifest:
        task_ids = manifest["task_ids"]
        if ("tasks" in manifest or not isinstance(task_ids, list)
                or not all(isinstance(task_id, str) for task_id in task_ids)
                or len(task_ids) != len(SCI_TASK_IDS) or set(task_ids) != set(SCI_TASK_IDS)):
            raise ValueError("Snapshot must contain exactly the five public SCI tasks")
    else:
        tasks = manifest.get("tasks")
        if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
            raise ValueError("Snapshot requires task descriptors")
        task_ids = [item.get("task_id") for item in tasks]
        if (not all(isinstance(task_id, str) for task_id in task_ids)
                or len(task_ids) != len(TASK_PATHS) or set(task_ids) != set(TASK_PATHS)):
            raise ValueError("Snapshot must contain exactly the selected three TBS tasks")
    return tuple(task_ids)


def verify_snapshot(snapshot_root: str | Path) -> tuple[dict, Path]:
    root = Path(snapshot_root).resolve(strict=True)
    manifest_path = safe_file(root, "snapshot_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    task_ids = snapshot_task_ids(manifest)
    source = root / "upstream"
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Snapshot requires a real upstream directory")
    inventory = manifest.get("files_sha256")
    count_key = "tracked_file_count" if "task_ids" in manifest else "file_count"
    if not isinstance(inventory, dict) or not inventory or manifest.get(count_key) != len(inventory):
        raise ValueError("Invalid snapshot inventory count")
    if manifest.get("unresolved_lfs_pointers"):
        raise ValueError("Snapshot contains unresolved LFS pointers")
    for item in manifest.get("tasks", []):
        task_id = item["task_id"]
        if item.get("path") != TASK_PATHS[task_id]:
            raise ValueError("Unexpected task directory")
        for key, expected in RESOURCE_REQUIREMENTS[task_id].items():
            if item.get("environment", {}).get(key) != expected:
                raise ValueError("Resource metadata differs from selected snapshot")
    for relative, expected in inventory.items():
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("Malformed inventory digest")
        path = inventory_file(source, relative, inventory)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected:
            raise ValueError("Snapshot SHA-256 mismatch")
    # Extra task files are rejected, rather than silently trusting an incomplete inventory.
    for task_id in task_ids:
        directory = source / ALL_TASK_PATHS[task_id]
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Missing or linked task directory")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ValueError("Task directory contains a symlink")
            if path.is_file() and path.relative_to(source).as_posix() not in inventory:
                raise ValueError("Task contains an untracked file")
    return manifest, source


class TaskAdapter:
    """Hash the actual inventory; project only instruction and public image descriptors."""

    def load(self, task_id: str, snapshot_root: str | Path) -> dict:
        if task_id not in ALL_TASK_PATHS:
            return incomplete("task_not_allowed", "Task is outside the selected TBS and public SCI corpora")
        try:
            manifest, source = verify_snapshot(snapshot_root)
            if task_id not in snapshot_task_ids(manifest):
                return incomplete("task_not_allowed", "Task is not part of this snapshot")
            prefix = ALL_TASK_PATHS[task_id]
            instruction_path = f"{prefix}/instruction.md"
            dockerfile_path = f"{prefix}/environment/Dockerfile"
            if instruction_path not in manifest["files_sha256"] or dockerfile_path not in manifest["files_sha256"]:
                raise ValueError("Instruction or agent Dockerfile is not inventoried")
            instruction_bytes = safe_file(source, instruction_path).read_bytes()
            if hashlib.sha256(instruction_bytes).hexdigest() != manifest["files_sha256"][instruction_path]:
                raise ValueError("Instruction changed after inventory verification")
            instruction = instruction_bytes.decode("utf-8")
            task = {
                "schema_version": "1.0", "id": uuid.uuid4().hex, "task_id": task_id,
                "goal": instruction, "instruction": instruction,
                "allowed_inputs": [dict(item) for item in IMAGE_INPUTS[task_id]],
                "snapshot_hash": sha256_hash(manifest),
                "resource_requirements": dict(RESOURCE_REQUIREMENTS[task_id]),
                "agent_image": {"context": "environment", "dockerfile_sha256": manifest["files_sha256"][dockerfile_path],
                                "build_network": "required", "runtime_network": "none"},
                "constraints": ["Offline non-root worker runtime", "No verifier, solution, authoring or generator inputs",
                                "Scientific readiness requires separate opt-in verification"],
            }
            validate("Task", task)
            return ok(task)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return incomplete("snapshot_invalid", str(exc))


__all__ = [
    "TaskAdapter", "verify_snapshot", "snapshot_task_ids", "TASK_PATHS",
    "SCI_TASK_PATHS", "SCI_TASK_IDS", "ALL_TASK_PATHS", "RESOURCE_REQUIREMENTS", "IMAGE_INPUTS",
]
