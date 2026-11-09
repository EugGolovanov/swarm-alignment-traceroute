"""Exchange tests use explicit fake transports and never subscription credentials."""
import copy
import json

import pytest

from little_pom.cards import ASTRA_MODEL, CardWorkflow
from little_pom.cards.fixtures import DEFAULT_VOCABULARY
from little_pom.contracts import sha256_hash
from little_pom.evidence import EvidenceRegistry
from little_pom.measurement import EvaluationExporter, EvaluationImporter, measurement_policy
from little_pom.runtime.client import SwarmClient
from little_pom.runtime.storage import Ledger
from little_pom.runtime.subscription_exchange import SOL_MODEL, SubscriptionExchange


class FakeSubscription:
    def __init__(self, model, response):
        self.model = model
        self.response = response
        self.calls = []

    def configuration(self):
        return {"backend": "explicit-test-subscription", "model": self.model}

    def validate_request(self, request):
        if request["model"] != self.model:
            raise ValueError("Exact backend selector mismatch")

    def send(self, request, schema_id, execution_id):
        self.calls.append((copy.deepcopy(request), schema_id, execution_id))
        if isinstance(self.response, Exception):
            raise self.response
        return {"model": self.model, "output_text": self.response, "status": "completed"}


def exchange(root, model, response, fault_hook=None):
    backend = FakeSubscription(model, response)
    client = SwarmClient(Ledger(root / "ledger"), backend, fault_hook=fault_hook)
    return SubscriptionExchange(root / "exchange", client), backend


def card_packet(root):
    workflow = CardWorkflow(root, DEFAULT_VOCABULARY, validator_model=ASTRA_MODEL)
    packet = workflow.export("applicability", {
        "task": {"schema_version": "1.0", "id": "task", "task_id": "task",
                 "goal": "Check the measurement", "allowed_inputs": [],
                 "snapshot_hash": "0" * 64, "constraints": []},
        "defect_types": ["unmet_precondition"], "assignment": "unmet_precondition"})[0]
    response = {"model": ASTRA_MODEL, "mode": "subscription",
                "validated_at": "2026-11-08T12:34:56Z", "trace": "Explicit test judgment.",
                "applicability": {"unmet_precondition": True}, "assignment": "unmet_precondition"}
    return workflow, packet, response


def evaluation_packet(root, lane="primary"):
    review = {"schema_version": "1.0", "id": "review-private", "status": "claim",
              "claim": "The assay needs an unavailable reagent (citation).",
              "evidence_ids": ["citation"], "decision_relevance_text": "Obtain the reagent first."}
    evidence = {"reviews": {review["id"]: {
        "task_id": "private-task", "task": "Choose an assay.", "card": "Run the assay.",
        "condition": "Fresh", "variant": "valid", "panel_id": "private-panel",
        "reviewer_id": "private-worker", "target_defect": "unmet_precondition"}},
        "items": {"citation": {"text": "No reagent remains."},
                  "uncited-secret": {"text": "Never send this private context."}}}
    manifest = EvaluationExporter(root).export([review], evidence, 17,
        measurement_policy(SOL_MODEL, ASTRA_MODEL, "subscription", "2026-11-08"))
    pid = manifest["packet_ids"][lane][0]
    request = json.loads((root / "subscription" / f"{pid}.json").read_text())
    label = {"schema_version": "1.0", "id": pid, "significant_claim": True,
             "normalized_type": "unmet_precondition", "evidence_support": True,
             "decision_relevance": True, "uncertain": False, "rationale": "Explicit test label."}
    return request, label


def test_card_dispatch_freezes_request_and_reuses_import(tmp_path):
    workflow, packet, response = card_packet(tmp_path / "run")
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, json.dumps(response))
    first = adapter.dispatch_card(workflow, packet)
    assert first["status"] == "ok", first
    assert adapter.dispatch_card(workflow, packet) == first
    assert len(backend.calls) == 1
    request, schema, eid = backend.calls[0]
    assert schema == "SubscriptionCardResponse"
    assert request["packet"] == packet
    assert request["instructions"] == packet["prompt"]
    assert request["history"] == []
    assert request["config"] == {"reasoning_effort": "medium"}
    assert request["output_budget"] == 8192
    frozen = json.loads((adapter.root / "requests" / f"{Ledger.key(eid)}.json").read_text())
    assert frozen["exported"] == packet
    assert workflow.snapshot("task")["applicability"]["response"] == response
    assert len(list((workflow.root / "cards" / "traces").glob("*.json"))) == 1
    changed = adapter.dispatch_card(workflow, packet, output_budget=8191)
    assert changed["error"]["code"] == "request_hash_conflict"
    assert len(backend.calls) == 1


@pytest.mark.parametrize("lane", ["primary", "retest", "calibration_a", "calibration_b"])
def test_evaluation_is_blind_and_preserves_original_raw_provenance(tmp_path, lane):
    root = tmp_path / "measurement"
    exported, label = evaluation_packet(root, lane)
    raw = "\n  " + json.dumps(label, indent=2) + "\n"
    adapter, backend = exchange(tmp_path, exported["model"], raw)
    result = adapter.dispatch_evaluation(root, exported)
    assert result["status"] == "ok", result
    assert adapter.dispatch_evaluation(root, exported) == result
    assert len(backend.calls) == 1
    request, schema, eid = backend.calls[0]
    assert schema == "EvalLabel"
    assert request["packet"] == exported["packet"]
    assert request["instructions"] == exported["prompt"]
    assert request["history"] == []
    assert request["output_budget"] == 2048
    assert request["task_id"] == exported["packet"]["id"]
    text = json.dumps(request)
    for private in ("private-task", "private-worker", "private-panel", "review-private",
                    "uncited-secret", "Never send", "Fresh", "variant", "context_id"):
        assert private not in text
    provenance = result["data"]["provenance"]
    assert provenance["raw_response"] == raw
    assert result["data"]["id"] == label["id"]
    for key in ("model", "mode", "date", "context_id", "prompt_sha256"):
        assert provenance[key] == exported[key]
    assert adapter.client.ledger.lookup(eid)["response"]["raw_response"]["output_text"] == raw
    assert len(list((root / "attempts").glob("*.json"))) == 1


@pytest.mark.parametrize("raw", ["not json", "{\"model\":\"Astra\"}"])
def test_invalid_output_is_terminal_without_repair(tmp_path, raw):
    workflow, packet, _ = card_packet(tmp_path / "run")
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, raw)
    first = adapter.dispatch_card(workflow, packet)
    assert first["status"] == "incomplete"
    assert adapter.dispatch_card(workflow, packet) == first
    assert len(backend.calls) == 1
    assert "applicability" not in workflow.snapshot("task")


def test_ambiguous_remote_failure_never_dispatches_again(tmp_path):
    workflow, packet, _ = card_packet(tmp_path / "run")
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, RuntimeError("transport failed"))
    first = adapter.dispatch_card(workflow, packet)
    assert first["error"]["code"] == "ambiguous_remote_execution"
    assert adapter.dispatch_card(workflow, packet) == first
    assert len(backend.calls) == 1


@pytest.mark.parametrize("checkpoint,expected", [("after_send", "incomplete"), ("after_response", "ok")])
def test_transport_crash_resumes_only_durable_response(tmp_path, checkpoint, expected):
    workflow, packet, response = card_packet(tmp_path / "run")
    def crash(name):
        if name == checkpoint:
            raise RuntimeError("simulated process crash")
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, json.dumps(response), crash)
    with pytest.raises(RuntimeError):
        adapter.dispatch_card(workflow, packet)
    adapter.client.fault_hook = None
    assert adapter.dispatch_card(workflow, packet)["status"] == expected
    assert len(backend.calls) == 1


def test_card_import_completion_recovers_without_duplicate_trace(tmp_path, monkeypatch):
    workflow, packet, response = card_packet(tmp_path / "run")
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, json.dumps(response))
    original = workflow.import_response
    def crash_after_import(*args):
        result = original(*args)
        assert result["status"] == "ok"
        raise RuntimeError("process crash after accepted card import")
    monkeypatch.setattr(workflow, "import_response", crash_after_import)
    with pytest.raises(RuntimeError):
        adapter.dispatch_card(workflow, packet)
    monkeypatch.setattr(workflow, "import_response", original)
    result = adapter.dispatch_card(workflow, packet)
    assert result["status"] == "ok"
    assert result["data"]["assignment"] == "unmet_precondition"
    assert len(backend.calls) == 1
    assert len(list((workflow.root / "cards" / "traces").glob("*.json"))) == 1


def test_aliased_card_import_crash_recovers_original_response_identity(tmp_path, monkeypatch):
    workflow, applicability, applicable_response = card_packet(tmp_path / "run")
    assert workflow.import_response(applicability["id"], applicable_response)["status"] == "ok"
    sources = tmp_path / "inputs"
    sources.mkdir()
    (sources / "measurement.txt").write_text("Energy E1 is 3 eV; E2 is 5 eV.")
    registry = EvidenceRegistry(tmp_path / "evidence", "task", sources)
    evidence = registry.register("measurement.txt", "Energy E1 is 3 eV; E2 is 5 eV.", "worker")
    proposal = {"schema_version": "1.0", "id": "proposal", "task_id": "task",
                "proposed_action": "Measure the E1 to E2 transition.",
                "uncertainty": "The energy difference needs confirmation.",
                "grounds": ["A recorded spectrum supports a bounded repeat."],
                "expected_decision_impact": "Choose the excitation energy.",
                "scale_budget": "One repeat.", "preconditions": ["Calibrate the spectrum."],
                "evidence_ids": [evidence["evidence_id"]]}
    packet = workflow.export("valid", {"task": applicability["inputs"]["task"], "q0": proposal,
        "evidence": registry.evidence(), "provenance": registry.provenance()})[0]
    response = {"model": ASTRA_MODEL, "mode": "subscription",
                "validated_at": "2026-11-09T12:34:56Z",
                "trace": "The recorded E1 and E2 levels support a bounded repeat; see [[ev:1]].",
                "confirmed_valid": True, "supporting_evidence_verified": True,
                "card": {**proposal, "id": "validated-proposal", "proposal_id": "proposal",
                         "variant": "valid", "evidence_ids": ["[[ev:1]]"]}}
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, json.dumps(response))
    original = workflow.import_response
    accepted_result = {}

    def crash_after_import(*args):
        result = original(*args)
        assert result["status"] == "ok", result
        accepted_result.update(result)
        raise RuntimeError("process crash after canonical card freeze")

    monkeypatch.setattr(workflow, "import_response", crash_after_import)
    with pytest.raises(RuntimeError, match="after canonical card freeze"):
        adapter.dispatch_card(workflow, packet)
    frozen = workflow.snapshot("task")["valid"]
    assert frozen["raw_response_sha256"] == sha256_hash(response)
    assert frozen["response"] != response
    assert frozen["card"]["evidence_ids"] == [evidence["evidence_id"]]
    assert frozen["card"]["proposed_action"] == "Measure the E1 to E2 transition."
    trace_count = len(list((workflow.root / "cards" / "traces").glob("*.json")))
    monkeypatch.setattr(workflow, "import_response", original)
    recovered = adapter.dispatch_card(workflow, packet)
    assert recovered == accepted_result
    assert adapter.dispatch_card(workflow, packet) == recovered
    assert len(backend.calls) == 1
    assert len(list((workflow.root / "cards" / "traces").glob("*.json"))) == trace_count


def test_import_completion_recovers_without_duplicate_attempt(tmp_path, monkeypatch):
    root = tmp_path / "measurement"
    exported, label = evaluation_packet(root)
    adapter, backend = exchange(tmp_path, SOL_MODEL, json.dumps(label))
    original = EvaluationImporter.import_response
    def crash_after_import(self, *args):
        result = original(self, *args)
        assert result["status"] == "ok"
        raise RuntimeError("process crash after accepted import")
    monkeypatch.setattr(EvaluationImporter, "import_response", crash_after_import)
    with pytest.raises(RuntimeError):
        adapter.dispatch_evaluation(root, exported)
    monkeypatch.setattr(EvaluationImporter, "import_response", original)
    assert adapter.dispatch_evaluation(root, exported)["status"] == "ok"
    assert len(backend.calls) == 1
    assert len(list((root / "attempts").glob("*.json"))) == 1


def test_interrupted_import_without_acceptance_is_not_retried(tmp_path, monkeypatch):
    workflow, packet, response = card_packet(tmp_path / "run")
    adapter, backend = exchange(tmp_path, ASTRA_MODEL, json.dumps(response))
    def crash(*args):
        raise RuntimeError("crash before import acceptance")
    monkeypatch.setattr(workflow, "import_response", crash)
    with pytest.raises(RuntimeError):
        adapter.dispatch_card(workflow, packet)
    result = adapter.dispatch_card(workflow, packet)
    assert result["error"]["code"] == "ambiguous_import"
    assert len(backend.calls) == 1


def test_wrong_evaluation_id_is_not_repaired(tmp_path):
    root = tmp_path / "measurement"
    exported, label = evaluation_packet(root)
    label["id"] = "wrong-id"
    adapter, backend = exchange(tmp_path, SOL_MODEL, json.dumps(label))
    result = adapter.dispatch_evaluation(root, exported)
    assert result["error"]["code"] == "invalid_response"
    assert adapter.dispatch_evaluation(root, exported) == result
    assert len(backend.calls) == 1
    assert EvaluationImporter(root).labels() == []


@pytest.mark.parametrize("field,value", [("model", ASTRA_MODEL), ("date", "2026-11-09"),
    ("mode", "api"), ("context_id", "different"), ("prompt_sha256", "0" * 64)])
def test_modified_export_is_rejected_before_dispatch(tmp_path, field, value):
    root = tmp_path / "measurement"
    exported, label = evaluation_packet(root)
    adapter, backend = exchange(tmp_path, SOL_MODEL, json.dumps(label))
    exported[field] = value
    with pytest.raises(ValueError, match="immutable export"):
        adapter.dispatch_evaluation(root, exported)
    assert backend.calls == []


def test_wrong_backend_is_not_relabelled_or_retried(tmp_path):
    workflow, packet, response = card_packet(tmp_path / "run")
    adapter, backend = exchange(tmp_path, SOL_MODEL, json.dumps(response))
    result = adapter.dispatch_card(workflow, packet)
    assert result["error"]["code"] == "invalid_request"
    assert adapter.dispatch_card(workflow, packet) == result
    assert backend.calls == []
