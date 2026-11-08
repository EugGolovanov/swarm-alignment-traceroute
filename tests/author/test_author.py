import copy

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
            "evidence_ids": ["a" * 64]}


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
    assert request["model"] == "qwen3.8-27b"
    assert set(request["packet"]) == {"task", "handoffs"}
    assert "worker_id" not in canonical_json(request["packet"]).decode()
    assert result["data"]["id"] == execution_id


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
