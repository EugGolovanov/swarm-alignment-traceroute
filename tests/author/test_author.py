import copy
import json

import pytest

from little_pom.author import AuthorRunner
from little_pom.contracts import canonical_json, sha256_bytes
from little_pom.workers import MODEL_CONFIG, task_context


class RecordingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def call(self, request, schema_id, execution_id):
        self.calls.append((copy.deepcopy(request), schema_id, execution_id))
        return copy.deepcopy(self.response)


def task():
    return {"schema_version": "1.0", "id": "task-record", "task_id": "task", "goal": "Choose a baseline.",
            "allowed_inputs": ["input/data.txt"], "constraints": [], "snapshot_hash": sha256_bytes(b"input")}


def handoffs():
    return [{"result": "The inspected baseline has adequate coverage.", "limitations": ["Synthetic data only."],
             "evidence": [{"evidence_id": "a" * 64, "fragment": "baseline coverage is ten"}]}]


def proposal():
    return {"schema_version": "1.0", "id": "provider-card", "task_id": "task",
            "proposed_action": "Use the baseline provisionally.", "uncertainty": "The corpus is synthetic.",
            "grounds": ["Verified baseline evidence."], "expected_decision_impact": "Proceed to a bounded trial.",
            "scale_budget": "One bounded trial.", "preconditions": ["Confirm real-data applicability."],
            "evidence_ids": ["[[ev:1]]"]}


def client():
    return RecordingClient({"status": "ok", "data": proposal(), "error": None})


def test_author_gets_only_explicit_handoffs_and_no_worker_histories(tmp_path):
    recording = client()
    result = AuthorRunner(tmp_path).run(task(), handoffs(), recording)
    assert result["status"] == "ok", result
    request, schema_id, execution_id = recording.calls[0]
    assert schema_id == "ProposalCard"
    assert request["history"] == []
    assert request["config"] == MODEL_CONFIG
    assert request["model"] == "qwen3.5-397b"
    assert set(request["packet"]) == {"task", "handoffs"}
    assert "worker_id" not in canonical_json(request["packet"]).decode()
    assert result["data"]["id"] == execution_id
    assert request["packet"]["handoffs"][0]["evidence"][0]["evidence_id"] == "[[ev:1]]"
    assert result["data"]["evidence_ids"] == ["a" * 64]


def test_explicit_terra_author_model_and_parameters_are_frozen(tmp_path):
    recording = client()
    runner = AuthorRunner(tmp_path, model="openai-codex/gpt-6.1-terra",
                          model_config={"reasoning_effort": "medium"})
    first = runner.run(task(), handoffs(), recording)
    assert first["status"] == "ok"
    assert recording.calls[0][0]["model"] == "openai-codex/gpt-6.1-terra"
    assert recording.calls[0][0]["config"] == {"reasoning_effort": "medium"}
    assert runner.run(task(), handoffs(), recording) == first
    assert len(recording.calls) == 1
    assert AuthorRunner(tmp_path).run(task(), handoffs(), recording)["status"] == "incomplete"
    assert len(recording.calls) == 1


def test_hidden_future_labels_are_rejected_before_author_call(tmp_path):
    recording = client()
    contaminated = handoffs()
    contaminated[0]["target_defect"] = "secret-target"
    contaminated[0]["informed_workers"] = ["hidden-worker"]
    result = AuthorRunner(tmp_path).run(task(), contaminated, recording)
    assert result["status"] == "incomplete"
    assert not recording.calls
    hidden_task = {**task(), "target_defect": "secret-target", "condition": "RoutedHistory"}
    assert "target_defect" not in task_context(hidden_task)
    assert "condition" not in task_context(hidden_task)
    result = AuthorRunner(tmp_path).run(hidden_task, handoffs(), recording)
    assert result["status"] == "incomplete"
    assert not recording.calls


def test_author_cannot_invent_evidence_or_cross_task_boundary(tmp_path):
    recording = client()
    recording.response["data"]["evidence_ids"] = ["b" * 64]
    result = AuthorRunner(tmp_path / "invented").run(task(), handoffs(), recording)
    assert result["status"] == "incomplete"
    recording = client()
    recording.response["data"]["task_id"] = "another-task"
    result = AuthorRunner(tmp_path / "cross-task").run(task(), handoffs(), recording)
    assert result["status"] == "incomplete"


def test_author_raw_error_and_budget_failure_are_explicit(tmp_path):
    recording = RecordingClient({"status": "incomplete", "data": None, "error": {"code": "timeout", "message": "failure"}})
    result = AuthorRunner(tmp_path).run(task(), handoffs(), recording)
    assert result["status"] == "incomplete"
    assert result["data"] is None
    assert result["error"]["code"] == "author_call_incomplete"
    recording = client()
    result = AuthorRunner(tmp_path, input_budget=1).run(task(), handoffs(), recording)
    assert result["error"]["code"] == "input_budget_exceeded"
    assert not recording.calls


def test_author_resume_reuses_proposal_and_rejects_changed_context(tmp_path):
    first = AuthorRunner(tmp_path).run(task(), handoffs(), client())
    recording = client()
    second = AuthorRunner(tmp_path).run(task(), handoffs(), recording)
    assert first == second
    assert not recording.calls
    changed = handoffs()
    changed[0]["result"] = "A different scientific result changes the authorized proposal inputs."
    result = AuthorRunner(tmp_path).run(task(), changed, recording)
    assert result["status"] == "incomplete"
    assert not recording.calls


def test_author_atomic_publication_recovers_cached_response(tmp_path, monkeypatch):
    from little_pom.runtime import Ledger, MockBackend, SwarmClient, storage

    backend = MockBackend(responses=[proposal()])
    recording = SwarmClient(ledger=Ledger(tmp_path / "ledger"), backend=backend)
    real_link = storage.os.link

    def interrupted_link(source, destination, **kwargs):
        if destination.parent.name == "author" and not destination.name.endswith(".budget.json"):
            raise OSError("Simulated interruption publishing author proposal")
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(storage.os, "link", interrupted_link)
    first = AuthorRunner(tmp_path).run(task(), handoffs(), recording)
    assert first["status"] == "incomplete"
    assert len(backend.calls) == 1
    assert all(path.name.endswith(".budget.json") for path in (tmp_path / "author").glob("*.json"))
    monkeypatch.setattr(storage.os, "link", real_link)
    recovered = AuthorRunner(tmp_path).run(task(), handoffs(), recording)
    assert recovered["status"] == "ok", recovered
    assert len(backend.calls) == 1


def test_exact_preflight_is_deterministic_and_accounts_for_every_byte(tmp_path):
    runner = AuthorRunner(tmp_path)
    supplied = handoffs() * 2
    supplied[0] = copy.deepcopy(supplied[0])
    supplied[0]["result"] += ' Unicode λ and escaped "quotes".'
    original = copy.deepcopy(supplied)
    report = runner.inspect(task(), supplied)
    assert runner.inspect(task(), supplied) == report
    assert supplied == original
    assert not list(tmp_path.rglob("*.json"))
    assert report["handoff_count"] == 2
    assert report["evidence_reference_count"] == 2
    assert report["unique_evidence_count"] == 1
    assert not report["citations"]["checked"]
    assert report["serialized_request_bytes"] == (
        report["request_overhead_bytes"] + sum(item["serialized_bytes"] for item in report["contributions"]))
    recording = client()
    assert runner.run(task(), supplied, recording)["status"] == "ok"
    assert report["serialized_request_bytes"] == len(canonical_json(recording.calls[0][0]))


def test_exact_budget_boundary_is_inclusive_and_rejection_is_saved(tmp_path):
    size = AuthorRunner(tmp_path / "inspection").inspect(task(), handoffs())["serialized_request_bytes"]
    recording = client()
    assert AuthorRunner(tmp_path / "fits", input_budget=size).run(task(), handoffs(), recording)["status"] == "ok"
    rejected = client()
    runner = AuthorRunner(tmp_path / "rejected", input_budget=size - 1)
    result = runner.run(task(), handoffs(), rejected)
    assert result["error"]["code"] == "input_budget_exceeded"
    assert not rejected.calls
    report = json.loads((runner.root / result["error"]["report_path"]).read_text())
    assert report["remaining_bytes"] == -1
    assert report["issues"][0]["action"]
    assert runner.run(task(), handoffs(), rejected) == result


def test_citation_inspection_reports_availability_without_repair(tmp_path):
    runner = AuthorRunner(tmp_path)
    report = runner.inspect(task(), handoffs(), cited_evidence_ids=["a" * 64, "b" * 64])
    assert not report["accepted"]
    assert report["citations"]["missing_evidence_ids"] == ["b" * 64]
    assert report["citations"]["available_evidence_ids"] == ["a" * 64]
    recording = client()
    recording.response["data"]["evidence_ids"] = ["b" * 64]
    result = runner.run(task(), handoffs(), recording)
    saved = json.loads((runner.root / result["error"]["report_path"]).read_text())
    assert result["error"]["code"] == "evidence_reference_contract"
    assert saved["code"] == "evidence_reference_contract"
    assert len(recording.calls) == 1
    assert not list((runner.root / "author").glob("*.json"))


def test_invalid_handoff_rejection_saves_actionable_report(tmp_path):
    runner = AuthorRunner(tmp_path)
    recording = client()
    invalid = handoffs()
    invalid[0]["private_label"] = "forbidden"
    result = runner.run(task(), invalid, recording)
    report = json.loads((runner.root / result["error"]["report_path"]).read_text())
    assert not recording.calls
    assert not report["accepted"]
    assert "unapproved fields" in report["message"]


def test_pre_worker_plan_distinguishes_bound_allocation_and_exact_size(tmp_path):
    runner = AuthorRunner(tmp_path)
    plan = runner.plan(task(), worker_count=8, worker_output_tokens=8192, worker_max_turns=4)
    assert plan["accepted"]
    assert plan["allocation_risk"]
    assert plan["worker_total_output_allocation_tokens"] == 262144
    assert plan["maximum_request_bytes"] is None
    assert plan["exact_request_bytes"] is None
    assert plan["minimum_request_bytes"] < runner.input_budget
    bounded = runner.plan(task(), worker_count=8, worker_output_tokens=8192, worker_max_turns=4,
                          worker_handoff_bytes=16384)
    assert bounded["maximum_request_bytes"] > runner.input_budget
    assert bounded["upper_bound_condition"]
    assert not bounded["accepted"]
    tiny = AuthorRunner(tmp_path / "tiny", input_budget=1)
    assert not tiny.plan(task(), worker_count=1, worker_output_tokens=1, worker_max_turns=1)["accepted"]
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_invalid_planning_allocations_are_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        AuthorRunner(tmp_path).plan(task(), worker_count=bad, worker_output_tokens=1, worker_max_turns=1)
