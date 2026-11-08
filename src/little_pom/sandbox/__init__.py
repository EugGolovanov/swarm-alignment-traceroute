"""Explicit synthetic or opt-in offline Docker sandboxes.

Docker images must be built separately with the owned Dockerfile overlay and pinned
by immutable image ID. No build, oracle, model call or host resource mutation occurs.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import copy
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from little_pom.contracts import sha256_hash, validate
from little_pom.tasks import IMAGE_INPUTS, RESOURCE_REQUIREMENTS, TASK_PATHS, incomplete, ok
from . import filesystem
from .docker import DockerCLI

DEFAULT_POLICY = {"timeout_sec": 30, "session_timeout_sec": 600, "output_limit_bytes": 65536,
                  "source_limit_bytes": 1048576, "workspace_mb": 256, "pids_limit": 64}

_CONTAINER_DISPATCH = '''
import base64, json, shutil, sys
request = json.load(sys.stdin)
try:
    operation = request['operation']
    root = '/work'
    if operation == 'bootstrap':
        for hidden in ('/tests', '/solution', '/authoring', '/secrets', '/build/generate_data.py',
                       '/build/out/ground_truth.json', '/workspace/ground_truth.json',
                       '/workspace/data/ground_truth.json', '/opt/little-inputs/solution',
                       '/opt/little-inputs/authoring', '/opt/little-inputs/tests'):
            if os.path.lexists(hidden):
                raise ValueError('Final agent image contains a forbidden private input path')
        hashes = {}
        task_id = request['task_id']
        source = '/opt/little-inputs' if task_id == 'finite-free-stam' else '/workspace/data'
        if task_id != 'dna-storage-codec':
            # Only the final agent-image stage is visible; no host corpus is mounted.
            for directory, dirs, files in os.walk(source, followlinks=False):
                for name in dirs + files:
                    candidate = os.path.join(directory, name)
                    if os.path.islink(candidate):
                        raise ValueError('Agent inputs contain a symlink')
                    if os.path.isfile(candidate) and os.stat(candidate).st_nlink != 1:
                        raise ValueError('Agent inputs contain a hardlink')
                    if task_id == 'nanoindentation-property-extraction' and name in files:
                        relative = os.path.relpath(candidate, source)
                        data = read_bytes(source, relative, request['limit'])
                        hashes['data/' + relative] = hashlib.sha256(data).hexdigest()
            if task_id == 'finite-free-stam':
                shutil.copytree(source, root, dirs_exist_ok=True)
                for relative in ('FiniteFree/Defs.lean', 'FiniteFree/Goal.lean', 'Tests/Public.lean',
                                 'lake-manifest.json', 'lakefile.lean', 'lean-toolchain'):
                    hashes[relative] = hashlib.sha256(read_bytes(root, relative, request['limit'])).hexdigest()
            else:
                # Read-only image measurements, not a writable copy mislabeled as source.
                os.symlink('/workspace/data', '/work/data')
        os.makedirs('/work/out', exist_ok=True)
        result = {'input_hashes': hashes}
    elif operation == 'read':
        if request['task_id'] == 'nanoindentation-property-extraction' and request['path'].startswith('data/'):
            if not os.path.islink('/work/data') or os.readlink('/work/data') != '/workspace/data':
                raise ValueError('Immutable measurement input alias was modified')
            root = '/workspace'
        result = {'bytes': base64.b64encode(read_bytes(root, request['path'], request['limit'])).decode('ascii')}
    elif operation == 'write':
        write_bytes(root, request['path'], base64.b64decode(request['bytes'], validate=True))
        result = {}
    elif operation == 'list':
        if request['task_id'] == 'nanoindentation-property-extraction' and request['path'].split('/')[0] == 'data':
            if not os.path.islink('/work/data') or os.readlink('/work/data') != '/workspace/data':
                raise ValueError('Immutable measurement input alias was modified')
            root = '/workspace'
        entries = list_files(root, request['path'], 4096)
        if request['task_id'] == 'nanoindentation-property-extraction' and request['path'] == '.':
            entries = sorted(set(entries) | {'data'})
        result = {'files': entries}
    else:
        raise ValueError('Unsupported filesystem operation')
    print(json.dumps({'ok': True, 'data': result}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
    sys.exit(1)
'''


def _policy(task: dict, policy: dict) -> dict:
    if not isinstance(policy, dict):
        raise ValueError("Sandbox policy must be an object")
    allowed = set(DEFAULT_POLICY) | {"cpus", "memory_mb", "worker_id", "workspace_id"}
    if set(policy) - allowed:
        raise ValueError("Unknown sandbox policy fields")
    result = {**DEFAULT_POLICY, **task["resource_requirements"], **policy}
    for field in (*DEFAULT_POLICY, "cpus", "memory_mb"):
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid resource bound: {field}")
    for field in ("output_limit_bytes", "source_limit_bytes", "workspace_mb", "pids_limit", "memory_mb"):
        if not isinstance(result[field], int):
            raise ValueError(f"Expected integer bound: {field}")
    if result["source_limit_bytes"] > 16 * 1024 * 1024 or result["output_limit_bytes"] > 1024 * 1024:
        raise ValueError("Evidence/output policy exceeds supported hard cap")
    if result["workspace_mb"] > task["resource_requirements"]["storage_mb"]:
        raise ValueError("Workspace exceeds task storage bound")
    if result["memory_mb"] < task["resource_requirements"]["memory_mb"] or result["cpus"] < task["resource_requirements"]["cpus"]:
        raise ValueError("Requested resources are below task requirements")
    return result


class Sandbox:
    def __init__(self, *, backend: str, fake_inputs: dict[str, str] | None = None,
                 docker_enabled: bool = False, images: dict[str, str] | None = None,
                 docker_binary: str = "docker", transport=None, workspace_root: str | Path | None = None):
        if backend not in {"fake", "docker"}:
            raise ValueError("Select the fake or docker backend explicitly")
        self.backend = backend
        self.fake_inputs = dict(fake_inputs or {})
        self.docker_enabled = docker_enabled
        self.images = dict(images or {})
        self.transport = transport or DockerCLI(docker_binary)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None
        if self.workspace_root is not None:
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, dict] = {}
        self._closed: set[str] = set()
        self._controller = Path(filesystem.__file__).read_text(encoding="utf-8") + _CONTAINER_DISPATCH

    def prepare(self, task: dict, policy: dict) -> dict:
        handle = None
        try:
            validate("Task", task)
            task = copy.deepcopy(task)
            if self.backend == "docker":
                if task["task_id"] not in TASK_PATHS:
                    raise ValueError("Task is outside the three-task pilot")
                if task["allowed_inputs"] != IMAGE_INPUTS[task["task_id"]]:
                    raise ValueError("Agent input descriptors differ from the fixed task allowlist")
                if task["resource_requirements"] != RESOURCE_REQUIREMENTS[task["task_id"]]:
                    raise ValueError("Task resource requirements differ from the selected corpus")
            else:
                # Explicit synthetic mode never reads task descriptors as host paths.
                task.setdefault("resource_requirements", {"cpus": 1, "memory_mb": 64, "storage_mb": 256})
            bounds = _policy(task, policy)
            handle = {"schema_version": "1.0", "id": uuid.uuid4().hex, "task_id": task["task_id"], "backend": self.backend}
            state = {"handle": handle, "task": copy.deepcopy(task), "policy": bounds,
                     "started": time.monotonic(), "input_hashes": {}, "immutable_inputs": set(), "root": None, "container": None}
            self._states[handle["id"]] = state
            if self.backend == "fake":
                state["root"] = tempfile.mkdtemp(prefix="little-pom-", dir=self.workspace_root)
                total = 0
                for path, content in self.fake_inputs.items():
                    path = "/".join(filesystem.parts_for(path))
                    if not isinstance(content, str):
                        raise ValueError("Synthetic inputs must be UTF-8 text")
                    data = content.encode("utf-8")
                    total += len(data)
                    if len(data) > bounds["source_limit_bytes"] or total > bounds["workspace_mb"] * 1024 * 1024:
                        raise ValueError("Synthetic input exceeds resource limits")
                    filesystem.write_bytes(state["root"], path, data)
                    state["input_hashes"][path] = filesystem.artifact(path, data, kind="input")["sha256"]
                    state["immutable_inputs"].add(path)
                Path(state["root"], "out").mkdir(exist_ok=True)
            else:
                self._prepare_docker(state)
            return ok(dict(handle))
        except Exception as exc:
            cleanup = self.close(handle) if handle is not None else None
            if cleanup is not None and cleanup["status"] != "ok":
                return incomplete("prepare_cleanup_failed", str(exc), {"cleanup": cleanup, "handle": handle})
            return incomplete("sandbox_prepare_failed", str(exc))

    def _prepare_docker(self, state: dict) -> None:
        if not self.docker_enabled:
            raise ValueError("Docker execution requires docker_enabled=True")
        task, bounds = state["task"], state["policy"]
        image = self.images.get(task["task_id"], "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("Supply an explicitly built immutable sha256 image ID")
        inspect = self.transport.run(["image", "inspect", image], timeout=30, output_limit=131072)
        if inspect["exit_status"] or inspect["truncated"]:
            raise ValueError("Agent image is unavailable or inspect evidence is incomplete")
        metadata = json.loads(inspect["stdout"])[0]
        labels = metadata.get("Config", {}).get("Labels") or {}
        expected = {"little_pom.task_id": task["task_id"], "little_pom.snapshot_hash": task["snapshot_hash"],
                    "little_pom.isolation_version": "1", "little_pom.dockerfile_sha256": task["agent_image"]["dockerfile_sha256"]}
        if metadata.get("Id") != image or any(labels.get(key) != value for key, value in expected.items()):
            raise ValueError("Image lacks matching explicit overlay provenance labels")
        info = self.transport.run(["info", "--format", "{{json .}}"], timeout=30, output_limit=131072)
        if info["exit_status"] or info["truncated"]:
            raise ValueError("Docker resource inspection failed")
        machine = json.loads(info["stdout"])
        if metadata.get("Config", {}).get("Volumes"):
            raise ValueError("Images declaring unmanaged writable volumes are not permitted")
        if machine.get("MemTotal", 0) < (bounds["memory_mb"] + 512) * 1024 * 1024:
            raise ValueError("Docker VM lacks requested RAM plus 512 MiB headroom")
        if machine.get("NCPU", 0) < bounds["cpus"]:
            raise ValueError("Docker VM lacks requested CPU allocation")
        normalize = {"aarch64": "arm64", "x86_64": "amd64"}
        arch = normalize.get(machine.get("Architecture"), machine.get("Architecture"))
        if metadata.get("Architecture") != arch:
            raise ValueError("Image architecture does not match Docker VM; emulation is not implicit")
        container = "little-pom-" + state["handle"]["id"]
        state["container"] = container
        args = ["run", "--detach", "--rm", "--no-healthcheck", "--pull=never", "--name", container,
                "--network=none", "--user=65532:65532", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true", "--ipc=none", "--pids-limit", str(bounds["pids_limit"]),
                "--cpus", str(bounds["cpus"]), "--memory", f"{bounds['memory_mb']}m",
                "--memory-swap", f"{bounds['memory_mb']}m", "--ulimit", "nofile=256:256",
                "--tmpfs", f"/work:rw,nosuid,nodev,size={bounds['workspace_mb']}m,uid=65532,gid=65532,mode=0700",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=65532,gid=65532,mode=0700",
                "--workdir=/work", "--env=HOME=/work", "--env=ELAN_HOME=/opt/elan",
                "--entrypoint=/usr/bin/env", image, "-i", "PATH=/opt/elan/bin:/usr/local/bin:/usr/bin:/bin",
                "HOME=/work", "ELAN_HOME=/opt/elan", "python3", "-I", "-S", "-c",
                f"import time; time.sleep({bounds['session_timeout_sec']!r})"]
        result = self.transport.run(args, timeout=30, output_limit=65536)
        if result["exit_status"] or result.get("timed_out"):
            raise ValueError("Docker container start failed")
        initial = self._filesystem(state, {"operation": "bootstrap"})
        state["input_hashes"] = initial["input_hashes"]
        if task["task_id"] == "nanoindentation-property-extraction":
            state["immutable_inputs"] = set(initial["input_hashes"])

    def _state(self, handle: dict) -> dict:
        if not isinstance(handle, dict) or not isinstance(handle.get("id"), str):
            raise ValueError("Invalid sandbox handle")
        state = self._states.get(handle["id"])
        if state is None or handle != state["handle"]:
            raise ValueError("Unknown or closed sandbox handle")
        if time.monotonic() - state["started"] > state["policy"]["session_timeout_sec"]:
            raise ValueError("Sandbox session deadline exceeded")
        return state

    def _filesystem(self, state: dict, request: dict) -> dict:
        limit = state["policy"]["source_limit_bytes"]
        request = {**request, "task_id": state["task"]["task_id"], "limit": limit}
        result = self.transport.run(["exec", "--interactive", "--user=65532:65532", state["container"],
                                    "/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/work",
                                    "python3", "-I", "-S", "-c", self._controller],
                                   stdin_data=json.dumps(request).encode("utf-8"),
                                   timeout=state["policy"]["timeout_sec"], output_limit=limit * 2 + 65536)
        if result.get("timed_out") or result["truncated"]:
            raise ValueError("Container filesystem response timed out or was truncated")
        response = json.loads(result["stdout"])
        if result["exit_status"] or not response.get("ok"):
            raise ValueError(response.get("error", "Container filesystem operation failed"))
        return response["data"]

    def read_source(self, handle: dict, path: str) -> bytes:
        try:
            state = self._state(handle)
            path = "/".join(filesystem.parts_for(path))
            if state["handle"]["backend"] == "fake":
                data = filesystem.read_bytes(state["root"], path, state["policy"]["source_limit_bytes"])
            else:
                result = self._filesystem(state, {"operation": "read", "path": path})
                data = base64.b64decode(result["bytes"], validate=True)
            if path in state["immutable_inputs"]:
                if filesystem.artifact(path, data, kind="input")["sha256"] != state["input_hashes"][path]:
                    raise ValueError("Immutable input content no longer matches its prepared SHA-256")
            return data
        except Exception:
            self.close(handle)
            raise

    def execute(self, handle: dict, tool_request: dict) -> dict:
        try:
            state = self._state(handle)
            if not isinstance(tool_request, dict):
                raise ValueError("Tool request must be an object")
            operation = tool_request.get("operation")
            fields = {"read": {"operation", "path"}, "write": {"operation", "path", "content"},
                      "list": {"operation", "path"}, "exec": {"operation", "argv", "artifacts"}}
            if operation not in fields or set(tool_request) - fields[operation]:
                raise ValueError("Unsupported tool request")
            stdout, stderr, exit_status, truncated, artifacts = "", "", 0, False, []
            if operation in {"read", "write"}:
                path = "/".join(filesystem.parts_for(tool_request["path"]))
                if operation == "write":
                    if not isinstance(tool_request.get("content"), str):
                        raise ValueError("Write content must be UTF-8 text")
                    if path in state["immutable_inputs"] or path.startswith("data/"):
                        raise ValueError("Disclosed input measurements are immutable")
                    data = tool_request["content"].encode("utf-8")
                    if len(data) > state["policy"]["source_limit_bytes"]:
                        raise ValueError("Write exceeds source byte limit")
                    if self.backend == "fake":
                        used = sum(p.stat().st_size for p in Path(state["root"]).rglob("*") if p.is_file() and not p.is_symlink())
                        if used + len(data) > state["policy"]["workspace_mb"] * 1024 * 1024:
                            raise ValueError("Workspace byte limit exceeded")
                        filesystem.write_bytes(state["root"], path, data)
                    else:
                        self._filesystem(state, {"operation": "write", "path": path, "bytes": base64.b64encode(data).decode("ascii")})
                data = self.read_source(handle, path)
                stdout = data.decode("utf-8") if operation == "read" else ""
                artifact = filesystem.artifact(path, data, kind="generated")
                if state["input_hashes"].get(path) == artifact["sha256"]:
                    artifact["kind"] = "input"
                artifacts.append(artifact)
            elif operation == "list":
                path = "/".join(filesystem.parts_for(tool_request.get("path", "."), allow_root=True)) or "."
                if self.backend == "fake":
                    entries = filesystem.list_files(state["root"], path, 4096)
                else:
                    entries = self._filesystem(state, {"operation": "list", "path": path})["files"]
                stdout = "\n".join(entries)
            else:
                argv = tool_request.get("argv")
                if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or "\x00" in a for a in argv):
                    raise ValueError("exec requires an argv array of strings")
                paths = tool_request.get("artifacts", [])
                if not isinstance(paths, list) or len(paths) > 128:
                    raise ValueError("Artifact paths must be a bounded list")
                paths = ["/".join(filesystem.parts_for(path)) for path in paths]
                if self.backend != "docker":
                    raise ValueError("Synthetic backend does not execute programs; select Docker explicitly")
                result = self.transport.run(["exec", "--user=65532:65532", "--workdir=/work", state["container"],
                                             "/usr/bin/env", "-i", "PATH=/opt/elan/bin:/usr/local/bin:/usr/bin:/bin",
                                             "HOME=/work", "ELAN_HOME=/opt/elan", *argv],
                                            timeout=state["policy"]["timeout_sec"], output_limit=state["policy"]["output_limit_bytes"])
                if result.get("timed_out"):
                    raise ValueError("Tool deadline exceeded; sandbox is closed")
                stdout, stderr, exit_status, truncated = (result[k] for k in ("stdout", "stderr", "exit_status", "truncated"))
                for path in paths:
                    data = self.read_source(handle, path)
                    artifact = filesystem.artifact(path, data, kind="generated")
                    if state["input_hashes"].get(path) == artifact["sha256"]:
                        artifact["kind"] = "input"
                    artifacts.append(artifact)
            limit = state["policy"]["output_limit_bytes"]
            for field, value in (("stdout", stdout), ("stderr", stderr)):
                raw = value.encode("utf-8")
                if len(raw) > limit:
                    truncated = True
                    clipped = raw[:limit].decode("utf-8", errors="ignore")
                    if field == "stdout":
                        stdout = clipped
                    else:
                        stderr = clipped
            data = {"schema_version": "1.0", "id": uuid.uuid4().hex, "task_id": state["task"]["task_id"],
                    "stdout": stdout, "stderr": stderr, "exit_status": exit_status, "artifacts": artifacts,
                    "provenance": {"backend": self.backend, "task_id": state["task"]["task_id"],
                                   "snapshot_hash": state["task"]["snapshot_hash"], "request_hash": sha256_hash(tool_request)},
                    "truncated": truncated}
            return ok(data)
        except Exception as exc:
            cleanup = self.close(handle)
            return incomplete("sandbox_execution_failed", str(exc), {"cleanup": cleanup})

    def close(self, handle: dict | None) -> dict:
        if not isinstance(handle, dict) or not isinstance(handle.get("id"), str):
            return incomplete("invalid_handle", "Invalid sandbox handle")
        identity = handle["id"]
        if identity in self._closed:
            return ok({"closed": True})
        state = self._states.get(identity)
        if state is None or state["handle"] != handle:
            return incomplete("invalid_handle", "Unknown sandbox handle")
        try:
            if state["container"]:
                result = self.transport.run(["rm", "--force", state["container"]], timeout=30, output_limit=65536)
                if result["exit_status"] and "No such container" not in result["stderr"]:
                    raise ValueError("Docker container cleanup failed; retry close with the same handle")
            if state["root"]:
                shutil.rmtree(state["root"])
            del self._states[identity]
            self._closed.add(identity)
            return ok({"closed": True})
        except Exception as exc:
            return incomplete("sandbox_cleanup_failed", str(exc))

    @contextmanager
    def session(self, task: dict, policy: dict):
        result = self.prepare(task, policy)
        if result["status"] != "ok":
            raise ValueError(result["error"]["message"])
        handle = result["data"]
        try:
            yield handle
        finally:
            closed = self.close(handle)
            if closed["status"] != "ok":
                raise RuntimeError(closed["error"]["message"])


from .preflight import preflight

__all__ = ["Sandbox", "DockerCLI", "preflight"]
