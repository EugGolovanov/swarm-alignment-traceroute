import json

import pytest

from little_pom.contracts import evidence_id, sha256_bytes
from little_pom.evidence import EvidenceRegistry


def make_registry(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "data.txt").write_text("Exact source α\nSecond line.\n", encoding="utf-8")
    return EvidenceRegistry(tmp_path / "registry", "task", source_root=inputs), inputs


def test_identity_deduplicates_and_retains_separate_worker_provenance(tmp_path):
    registry, inputs = make_registry(tmp_path)
    first = registry.register("data.txt", "Exact source α", "worker-a")
    second = registry.register("data.txt", "Exact source α", "worker-b")
    again = registry.register("data.txt", "Exact source α", "worker-a")
    assert first == second == again
    assert len(registry.evidence()) == 1
    assert {item["worker_id"] for item in registry.provenance()} == {"worker-a", "worker-b"}
    assert len(registry.provenance()) == 2
    assert first["evidence_id"] == evidence_id(sha256_bytes((inputs / "data.txt").read_bytes()), sha256_bytes("Exact source α".encode()))
    snapshot = registry.root / "sources" / first["source_sha256"]
    assert snapshot.read_bytes() == (inputs / "data.txt").read_bytes()
    reopened = EvidenceRegistry(registry.root, "task", source_root=inputs)
    assert reopened.register("data.txt", "Exact source α", "worker-b") == first


def test_source_changes_change_identity_and_preserve_old_snapshot(tmp_path):
    registry, inputs = make_registry(tmp_path)
    old = registry.register("data.txt", "Second line.", "a")
    (inputs / "data.txt").write_text("Changed source\nSecond line.\n", encoding="utf-8")
    new = registry.register("data.txt", "Second line.", "a")
    assert old["evidence_id"] != new["evidence_id"]
    assert len(registry.evidence()) == 2
    assert (registry.root / "sources" / old["source_sha256"]).read_text().startswith("Exact")


@pytest.mark.parametrize("source,fragment", [("missing.txt", "text"), ("data.txt", "invented quotation"),
                                               ("../outside", "text"), ("/etc/passwd", "text"), ("data.txt", "")])
def test_missing_inexact_or_unsafe_sources_are_rejected(tmp_path, source, fragment):
    registry, _ = make_registry(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        registry.register(source, fragment, "worker")
    assert not registry.evidence()
    assert not registry.provenance()


def test_symlink_cannot_escape_explicit_source_root(tmp_path):
    registry, inputs = make_registry(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("not allowed")
    (inputs / "link.txt").symlink_to(secret)
    with pytest.raises(ValueError):
        registry.register("link.txt", "not allowed", "worker")


def test_generated_artifacts_are_distinct_from_unverified_claims(tmp_path):
    registry, _ = make_registry(tmp_path)
    registry.bind_worker("a", lambda source: b"Generated numerical output: 4")
    registry.mark_generated("a", "out/result.txt")
    evidence = registry.register("out/result.txt", "numerical output: 4", "a")
    assert evidence["kind"] == "generated"
    assert registry.provenance("a")[0]["kind"] == "generated"
    with pytest.raises(ValueError):
        registry.register("out/result.txt", "numerical output: 5", "a")
    registry.unbind_worker("a")
    with pytest.raises(FileNotFoundError):
        registry.register("out/result.txt", "numerical output: 4", "a")


def test_mutated_persisted_evidence_is_not_silently_reused(tmp_path):
    registry, _ = make_registry(tmp_path)
    item = registry.register("data.txt", "Second line.", "a")
    path = registry.root / "objects" / f"{item['evidence_id']}.json"
    item["fragment"] = "invented"
    path.chmod(0o600)
    path.write_text(json.dumps(item))
    with pytest.raises(ValueError):
        registry.register("data.txt", "Second line.", "b")


def test_registry_never_accepts_model_supplied_identity(tmp_path):
    registry, _ = make_registry(tmp_path)
    with pytest.raises(TypeError):
        registry.register("data.txt", "Second line.", "a", evidence_id="fabricated")


@pytest.mark.parametrize("generated_first", [False, True])
def test_deduplication_preserves_current_observation_in_both_orders(tmp_path, generated_first):
    registry = EvidenceRegistry(tmp_path / "registry", "task")
    registry.bind_worker("input-worker", lambda source: b"Identical source bytes")
    registry.bind_worker("generated-worker", lambda source: b"Identical source bytes")
    registry.mark_generated("generated-worker", "./out/copy.txt")
    observations = [("input/data.txt", "input-worker"), ("out/copy.txt", "generated-worker")]
    if generated_first:
        observations.reverse()
    returned = {worker: registry.register(path, "Identical source", worker) for path, worker in observations}
    assert returned["input-worker"]["evidence_id"] == returned["generated-worker"]["evidence_id"]
    assert returned["input-worker"]["source"] == "input/data.txt"
    assert returned["input-worker"]["kind"] == "source"
    assert returned["generated-worker"]["source"] == "out/copy.txt"
    assert returned["generated-worker"]["kind"] == "generated"
    assert len(registry.evidence()) == 1
    by_worker = {entry["worker_id"]: entry for entry in registry.provenance()}
    assert by_worker["input-worker"]["source"] == "input/data.txt"
    assert by_worker["input-worker"]["kind"] == "source"
    assert by_worker["generated-worker"]["source"] == "out/copy.txt"
    assert by_worker["generated-worker"]["kind"] == "generated"


@pytest.mark.parametrize("interrupted_directory", ["sources", "objects", "provenance"])
def test_interrupted_publication_never_leaves_partial_final_artifacts(tmp_path, monkeypatch, interrupted_directory):
    from little_pom.runtime import storage

    registry, _ = make_registry(tmp_path)
    real_link = storage.os.link

    def interrupted_link(source, destination, **kwargs):
        if destination.parent.name == interrupted_directory:
            raise OSError("Simulated interruption before atomic publication")
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(storage.os, "link", interrupted_link)
    with pytest.raises(OSError, match="Simulated interruption"):
        registry.register("data.txt", "Second line.", "a")
    assert not list((registry.root / interrupted_directory).glob("*"))
    monkeypatch.setattr(storage.os, "link", real_link)
    recovered = registry.register("data.txt", "Second line.", "a")
    assert recovered["fragment"] == "Second line."
    assert len(registry.evidence()) == len(registry.provenance()) == 1
