import json

import pytest

from little_pom.analysis.trace_audit import audit_saved_run, write_audit
from little_pom.contracts import canonical_json, sha256_bytes
from little_pom.contracts.hashing import evidence_id


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))
    return path


def fixture(root):
    def evidence(source, text, fragment, kind="source"):
        raw = text.encode()
        source_hash = sha256_bytes(raw)
        fragment_hash = sha256_bytes(fragment.encode())
        eid = evidence_id(source_hash, fragment_hash)
        obj = {"id": eid, "evidence_id": eid, "task_id": "task", "source": source,
               "source_sha256": source_hash, "fragment_sha256": fragment_hash,
               "fragment": fragment, "kind": kind}
        put(root, f"evidence/task/objects/{eid}.json", obj)
        path = root / "evidence/task/sources" / source_hash
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return obj

    public = evidence("__little_pom__/task.txt", "Use exact units.\n", "Use exact units.")
    shared = evidence("data/a", "temperature = 300 K\n", "temperature = 300 K")
    # Same literal text, different source snapshots: NOT a novel scientific fact.
    copied = evidence("out/copy", "temperature = 300 K\ncopy", "temperature = 300 K", "generated")
    unique = evidence("out/measurement", "α = 2\n", "α = 2", "generated")
    orphan = evidence("out/incomplete", "unaccepted\n", "unaccepted", "generated")

    def state(worker, records, complete=True):
        result = {"worker_id": worker, "task_id": "task", "history": [],
                  "evidence_ids": [e["evidence_id"] for e in records], "evidence": records,
                  "output": {"complete": complete, "result": "A grounded result with Unicode α", "limitations": []}}
        put(root, f"workers/{worker}/state.json", result)
        return result

    states = [state("a", [public, shared, unique]), state("b", [shared, copied])]
    state("incomplete", [orphan], False)
    provenance = []
    for worker, records in [("a", [public, shared, unique]), ("b", [shared, copied]), ("incomplete", [orphan])]:
        for record in records:
            entry = {"id": worker + record["evidence_id"], "task_id": "task", "worker_id": worker,
                     "evidence_id": record["evidence_id"], "kind": record["kind"],
                     "source": record["source"], "source_sha256": record["source_sha256"]}
            provenance.append(entry)
            put(root, f"evidence/task/provenance/{entry['id']}.json", entry)
    ids = {name: obj["evidence_id"] for name, obj in
           [("public", public), ("shared", shared), ("copied", copied), ("unique", unique), ("orphan", orphan)]}
    saved = {"task": {"task_id": "task", "instruction": "Use exact units."}, "states": states,
             "evidence": [public, shared, copied, unique, orphan], "provenance": provenance,
             "proposal": {"evidence_ids": [ids["shared"]]}, "author_error": None}
    put(root, "stages/workers.json", {"tasks": {"task": saved}})
    handoffs = [{"result": s["output"]["result"], "limitations": [],
                 "evidence": [{"evidence_id": e["evidence_id"], "fragment": e["fragment"]} for e in s["evidence"]]}
                for s in states]
    author_request = {"packet": {"handoffs": handoffs}, "stage": "author", "task_id": "task"}
    put(root, "intent/author.json", {"task_id": "task", "stage": "author", "execution_id": "author", "request": author_request})
    put(root, "result/author.json", {"service_result": {"status": "ok", "data": {"evidence_ids": [ids["shared"], "invented"]}}})
    packet = {"id": "packet", "task": {"text": "Use exact units."},
              "card": {"text": json.dumps({"evidence_ids": [ids["unique"]]})},
              "dossier": {"text": ""},
              "worker_context": {"evidence": [{"evidence_id": ids["shared"], "text": shared["fragment"]}]}}
    put(root, "intent/review.json", {"task_id": "task", "stage": "reviews", "execution_id": "review",
                                     "request": {"packet": packet, "stage": "reviews", "task_id": "task"}})
    put(root, "result/review.json", {"service_result": {"status": "ok", "data": {"evidence_ids": [ids["shared"], ids["unique"], "invented"]}}})
    put(root, "reviews/dispatch/d.json", {"slots": [{"packet_id": "packet", "panel_id": "panel", "worker_id": "b"}]})
    put(root, "stages/panels.json", {"panels": [{"id": "panel", "condition": "RandomHistory", "variant": "valid"}]})
    put(root, "cards/task/valid.json", {"task_id": "task", "card": {"variant": "valid", "evidence_ids": [ids["shared"]]}})
    return ids, author_request


def test_exact_identity_is_not_scientific_novelty_and_tracks_transfer(tmp_path):
    ids, request = fixture(tmp_path)
    report = audit_saved_run(tmp_path, author_input_budget=100)
    task = report["tasks"][0]
    rows = {e["evidence_id"]: e for e in task["evidence"]}
    assert task["counts"]["accepted_workers"] == 2
    assert task["counts"]["incomplete_or_unaccepted_workers"] == 1
    assert task["counts"]["accepted_verified_exact_identities"] == 4
    assert task["counts"]["unique_accepted_verified_exact_identities"] == 3
    assert task["counts"]["shared_accepted_verified_exact_identities"] == 1
    assert task["counts"]["accepted_distinct_exact_fragment_hashes"] == 3
    assert rows[ids["copied"]]["exact_identity_unique_to_accepted_worker"]
    assert not rows[ids["copied"]]["exact_fragment_unique_across_sources"]
    assert rows[ids["copied"]]["scientific_novelty"] == "not_assessed"
    assert rows[ids["public"]]["public_instruction_only_origin"]
    assert rows[ids["unique"]]["available_in_dispatched_author_packet"]
    assert rows[ids["shared"]]["proposal_cited"]
    assert task["registered_but_not_handed_off_ids"] == [ids["orphan"]]
    assert ids["unique"] in task["handoff_not_cited_by_proposal_ids"]
    assert task["handoff_pressure"]["request_bytes"] == len(canonical_json(request))
    assert task["handoff_pressure"]["request_exceeds_budget"]
    assert task["author_requests"][0]["unauthorized_citations"] == ["invented"]
    reviewer = task["reviewers"][0]
    assert reviewer["condition"] == "RandomHistory"
    assert ids["unique"] in reviewer["id_mentioned_ids"]
    assert ids["unique"] not in reviewer["fragment_available_ids"]
    assert set(reviewer["unavailable_fragment_citations"]) == {ids["unique"], "invented"}
    assert reviewer["unknown_citations"] == ["invented"]
    assert ids["shared"] in reviewer["worker_context"]["fragment_available_ids"]
    assert ids["shared"] not in reviewer["common_context"]["fragment_available_ids"]
    assert report["model_calls"] == 0


def test_corrupt_snapshot_is_not_verified_and_missing_snapshot_is_explicit(tmp_path):
    ids, _ = fixture(tmp_path)
    path = tmp_path / f"evidence/task/objects/{ids['unique']}.json"
    obj = json.loads(path.read_text())
    (tmp_path / "evidence/task/sources" / obj["source_sha256"]).write_bytes(b"different")
    orphan = json.loads((tmp_path / f"evidence/task/objects/{ids['orphan']}.json").read_text())
    (tmp_path / "evidence/task/sources" / orphan["source_sha256"]).unlink()
    report = audit_saved_run(tmp_path)
    rows = {e["evidence_id"]: e for e in report["tasks"][0]["evidence"]}
    assert not rows[ids["unique"]]["verified_source_fragment"]
    assert not rows[ids["unique"]]["integrity"]["source_sha256_matches"]
    assert not rows[ids["orphan"]]["integrity"]["source_snapshot_present"]
    assert rows[ids["orphan"]]["integrity"]["exact_fragment_in_source"] is None


def test_budget_rejection_does_not_claim_author_received_handoffs(tmp_path):
    ids, _ = fixture(tmp_path)
    (tmp_path / "intent/author.json").unlink()
    stage_path = tmp_path / "stages/workers.json"
    stage = json.loads(stage_path.read_text())
    stage["tasks"]["task"].update(proposal=None, author_error={"code": "input_budget_exceeded"})
    put(tmp_path, "stages/workers.json", stage)
    report = audit_saved_run(tmp_path, author_input_budget=10)
    task = report["tasks"][0]
    assert task["handoff_pressure"]["handoff_alone_exceeds_budget"]
    assert task["handoff_pressure"]["request_bytes"] is None
    assert task["handoff_not_cited_by_proposal_ids"] is None
    assert all(not e["available_in_dispatched_author_packet"] for e in task["evidence"])
    assert any(e["handed_off"] for e in task["evidence"])


def test_writer_preserves_source_and_refuses_old_outputs(tmp_path):
    source, output = tmp_path / "source", tmp_path / "diagnostics"
    fixture(source)
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    report = write_audit(source, output)
    assert json.loads((output / "trace-audit.json").read_text()) == report
    assert json.loads((output / "trace-audit-summary.json").read_text())["tasks"][0]["counts"]["accepted_workers"] == 2
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        write_audit(source, output)
    with pytest.raises(ValueError, match="outside"):
        write_audit(source, source / "diagnostics")
    assert audit_saved_run(source) == report


def test_empty_run_fails_instead_of_claiming_zero_evidence(tmp_path):
    with pytest.raises(ValueError, match="No saved"):
        audit_saved_run(tmp_path)
