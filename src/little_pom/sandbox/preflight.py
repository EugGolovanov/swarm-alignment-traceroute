"""Read-only readiness reporting; historical observations never become live facts."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import platform
import re
import uuid

from little_pom.contracts import sha256_hash
from little_pom.tasks import RESOURCE_REQUIREMENTS, TASK_PATHS, incomplete, verify_snapshot
from .docker import DockerCLI

DNA_VERIFIER_STATUS = {
    "status": "unresolved",
    "verification": "static_repository_evidence_only",
    "snapshot_commit": "437d5edbc7df862bf6dd6e6653368ca973bff3b0",
    "evidence": [
        {"path": TASK_PATHS["dna-storage-codec"] + "/task.toml", "lines": "5-10",
         "observation": "The whole /app/ artifact directory is transferred to the separate verifier."},
        {"path": TASK_PATHS["dna-storage-codec"] + "/tests/test_encoder_decoder.py", "lines": "69-73,171-177",
         "observation": "Backup follows directory links; reset branches on os.path.isdir then calls shutil.rmtree without an explicit symlink check."},
    ],
    "inference": "A directory symlink can reach shutil.rmtree, which refuses directory symlinks. This is a candidate explanation, not a reproduced upstream oracle result.",
    "regression_fixture": "tests/sandbox/fixtures/dna_symlink_verifier.json",
    "upstream_fix_verified": False,
    "oracle_run": False,
}


def preflight(snapshot_root: str | Path, *, inspect_docker: bool = False,
              images: dict[str, str] | None = None, docker_binary: str = "docker", transport=None) -> dict:
    """Inspect hashes and optionally Docker metadata, never build or run containers.

    `ready` is scientific readiness, not synthetic-pipeline usability. Even a present
    image does not establish non-root execution, architecture compatibility, oracle
    validity, or a resolution of the reported DNA verifier defect.
    """
    report = {"schema_version": "1.0", "id": uuid.uuid4().hex,
              "ready": False, "synthetic_backend_available": True,
              "snapshot": {"verified": False}, "host_architecture": platform.machine(),
              "historical_environment": {"source": "environments/harbor/README.md:18-28",
                                         "docker_architecture": "arm64", "vm_memory_gib_approx": 7.75,
                                         "scientific_images_built": False, "live_observation": False},
              "docker": {"inspected": False}, "tasks": [],
              "dna_verifier": deepcopy(DNA_VERIFIER_STATUS),
              "network_policy": {"build_time": "Explicit separate opt-in network access may be required",
                                 "worker_runtime": "none", "upstream_public_network_is_authorization": False},
              "blockers": []}
    try:
        manifest, _ = verify_snapshot(snapshot_root)
        report["snapshot"] = {"verified": True, "inventory_count": len(manifest["files_sha256"]),
                              "snapshot_hash": sha256_hash(manifest), "commit": manifest.get("commit"),
                              "verification_scope": "Actual bytes of every inventoried file and absence of extra task files"}
    except Exception as exc:
        report["blockers"].append("snapshot_invalid")
        report["snapshot"]["error"] = str(exc)
    cli = transport or DockerCLI(docker_binary)
    machine = None
    if inspect_docker:
        try:
            result = cli.run(["info", "--format", "{{json .}}"], timeout=30, output_limit=131072)
            if result["exit_status"] or result["truncated"] or result.get("timed_out"):
                raise ValueError("Docker info failed or returned incomplete evidence")
            machine = json.loads(result["stdout"])
            if not isinstance(machine, dict):
                raise ValueError("Docker info did not return a server object")
            if machine.get("ServerErrors"):
                raise ValueError("Docker server unavailable: " + "; ".join(map(str, machine["ServerErrors"])))
            if (not machine.get("Architecture") or type(machine.get("MemTotal")) is not int
                    or machine["MemTotal"] <= 0 or type(machine.get("NCPU")) is not int
                    or machine["NCPU"] <= 0):
                raise ValueError("Docker server resource metadata is unavailable")
            report["docker"] = {"inspected": True, "available": True, "architecture": machine.get("Architecture"),
                                "memory_bytes": machine.get("MemTotal"), "cpus": machine.get("NCPU"),
                                "source": "docker info --format '{{json .}}'"}
        except Exception as exc:
            machine = None
            report["docker"] = {"inspected": True, "available": False, "error": str(exc)}
            report["blockers"].append("docker_unavailable")
    else:
        report["blockers"].append("docker_not_inspected")
    for task_id, requirements in RESOURCE_REQUIREMENTS.items():
        task = {"task_id": task_id, "resource_requirements": dict(requirements),
                "resource_source": "Declared snapshot metadata; checked against frozen selected-task requirements",
                "required_memory_with_headroom_bytes": (requirements["memory_mb"] + 512) * 1024 * 1024,
                "ram_sufficient": None, "cpu_sufficient": None,
                "image": {"state": "not_supplied", "runtime_verified": False},
                "architecture_runtime_verified": False, "ready": False, "blockers": []}
        if machine is not None:
            task["ram_sufficient"] = machine.get("MemTotal", 0) >= task["required_memory_with_headroom_bytes"]
            task["cpu_sufficient"] = machine.get("NCPU", 0) >= requirements["cpus"]
            if not task["ram_sufficient"]:
                task["blockers"].append("insufficient_vm_ram_with_headroom")
            if not task["cpu_sufficient"]:
                task["blockers"].append("insufficient_vm_cpus")
        else:
            task["blockers"].append("live_resources_unverified")
        image = (images or {}).get(task_id)
        if image:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                task["image"] = {"state": "invalid_mutable_reference", "runtime_verified": False}
            elif inspect_docker and machine is not None:
                try:
                    result = cli.run(["image", "inspect", image], timeout=30, output_limit=131072)
                    if result["exit_status"] or result["truncated"] or result.get("timed_out"):
                        raise ValueError("Pinned image is absent or image inspection failed")
                    metadata = json.loads(result["stdout"])[0]
                    labels = metadata.get("Config", {}).get("Labels") or {}
                    dockerfile_hash = (manifest["files_sha256"].get(f"{TASK_PATHS[task_id]}/environment/Dockerfile")
                                       if report["snapshot"]["verified"] else None)
                    expected = {"little_pom.task_id": task_id, "little_pom.snapshot_hash": report["snapshot"].get("snapshot_hash"),
                                "little_pom.isolation_version": "1", "little_pom.dockerfile_sha256": dockerfile_hash}
                    provenance_matches = (report["snapshot"]["verified"] and metadata.get("Id") == image
                                          and all(labels.get(k) == v for k, v in expected.items()))
                    task["image"] = {"state": "present", "id": metadata.get("Id"), "architecture": metadata.get("Architecture"),
                                     "provenance_matches": provenance_matches, "runtime_verified": False}
                    if not provenance_matches:
                        task["blockers"].append("image_provenance_mismatch")
                    aliases = {"aarch64": "arm64", "x86_64": "amd64"}
                    arch = aliases.get(machine.get("Architecture"), machine.get("Architecture"))
                    if metadata.get("Architecture") != arch:
                        task["blockers"].append("image_architecture_mismatch")
                except Exception as exc:
                    task["image"] = {"state": "unavailable", "error": str(exc), "runtime_verified": False}
            else:
                task["image"] = {"state": "supplied_not_inspected", "runtime_verified": False}
        if task["image"]["state"] != "present":
            task["blockers"].append("agent_image_not_verified_present")
        task["blockers"].append("scientific_runtime_and_oracle_unverified")
        if task_id == "dna-storage-codec":
            task["blockers"].append("dna_symlink_verifier_unresolved")
        if task_id == "finite-free-stam":
            task["nonroot_toolchain"] = "Owned Dockerfile relocates /root/.elan to /opt/elan; requires explicit image build and runtime verification"
        report["tasks"].append(task)
    report["blockers"].extend(sorted({blocker for task in report["tasks"] for blocker in task["blockers"]}))
    return incomplete("scientific_readiness_unresolved", "Preflight completed; scientific readiness has unresolved gates", report)
