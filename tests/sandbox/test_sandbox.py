import base64
import copy
import json
import os
from pathlib import Path
import shutil

import pytest

from little_pom.sandbox import Sandbox
from little_pom.sandbox.filesystem import read_bytes
from little_pom.tasks import IMAGE_INPUTS, RESOURCE_REQUIREMENTS


@pytest.fixture
def task():
    task_id = "dna-storage-codec"
    return {"schema_version": "1.0", "id": "synthetic-task", "task_id": task_id,
            "goal": "Read synthetic measurements", "instruction": "Read synthetic measurements",
            "allowed_inputs": copy.deepcopy(IMAGE_INPUTS[task_id]), "snapshot_hash": "a" * 64,
            "resource_requirements": dict(RESOURCE_REQUIREMENTS[task_id]), "constraints": [],
            "agent_image": {"context": "environment", "dockerfile_sha256": "b" * 64,
                            "build_network": "required", "runtime_network": "none"}}


def prepare(sandbox, task, policy=None):
    result = sandbox.prepare(task, policy or {})
    assert result["status"] == "ok", result
    return result["data"]


def test_private_per_worker_workspaces_and_exact_utf8(task, tmp_path):
    sandbox = Sandbox(backend="fake", fake_inputs={"input.txt": "measurement α\n"}, workspace_root=tmp_path)
    first, second = prepare(sandbox, task), prepare(sandbox, task)
    assert first["id"] != second["id"]
    result = sandbox.execute(first, {"operation": "read", "path": "input.txt"})["data"]
    assert result["stdout"] == "measurement α\n"
    assert result["artifacts"][0]["kind"] == "input"
    assert result["provenance"]["backend"] == "fake"
    assert not result["truncated"]
    sandbox.execute(first, {"operation": "write", "path": "out/a.txt", "content": "one"})
    assert sandbox.execute(second, {"operation": "read", "path": "out/a.txt"})["status"] == "incomplete"
    assert sandbox.read_source(first, "out/a.txt") == b"one"
    sandbox.close(first)
    assert list(tmp_path.iterdir()) == []
    assert sandbox.close(first)["status"] == "ok"


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "out/../../secret", "tests/secret.txt", "solution/a.py",
                                  "authoring/data", "generate_data.py", "ground_truth.json", "out\\..\\secret"])
def test_unsafe_paths_fail_closed_and_cleanup(task, tmp_path, path):
    sandbox = Sandbox(backend="fake", workspace_root=tmp_path)
    handle = prepare(sandbox, task)
    result = sandbox.execute(handle, {"operation": "read", "path": path})
    assert result["status"] == "incomplete"
    assert result["data"]["cleanup"]["status"] == "ok"
    assert list(tmp_path.iterdir()) == []


def test_symlink_cannot_read_hidden_fixture_and_cleanup_does_not_follow(task, tmp_path):
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("not worker evidence")
    roots = tmp_path / "workspaces"
    sandbox = Sandbox(backend="fake", workspace_root=roots)
    handle = prepare(sandbox, task)
    root = Path(sandbox._states[handle["id"]]["root"])
    (root / "leak").symlink_to(hidden, target_is_directory=True)
    result = sandbox.execute(handle, {"operation": "read", "path": "leak/secret.txt"})
    assert result["status"] == "incomplete"
    assert "not worker evidence" not in json.dumps(result)
    assert (hidden / "secret.txt").read_text() == "not worker evidence"
    assert not root.exists()


def test_hardlink_is_not_evidence(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("private")
    root = tmp_path / "workspace"
    root.mkdir()
    os.link(secret, root / "linked")
    with pytest.raises(ValueError, match="single-link"):
        read_bytes(str(root), "linked", 1024)


def test_symlink_write_is_rejected_without_modifying_target(task, tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    sandbox = Sandbox(backend="fake")
    handle = prepare(sandbox, task)
    root = Path(sandbox._states[handle["id"]]["root"])
    (root / "out" / "escape").symlink_to(outside)
    result = sandbox.execute(handle, {"operation": "write", "path": "out/escape", "content": "attack"})
    assert result["status"] == "incomplete"
    assert outside.read_text() == "private"


def test_output_loss_is_explicit_and_source_can_be_read_independently(task):
    sandbox = Sandbox(backend="fake", fake_inputs={"input.txt": "abcdefgh"})
    handle = prepare(sandbox, task, {"output_limit_bytes": 4})
    result = sandbox.execute(handle, {"operation": "read", "path": "input.txt"})["data"]
    assert result["stdout"] == "abcd"
    assert result["truncated"]
    assert result["artifacts"][0]["size_bytes"] == 8
    assert sandbox.read_source(handle, "input.txt") == b"abcdefgh"
    sandbox.close(handle)


def test_fake_never_executes_shell_and_closes_on_failure(task, tmp_path):
    sandbox = Sandbox(backend="fake", workspace_root=tmp_path)
    handle = prepare(sandbox, task)
    assert sandbox.execute(handle, {"operation": "exec", "argv": ["touch", "/tmp/unwanted"]})["status"] == "incomplete"
    assert not sandbox._states
    assert not list(tmp_path.iterdir())


def test_session_guarantees_cleanup_on_caller_error(task, tmp_path):
    sandbox = Sandbox(backend="fake", workspace_root=tmp_path)
    with pytest.raises(RuntimeError, match="caller failure"):
        with sandbox.session(task, {}):
            raise RuntimeError("caller failure")
    assert not sandbox._states
    assert list(tmp_path.iterdir()) == []


def test_prepare_failure_also_removes_workspace(task, tmp_path):
    sandbox = Sandbox(backend="fake", fake_inputs={"../hidden": "bad"}, workspace_root=tmp_path)
    assert sandbox.prepare(task, {})["status"] == "incomplete"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("policy", [{"memory_mb": 1024}, {"timeout_sec": 0}, {"output_limit_bytes": -1},
                                    {"cpus": False}, {"network": "public"}])
def test_resource_and_network_bounds(task, policy):
    sandbox = Sandbox(backend="fake")
    assert sandbox.prepare(task, policy)["status"] == "incomplete"


class RecordingDocker:
    def __init__(self, task, *, ram_gib=16, architecture="arm64", timeout_exec=False, fail_bootstrap=False):
        self.task = task
        self.ram_gib = ram_gib
        self.architecture = architecture
        self.timeout_exec = timeout_exec
        self.fail_bootstrap = fail_bootstrap
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        result = {"stdout": "", "stderr": "", "exit_status": 0, "truncated": False, "timed_out": False}
        if args[:2] == ["image", "inspect"]:
            result["stdout"] = json.dumps([{"Id": "sha256:" + "c" * 64, "Architecture": self.architecture,
                "Config": {"Labels": {"little_pom.task_id": self.task["task_id"],
                "little_pom.snapshot_hash": self.task["snapshot_hash"], "little_pom.isolation_version": "1",
                "little_pom.dockerfile_sha256": self.task["agent_image"]["dockerfile_sha256"]}}}])
        elif args[0] == "info":
            result["stdout"] = json.dumps({"MemTotal": int(self.ram_gib * 1024 ** 3), "NCPU": 8, "Architecture": "aarch64"})
        elif args[0] == "exec" and "-c" in args:
            request = json.loads(kwargs["stdin_data"])
            if request["operation"] == "bootstrap" and self.fail_bootstrap:
                result.update(stdout=json.dumps({"ok": False, "error": "injected bootstrap failure"}), exit_status=1)
            elif request["operation"] == "bootstrap":
                result["stdout"] = json.dumps({"ok": True, "data": {"input_hashes": {}}})
            elif request["operation"] == "read":
                result["stdout"] = json.dumps({"ok": True, "data": {"bytes": base64.b64encode(b"measured").decode()}})
            else:
                result["stdout"] = json.dumps({"ok": True, "data": {}})
        elif args[0] == "exec":
            result.update(stdout="bounded output", timed_out=self.timeout_exec)
        return result


def docker_sandbox(task, transport, **kwargs):
    return Sandbox(backend="docker", docker_enabled=True,
                   images={task["task_id"]: "sha256:" + "c" * 64}, transport=transport, **kwargs)


def test_docker_is_opt_in_and_never_auto_builds(task):
    transport = RecordingDocker(task)
    sandbox = Sandbox(backend="docker", transport=transport)
    assert sandbox.prepare(task, {})["status"] == "incomplete"
    assert transport.calls == []


def test_real_backend_builds_strict_nonroot_offline_command(task):
    transport = RecordingDocker(task)
    sandbox = docker_sandbox(task, transport)
    handle = prepare(sandbox, task)
    launch = next(args for args, _ in transport.calls if args[0] == "run")
    for flag in ("--network=none", "--user=65532:65532", "--read-only", "--cap-drop=ALL", "--pull=never",
                 "--security-opt=no-new-privileges:true", "--rm", "--no-healthcheck"):
        assert flag in launch
    assert not any("docker.sock" in arg or "--volume" in arg or "--mount" in arg for arg in launch)
    assert "--cpus" in launch and "--memory" in launch and "--pids-limit" in launch
    result = sandbox.execute(handle, {"operation": "exec", "argv": ["python3", "-V"]})
    assert result["data"]["stdout"] == "bounded output"
    sandbox.close(handle)
    assert transport.calls[-1][0][:2] == ["rm", "--force"]
    assert all(args[0] not in {"build", "pull"} for args, _ in transport.calls)


@pytest.mark.parametrize("failure", ["timeout", "bootstrap"])
def test_docker_cleanup_after_transport_or_bootstrap_failure(task, failure):
    transport = RecordingDocker(task, timeout_exec=failure == "timeout", fail_bootstrap=failure == "bootstrap")
    sandbox = docker_sandbox(task, transport)
    result = sandbox.prepare(task, {})
    if failure == "timeout":
        result = sandbox.execute(result["data"], {"operation": "exec", "argv": ["sleep", "999"]})
    assert result["status"] == "incomplete"
    assert transport.calls[-1][0][0] == "rm"
    assert not sandbox._states


def test_arm64_mismatch_and_low_ram_are_not_silently_ignored(task):
    for transport in (RecordingDocker(task, architecture="amd64"), RecordingDocker(task, ram_gib=4)):
        sandbox = docker_sandbox(task, transport)
        assert sandbox.prepare(task, {})["status"] == "incomplete"
        assert all(args[0] != "run" for args, _ in transport.calls)


def test_dna_directory_symlink_regression_is_local_not_oracle(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/dna_symlink_verifier.json").read_text())
    assert fixture["upstream_status"] == "unresolved"
    target = tmp_path / "private-fixture"
    target.mkdir()
    (target / "secret").write_text("not an agent input")
    link = tmp_path / "artifact-link"
    link.symlink_to(target, target_is_directory=True)
    assert os.path.isdir(link)
    # Reproduce only the observed stdlib branch semantics, never execute the verifier.
    with pytest.raises(OSError):
        shutil.rmtree(link)
    assert (target / "secret").exists()
    link.unlink()


def test_source_aliases_are_canonical_and_cannot_bypass_immutability(task):
    sandbox = Sandbox(backend="fake", fake_inputs={"data/x.txt": "original"})
    handle = prepare(sandbox, task)
    read = sandbox.execute(handle, {"operation": "read", "path": "./data//x.txt"})
    assert read["data"]["artifacts"][0]["path"] == "data/x.txt"
    assert read["data"]["artifacts"][0]["kind"] == "input"
    write = sandbox.execute(handle, {"operation": "write", "path": "./data//x.txt", "content": "forged"})
    assert write["status"] == "incomplete"
    assert not sandbox._states


def test_modified_initial_input_is_not_accepted_as_source(task):
    sandbox = Sandbox(backend="fake", fake_inputs={"input.txt": "original"})
    handle = prepare(sandbox, task)
    root = Path(sandbox._states[handle["id"]]["root"])
    # Simulate an out-of-band worker program modifying a formerly trusted source.
    (root / "input.txt").write_text("forged")
    result = sandbox.execute(handle, {"operation": "read", "path": "input.txt"})
    assert result["status"] == "incomplete"
    assert "prepared SHA-256" in result["error"]["message"]
    assert "forged" not in json.dumps(result)
    assert not root.exists()


def test_trusted_controller_disables_worker_import_shadowing(task, tmp_path):
    import subprocess
    import sys
    marker = tmp_path / "controller-hijacked"
    for module in ("json", "hashlib"):
        (tmp_path / f"{module}.py").write_text(f"open({str(marker)!r}, 'w').write('hijacked')\n")
    transport = RecordingDocker(task)
    sandbox = docker_sandbox(task, transport)
    handle = prepare(sandbox, task)
    controller = next(args for args, _ in transport.calls if args[0] == "exec")
    assert controller[controller.index("python3") + 1:controller.index("-c")] == ["-I", "-S"]
    # Only a synthetic local filesystem operation, never Docker or a scientific oracle.
    source = sandbox._controller.replace("root = '/work'", f"root = {str(tmp_path)!r}")
    result = subprocess.run([sys.executable, "-I", "-S", "-c", source],
                            input=json.dumps({"operation": "list", "path": ".", "task_id": task["task_id"]}),
                            text=True, capture_output=True, cwd=tmp_path, timeout=10, check=True)
    assert json.loads(result.stdout)["ok"] is True
    assert not marker.exists()
    sandbox.close(handle)


def test_docker_input_manifest_rejects_changed_measurements_after_exec(task):
    import hashlib
    task = copy.deepcopy(task)
    task["task_id"] = "nanoindentation-property-extraction"
    task["allowed_inputs"] = copy.deepcopy(IMAGE_INPUTS[task["task_id"]])
    task["resource_requirements"] = dict(RESOURCE_REQUIREMENTS[task["task_id"]])

    class ChangedMeasurementDocker(RecordingDocker):
        def run(self, args, **kwargs):
            result = super().run(args, **kwargs)
            if args[0] == "exec" and "-c" in args:
                request = json.loads(kwargs["stdin_data"])
                if request["operation"] == "bootstrap":
                    result["stdout"] = json.dumps({"ok": True, "data": {"input_hashes": {
                        "data/x.txt": hashlib.sha256(b"original").hexdigest()}}})
                elif request["operation"] == "read":
                    result["stdout"] = json.dumps({"ok": True, "data": {
                        "bytes": base64.b64encode(b"forged").decode()}})
            return result

    transport = ChangedMeasurementDocker(task)
    sandbox = docker_sandbox(task, transport)
    handle = prepare(sandbox, task)
    sandbox.execute(handle, {"operation": "exec", "argv": ["python3", "worker.py"]})
    result = sandbox.execute(handle, {"operation": "read", "path": "./data/x.txt"})
    assert result["status"] == "incomplete"
    assert "prepared SHA-256" in result["error"]["message"]
    assert transport.calls[-1][0][0] == "rm"


def test_synthetic_mode_accepts_explicit_non_scientific_fixture_identity(task):
    task["task_id"] = "fixture-task"
    task["allowed_inputs"] = ["input/data.txt"]
    task.pop("resource_requirements")
    sandbox = Sandbox(backend="fake", fake_inputs={"input/data.txt": "synthetic only"})
    with sandbox.session(task, {}) as handle:
        assert sandbox.read_source(handle, "input/data.txt") == b"synthetic only"


def test_real_docker_never_accepts_synthetic_or_fourth_task(task):
    task["task_id"] = "fixture-task"
    transport = RecordingDocker(task)
    sandbox = docker_sandbox(task, transport)
    assert sandbox.prepare(task, {})["status"] == "incomplete"
    assert transport.calls == []


def test_cleanup_failure_is_visible_and_retryable(task):
    class CleanupFailure(RecordingDocker):
        failed = False

        def run(self, args, **kwargs):
            result = super().run(args, **kwargs)
            if args[0] == "rm" and not self.failed:
                self.failed = True
                result.update(exit_status=1, stderr="daemon temporarily unavailable")
            return result

    transport = CleanupFailure(task)
    sandbox = docker_sandbox(task, transport)
    handle = prepare(sandbox, task)
    assert sandbox.close(handle)["error"]["code"] == "sandbox_cleanup_failed"
    assert handle["id"] in sandbox._states
    assert sandbox.close(handle)["status"] == "ok"
    assert not sandbox._states
