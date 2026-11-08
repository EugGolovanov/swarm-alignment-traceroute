import hashlib
import json

from little_pom.sandbox import preflight
from little_pom.tasks import RESOURCE_REQUIREMENTS, TASK_PATHS


def synthetic_snapshot(tmp_path):
    inventory = {}
    for task_id, prefix in TASK_PATHS.items():
        for relative, content in {"instruction.md": "public synthetic task", "environment/Dockerfile": "FROM fake"}.items():
            name = f"{prefix}/{relative}"
            path = tmp_path / "upstream" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            inventory[name] = hashlib.sha256(content.encode()).hexdigest()
    manifest = {"commit": "synthetic", "file_count": len(inventory), "files_sha256": inventory,
                "unresolved_lfs_pointers": [], "tasks": [
                    {"task_id": task_id, "path": path, "environment": RESOURCE_REQUIREMENTS[task_id]}
                    for task_id, path in TASK_PATHS.items()]}
    (tmp_path / "snapshot_manifest.json").write_text(json.dumps(manifest))
    return tmp_path


class InspectionOnly:
    def __init__(self):
        self.commands = []

    def run(self, args, **kwargs):
        self.commands.append(args)
        assert args[0] in {"info", "image"}, "Preflight must not build, run, pull, or mutate Docker"
        if args[0] == "info":
            return {"stdout": json.dumps({"MemTotal": int(7.75 * 1024 ** 3), "NCPU": 10, "Architecture": "aarch64"}),
                    "stderr": "", "exit_status": 0, "truncated": False, "timed_out": False}
        return {"stdout": "", "stderr": "No such image", "exit_status": 1, "truncated": False, "timed_out": False}


def test_preflight_does_not_promote_historical_readiness(tmp_path):
    transport = InspectionOnly()
    result = preflight(synthetic_snapshot(tmp_path), transport=transport)
    assert result["status"] == "incomplete"
    report = result["data"]
    assert report["snapshot"]["verified"]
    assert report["historical_environment"]["live_observation"] is False
    assert report["docker"]["inspected"] is False
    assert report["ready"] is False
    assert report["dna_verifier"]["status"] == "unresolved"
    assert report["dna_verifier"]["upstream_fix_verified"] is False
    assert transport.commands == []
    assert all(task["ram_sufficient"] is None for task in report["tasks"])


def test_live_inspection_gates_eight_gb_tasks_and_absent_images(tmp_path):
    transport = InspectionOnly()
    result = preflight(synthetic_snapshot(tmp_path), inspect_docker=True,
                       images={task_id: "sha256:" + "a" * 64 for task_id in TASK_PATHS}, transport=transport)
    tasks = {task["task_id"]: task for task in result["data"]["tasks"]}
    assert tasks["dna-storage-codec"]["ram_sufficient"] is True
    for task_id in ("finite-free-stam", "nanoindentation-property-extraction"):
        assert tasks[task_id]["ram_sufficient"] is False
        assert "insufficient_vm_ram_with_headroom" in tasks[task_id]["blockers"]
    assert all(task["image"]["state"] == "unavailable" for task in tasks.values())
    assert result["data"]["docker"]["architecture"] == "aarch64"
    assert not result["data"]["ready"]


def test_invalid_snapshot_does_not_erase_other_readiness_findings(tmp_path):
    report = preflight(tmp_path)["data"]
    assert "snapshot_invalid" in report["blockers"]
    assert len(report["tasks"]) == 3
    assert report["snapshot"]["verified"] is False
    assert report["dna_verifier"]["oracle_run"] is False


def test_mutable_image_tags_are_not_accepted_as_verified_images(tmp_path):
    report = preflight(synthetic_snapshot(tmp_path), images={"dna-storage-codec": "science:latest"})["data"]
    dna = next(task for task in report["tasks"] if task["task_id"] == "dna-storage-codec")
    assert dna["image"]["state"] == "invalid_mutable_reference"
    assert "agent_image_not_verified_present" in dna["blockers"]


def test_paused_daemon_with_zero_exit_status_is_unavailable_not_resource_shortage(tmp_path):
    class PausedDaemon:
        def run(self, args, **kwargs):
            return {"stdout": json.dumps({
                "ServerErrors": ["Docker Desktop is manually paused."],
                "MemTotal": 0, "NCPU": 0, "Architecture": "",
            }), "stderr": "Docker Desktop is manually paused.", "exit_status": 0,
                "truncated": False, "timed_out": False}

    report = preflight(synthetic_snapshot(tmp_path), inspect_docker=True,
                       transport=PausedDaemon())["data"]
    assert report["docker"]["available"] is False
    assert "docker_unavailable" in report["blockers"]
    assert all(task["ram_sufficient"] is None and task["cpu_sufficient"] is None
               for task in report["tasks"])
    assert "insufficient_vm_cpus" not in report["blockers"]
    assert "insufficient_vm_ram_with_headroom" not in report["blockers"]
