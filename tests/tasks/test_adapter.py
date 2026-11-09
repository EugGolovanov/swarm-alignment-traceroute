import hashlib
import json
from pathlib import Path

import pytest

from little_pom.contracts import sha256_hash
from little_pom.tasks import (
    RESOURCE_REQUIREMENTS, SCI_TASK_IDS, SCI_TASK_PATHS, TASK_PATHS,
    TaskAdapter, snapshot_task_ids, verify_snapshot,
)


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "corpus"
    source = root / "upstream"
    inventory = {}
    for task_id, relative in TASK_PATHS.items():
        files = {"instruction.md": f"Solve the public {task_id} task.",
                 "environment/Dockerfile": "FROM synthetic-agent-image\n",
                 "tests/secret.json": "private-verifier-marker",
                 "solution/solve.py": "private-solution-marker",
                 "authoring/generator.py": "private-authoring-marker"}
        for name, content in files.items():
            path = source / relative / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            inventory[f"{relative}/{name}"] = hashlib.sha256(content.encode()).hexdigest()
    manifest = {"commit": "synthetic-commit", "tasks": [
        {"task_id": task_id, "path": relative, "environment": RESOURCE_REQUIREMENTS[task_id]}
        for task_id, relative in TASK_PATHS.items()], "files_sha256": inventory,
        "file_count": len(inventory), "unresolved_lfs_pointers": []}
    (root / "snapshot_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.mark.parametrize("task_id", TASK_PATHS)
def test_real_hash_inventory_and_private_projection(snapshot, task_id):
    result = TaskAdapter().load(task_id, snapshot)
    assert result["status"] == "ok"
    task = result["data"]
    assert task["instruction"] == task["goal"]
    assert len(task["snapshot_hash"]) == 64
    assert task["resource_requirements"] == RESOURCE_REQUIREMENTS[task_id]
    serialized = json.dumps(task)
    for marker in ("private-verifier-marker", "private-solution-marker", "private-authoring-marker",
                   "secret.json", "solve.py", "generator.py", str(snapshot)):
        assert marker not in serialized
    assert task["agent_image"]["runtime_network"] == "none"


def test_allowlist_rejects_unknown_task_without_touching_root(tmp_path):
    result = TaskAdapter().load("other-task", tmp_path / "absent")
    assert result["status"] == "incomplete"
    assert result["error"]["code"] == "task_not_allowed"


def test_hashes_hidden_inventory_bytes_without_exposing_them(snapshot):
    path = snapshot / "upstream" / TASK_PATHS["finite-free-stam"] / "tests/secret.json"
    path.write_text("tampered", encoding="utf-8")
    result = TaskAdapter().load("dna-storage-codec", snapshot)
    assert result["status"] == "incomplete"
    assert "SHA-256 mismatch" in result["error"]["message"]


def test_extra_file_is_not_silently_admitted(snapshot):
    (snapshot / "upstream" / TASK_PATHS["dna-storage-codec"] / "extra.txt").write_text("extra")
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "incomplete"


def test_snapshot_symlink_rejected_even_when_bytes_match(snapshot, tmp_path):
    path = snapshot / "upstream" / TASK_PATHS["dna-storage-codec"] / "instruction.md"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "incomplete"


def test_manifest_path_traversal_rejected(snapshot):
    path = snapshot / "snapshot_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files_sha256"]["../outside"] = "0" * 64
    manifest["file_count"] += 1
    path.write_text(json.dumps(manifest))
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "incomplete"


def test_resource_metadata_cannot_be_lowered(snapshot):
    path = snapshot / "snapshot_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["tasks"][0]["environment"]["memory_mb"] = 64
    path.write_text(json.dumps(manifest))
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "incomplete"


def test_build_generated_nano_inputs_are_agent_image_descriptors(snapshot):
    task = TaskAdapter().load("nanoindentation-property-extraction", snapshot)["data"]
    assert task["allowed_inputs"] == [{"path": "/workspace/data", "kind": "directory", "source": "agent_image"}]
    assert "generate_data.py" not in json.dumps(task)


def test_instruction_changed_between_inventory_and_projection_is_rejected(snapshot, monkeypatch):
    import little_pom.tasks as tasks_module
    original = tasks_module.verify_snapshot

    def mutate_after_verification(root):
        manifest, source = original(root)
        (source / TASK_PATHS["dna-storage-codec"] / "instruction.md").write_text("unverified replacement")
        return manifest, source

    monkeypatch.setattr(tasks_module, "verify_snapshot", mutate_after_verification)
    result = TaskAdapter().load("dna-storage-codec", snapshot)
    assert result["status"] == "incomplete"
    assert result["error"]["message"] == "Instruction changed after inventory verification"


def add_metadata_inventory(snapshot, entries):
    manifest_path = snapshot / "snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"].update(entries)
    manifest["file_count"] = len(manifest["files_sha256"])
    manifest_path.write_text(json.dumps(manifest))


def test_inventoried_root_metadata_link_chain_is_hashed(snapshot):
    source = snapshot / "upstream"
    content = b"repository metadata"
    (source / "AGENTS.md").write_bytes(content)
    (source / "CLAUDE.md").symlink_to("AGENTS.md")
    (source / "ALIAS.md").symlink_to("CLAUDE.md")
    digest = hashlib.sha256(content).hexdigest()
    add_metadata_inventory(snapshot, {name: digest for name in ("AGENTS.md", "CLAUDE.md", "ALIAS.md")})
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "ok"
    (source / "AGENTS.md").write_bytes(b"changed metadata")
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "incomplete"


@pytest.mark.parametrize("target", ["../outside.md", "/tmp/outside.md", "untracked.md"])
def test_metadata_link_escape_and_untracked_targets_are_rejected(snapshot, target):
    source = snapshot / "upstream"
    (source / "untracked.md").write_text("untracked metadata")
    (source / "CLAUDE.md").symlink_to(target)
    add_metadata_inventory(snapshot, {"CLAUDE.md": "a" * 64})
    result = TaskAdapter().load("dna-storage-codec", snapshot)
    assert result["status"] == "incomplete"
    assert "symlink" in result["error"]["message"]


def test_inventoried_metadata_link_cycle_is_rejected(snapshot):
    source = snapshot / "upstream"
    (source / "CLAUDE.md").symlink_to("AGENTS.md")
    (source / "AGENTS.md").symlink_to("CLAUDE.md")
    add_metadata_inventory(snapshot, {"CLAUDE.md": "a" * 64, "AGENTS.md": "a" * 64})
    result = TaskAdapter().load("dna-storage-codec", snapshot)
    assert result["status"] == "incomplete"
    assert "cycle" in result["error"]["message"]


def test_metadata_alias_cannot_link_into_inventoried_private_task_material(snapshot):
    source = snapshot / "upstream"
    target = TASK_PATHS["dna-storage-codec"] + "/tests/secret.json"
    (source / "CLAUDE.md").symlink_to(target)
    digest = hashlib.sha256((source / target).read_bytes()).hexdigest()
    add_metadata_inventory(snapshot, {"CLAUDE.md": digest})
    assert TaskAdapter().load("dna-storage-codec", snapshot)["status"] == "incomplete"


@pytest.mark.parametrize("task_id", TASK_PATHS)
def test_actual_frozen_corpus_loads_with_its_metadata_symlink(task_id):
    corpus = Path(__file__).resolve().parents[2] / "data" / "terminal-bench-science"
    assert (corpus / "upstream" / "CLAUDE.md").is_symlink()
    result = TaskAdapter().load(task_id, corpus)
    assert result["status"] == "ok", result
    assert result["data"]["task_id"] == task_id
    assert "files_sha256" not in result["data"]


@pytest.fixture
def sci_snapshot(tmp_path):
    root = tmp_path / "sci"
    inventory = {}
    for task_id, prefix in SCI_TASK_PATHS.items():
        files = {
            "instruction.md": f"Solve the public {task_id} task.",
            "environment/Dockerfile": "COPY data/ /home/agent/data/\n",
            "environment/data/public.csv": "public-input-marker",
            "tests/rubric.json": "private-rubric-marker",
            "tests/golden.json": "private-golden-marker",
            "solution/solve.py": "private-solution-marker",
        }
        for relative, content in files.items():
            name = f"{prefix}/{relative}"
            path = root / "upstream" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            inventory[name] = hashlib.sha256(content.encode()).hexdigest()
    manifest = {
        "commit": "synthetic-sci-commit",
        "scope": "public_example_extract",
        "development_split_available": False,
        "task_ids": list(SCI_TASK_IDS),
        "tracked_file_count": len(inventory),
        "files_sha256": inventory,
        "unresolved_lfs_pointers": [],
    }
    (root / "snapshot_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.mark.parametrize("task_id", SCI_TASK_IDS)
def test_sci_projection_preserves_manifest_and_dockerfile_hashes(sci_snapshot, task_id):
    original = json.loads((sci_snapshot / "snapshot_manifest.json").read_text())
    manifest, source = verify_snapshot(sci_snapshot)
    assert manifest == original
    assert source == sci_snapshot / "upstream"
    assert snapshot_task_ids(manifest) == SCI_TASK_IDS
    result = TaskAdapter().load(task_id, sci_snapshot)
    assert result["status"] == "ok", result
    task = result["data"]
    assert task["task_id"] == task_id
    assert task["instruction"] == task["goal"] == f"Solve the public {task_id} task."
    assert task["snapshot_hash"] == sha256_hash(original)
    assert task["agent_image"]["dockerfile_sha256"] == original["files_sha256"][
        f"{SCI_TASK_PATHS[task_id]}/environment/Dockerfile"
    ]
    assert task["allowed_inputs"] == [
        {"path": "/home/agent/data", "kind": "directory", "source": "agent_image"}
    ]
    assert task["resource_requirements"] == {"cpus": 1, "memory_mb": 4096, "storage_mb": 10240}
    assert task["agent_image"]["runtime_network"] == "none"
    serialized = json.dumps(task)
    for marker in ("private-rubric-marker", "private-golden-marker", "private-solution-marker",
                   "rubric.json", "golden.json", "solve.py", "public-input-marker",
                   "files_sha256", "tracked_file_count", str(sci_snapshot)):
        assert marker not in serialized


@pytest.mark.parametrize("field,value", [
    ("tracked_file_count", 0),
    ("task_ids", list(SCI_TASK_IDS[:-1])),
    ("task_ids", [SCI_TASK_IDS[0]] * 5),
    ("task_ids", list(SCI_TASK_IDS[:-1]) + ["unknown"]),
    ("task_ids", [None] * 5),
    ("task_ids", None),
    ("tasks", []),
    ("unresolved_lfs_pointers", ["environment/data/input.csv"]),
])
def test_sci_manifest_rejects_invalid_selection_and_inventory(sci_snapshot, field, value):
    path = sci_snapshot / "snapshot_manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    assert TaskAdapter().load(SCI_TASK_IDS[0], sci_snapshot)["error"]["code"] == "snapshot_invalid"


@pytest.mark.parametrize("relative", ["environment/data/public.csv", "tests/golden.json"])
def test_sci_public_and_private_bytes_are_both_verified(sci_snapshot, relative):
    path = sci_snapshot / "upstream" / SCI_TASK_PATHS[SCI_TASK_IDS[-1]] / relative
    path.write_text("tampered")
    result = TaskAdapter().load(SCI_TASK_IDS[0], sci_snapshot)
    assert result["error"]["code"] == "snapshot_invalid"
    assert "SHA-256 mismatch" in result["error"]["message"]


def test_sci_extra_task_file_is_rejected(sci_snapshot):
    path = sci_snapshot / "upstream" / SCI_TASK_PATHS[SCI_TASK_IDS[0]] / "extra.txt"
    path.write_text("untracked")
    assert TaskAdapter().load(SCI_TASK_IDS[0], sci_snapshot)["error"]["code"] == "snapshot_invalid"


def test_sci_public_input_symlink_is_rejected(sci_snapshot):
    directory = sci_snapshot / "upstream" / SCI_TASK_PATHS[SCI_TASK_IDS[0]]
    path = directory / "environment/data/public.csv"
    path.unlink()
    path.symlink_to(directory / "tests/golden.json")
    assert TaskAdapter().load(SCI_TASK_IDS[0], sci_snapshot)["error"]["code"] == "snapshot_invalid"


def test_task_must_belong_to_selected_corpus(snapshot, sci_snapshot):
    assert TaskAdapter().load(SCI_TASK_IDS[0], snapshot)["error"]["code"] == "task_not_allowed"
    assert TaskAdapter().load("dna-storage-codec", sci_snapshot)["error"]["code"] == "task_not_allowed"


@pytest.mark.parametrize("task_id", SCI_TASK_IDS)
def test_actual_public_sci_corpus_loads_without_normalizing_manifest(task_id):
    corpus = Path(__file__).resolve().parents[2] / "data" / "sci-agent-exam"
    manifest = json.loads((corpus / "snapshot_manifest.json").read_text())
    result = TaskAdapter().load(task_id, corpus)
    assert result["status"] == "ok", result
    assert result["data"]["task_id"] == task_id
    assert result["data"]["snapshot_hash"] == sha256_hash(manifest)
    assert "files_sha256" not in result["data"]
