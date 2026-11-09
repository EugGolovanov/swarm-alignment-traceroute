import copy
import hashlib
import json

import pytest

from little_pom.cards import CardWorkflow, MechanismBuilder, diagnose_exploitability
from little_pom.cards.fixtures import DEFAULT_VOCABULARY, subscription_response
from little_pom.contracts import evidence_id, sha256_hash
from little_pom.profiles import ProfileBuilder


def fixture_inputs():
    task = {"schema_version": "1.0", "id": "task", "task_id": "task", "goal": "Check a limited measurement",
            "allowed_inputs": ["data"], "snapshot_hash": "0" * 64, "constraints": []}
    q0 = {"schema_version": "1.0", "id": "proposal", "task_id": "task", "proposed_action": "Validate the data",
          "uncertainty": "Measurement uncertainty", "grounds": [], "expected_decision_impact": "Decide after validation",
          "scale_budget": "Small budget", "preconditions": [], "evidence_ids": []}
    evidence = []
    for index, text in enumerate(("The hidden sample contains only three observations.", "A separate artifact disproves universality.")):
        source_hash = hashlib.sha256(f"source-{index}".encode()).hexdigest()
        fragment_hash = hashlib.sha256(text.encode()).hexdigest()
        eid = evidence_id(source_hash, fragment_hash)
        evidence.append({"schema_version": "1.0", "id": eid, "task_id": "task", "evidence_id": eid,
                         "source": f"source-{index}", "source_sha256": source_hash, "fragment_sha256": fragment_hash,
                         "fragment": text, "kind": "source"})
    provenance = [{"worker_id": f"w{i}", "evidence_id": e["evidence_id"], "source": e["source"]} for i, e in enumerate(evidence)]
    return {"task": task, "q0": q0, "evidence": evidence, "provenance": provenance}


def apply(workflow, stage, inputs):
    packet = workflow.export(stage, inputs)[0]
    response = subscription_response(packet)
    result = workflow.import_response(packet["id"], response)
    assert result["status"] == "ok", result
    return packet, response, result["data"]


def applicability(workflow, inputs):
    return apply(workflow, "applicability", {"task": inputs["task"],
                 "defect_types": ["unsupported_claim", "budget_error"], "assignment": "unsupported_claim"})


def test_export_import_lifecycle_and_full_immutable_trace(tmp_path):
    inputs = fixture_inputs()
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY)
    with pytest.raises(FileNotFoundError):
        workflow.export("valid", inputs)
    applicability(workflow, inputs)
    workflow.begin_workers("task")
    with pytest.raises(FileNotFoundError):
        workflow.export("def", {"task_id": "task"})
    packet, response, valid = apply(workflow, "valid", inputs)
    assert valid["requirements"]["card_id"] == valid["card"]["id"]
    resumed = CardWorkflow(tmp_path, DEFAULT_VOCABULARY)
    assert resumed.snapshot("task")["requirements"] == valid["requirements"]
    def_packet, def_response, defective = apply(resumed, "def", {"task_id": "task"})
    assert defective["card"]["proposal_id"] == inputs["q0"]["id"]
    assert defective["mechanism"]["informed_private_workers_def"]
    assert all(item["evidence_id"].startswith("[[ev:") for item in def_packet["inputs"]["evidence"])
    assert set(defective["mechanism"]["target_evidence_ids"]) <= {e["evidence_id"] for e in inputs["evidence"]}
    assert resumed.import_response(packet["id"], response)["status"] == "incomplete"
    def_response["card"]["proposed_action"] = "Late alteration"
    assert resumed.import_response(def_packet["id"], def_response)["status"] == "incomplete"
    traces = [json.loads(path.read_text()) for path in (tmp_path / "cards" / "traces").glob("*.json")]
    assert len(traces) == 5
    assert all(record["response"]["trace"] for record in traces)
    assert all(record["packet"]["prompt"] for record in traces)


def test_applicability_is_preoutcome_and_three_tasks_need_not_cover_all_types(tmp_path):
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY)
    base = fixture_inputs()
    for i in range(3):
        task = copy.deepcopy(base["task"])
        task.update(id=f"task-{i}", task_id=f"task-{i}")
        packet, response, data = apply(workflow, "applicability", {"task": task,
            "defect_types": [f"type-{j}" for j in range(6)], "assignment": "type-0"})
        assert data["assignment"] == "type-0"
        workflow.begin_workers(task["id"])
        assert workflow.import_response(packet["id"], response)["status"] == "incomplete"
    with pytest.raises(ValueError, match="forbidden"):
        workflow.export("applicability", {"task": base["task"], "defect_types": ["x"], "assignment": "x", "worker_outputs": []})


def test_failed_applicability_is_not_silently_reassigned(tmp_path):
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY)
    inputs = {"task": fixture_inputs()["task"], "defect_types": ["x", "y"], "assignment": "x"}
    packet = workflow.export("applicability", inputs)[0]
    response = subscription_response(packet)
    response["applicability"]["x"] = False
    assert workflow.import_response(packet["id"], response)["status"] == "incomplete"
    inputs["assignment"] = "y"
    with pytest.raises(ValueError, match="Immutable"):
        workflow.export("applicability", inputs)
    with pytest.raises(FileNotFoundError):
        workflow.begin_workers("task")


def test_single_defect_and_evidence_integrity_required(tmp_path):
    inputs = fixture_inputs()
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY)
    applicability(workflow, inputs)
    apply(workflow, "valid", inputs)
    packet = workflow.export("def", {"task_id": "task"})[0]
    response = subscription_response(packet)
    response["defect_count"] = 2
    assert workflow.import_response(packet["id"], response)["status"] == "incomplete"
    response["defect_count"] = 1
    response["card"]["evidence_ids"] = ["missing-evidence"]
    assert workflow.import_response(packet["id"], response)["status"] == "incomplete"
    assert workflow.import_response(packet["id"], response)["error"]["code"] == "evidence_reference_contract"
    response["card"]["evidence_ids"] = []
    response["confirmations"]["no_second_significant_defect"] = False
    assert workflow.import_response(packet["id"], response)["status"] == "incomplete"


def test_exact_validator_model_preserves_live_contract_and_required_tags(tmp_path):
    from little_pom.cards import ASTRA_MODEL
    inputs = fixture_inputs()
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY, validator_model=ASTRA_MODEL)
    packets = []
    for stage, stage_inputs in (
        ("applicability", {"task": inputs["task"], "defect_types": ["unsupported_claim"],
                           "assignment": "unsupported_claim"}),
        ("valid", inputs),
        ("def", {"task_id": "task"}),
    ):
        packet = workflow.export(stage, stage_inputs)[0]
        assert packet["model"] == ASTRA_MODEL
        assert ASTRA_MODEL in packet["prompt"]
        assert "validated_at" in packet["prompt"]
        response = subscription_response(packet)
        # The explicitly synthetic fixture is not an actual model response.
        response["model"] = ASTRA_MODEL
        result = workflow.import_response(packet["id"], response)
        assert result["status"] == "ok", result
        packets.append(packet)
    assert "ExperimentalCard" in packets[1]["prompt"]
    assert '"proposal_id"' in packets[1]["prompt"]
    assert "visibility_labels" in packets[2]["prompt"]
    state = workflow.snapshot("task")
    assert state["requirements"] == state["valid"]["requirements"]
    assert state["requirements"]["card_id"] == state["valid"]["card"]["id"]
    assert state["def"]["card"]["proposal_id"] == state["valid"]["card"]["proposal_id"]


@pytest.mark.parametrize("change", [
    {"model": "Astra"}, {"model": "openai-codex/gpt-6.1-sol"},
    {"mode": "api"}, {"trace": "  "}, {"validated_at": ""},
])
def test_live_validator_provenance_is_never_relabelled(tmp_path, change):
    from little_pom.cards import ASTRA_MODEL
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY, validator_model=ASTRA_MODEL)
    packet = workflow.export("applicability", {"task": fixture_inputs()["task"],
        "defect_types": ["unsupported_claim"], "assignment": "unsupported_claim"})[0]
    response = {**subscription_response(packet), "model": ASTRA_MODEL, **change}
    assert workflow.import_response(packet["id"], response)["status"] == "incomplete"
    assert "applicability" not in workflow.snapshot("task")
    trace = json.loads(next((tmp_path / "cards" / "traces").glob("*.json")).read_text())
    assert trace["response"] == response


def mechanism_fixture():
    inputs = fixture_inputs()
    valid = {**inputs["q0"], "id": "valid", "proposal_id": "proposal", "variant": "valid"}
    defective = {**valid, "id": "def", "variant": "def", "proposed_action": "Adopt the result universally"}
    ids = [e["evidence_id"] for e in inputs["evidence"]]
    cards = {"task": inputs["task"], "valid": valid, "def": defective, "target_defect_type": "unsupported_claim",
             "target_evidence_ids": ids, "provenance": inputs["provenance"]}
    labels = {v: {eid: {"visible": False, "disputed": False, "reason": "Content absent from fresh material"} for eid in ids}
              for v in ("valid", "def")}
    return cards, inputs["evidence"], labels


def test_private_valid_def_strict_are_content_not_ids():
    cards, evidence, labels = mechanism_fixture()
    first, second = [e["evidence_id"] for e in evidence]
    cards["valid"]["grounds"] = [evidence[0]["fragment"]]
    cards["def"]["evidence_ids"] = [first, second]
    labels["valid"][first]["visible"] = True
    result = MechanismBuilder().build(cards, evidence, labels)
    assert result["private_target_evidence_valid"] == [second]
    assert set(result["private_target_evidence_def"]) == {first, second}
    assert result["private_target_evidence_strict"] == [second]
    assert result["informed_private_workers_valid"] == ["w1"]
    assert result["informed_private_workers_def"] == ["w0", "w1"]


def test_paraphrase_labels_and_disputes_are_explicit_conservative():
    cards, evidence, labels = mechanism_fixture()
    first, second = [e["evidence_id"] for e in evidence]
    cards["valid"]["grounds"] = [evidence[0]["fragment"]]
    labels["def"][second] = {"visible": True, "disputed": False, "reason": "Equivalent content is paraphrased in public material"}
    labels["def"][first]["disputed"] = True
    result = MechanismBuilder().build(cards, evidence, labels)
    assert result["private_target_evidence_def"] == []
    assert "visibility_disputed" in result["diagnostics"]
    assert "no_private_def_evidence" in result["diagnostics"]
    assert len(result["visibility_disputes"]) == 2
    del labels["valid"][second]
    with pytest.raises(ValueError, match="every target"):
        MechanismBuilder().build(cards, evidence, labels)


def test_evidence_forgery_is_rejected():
    cards, evidence, labels = mechanism_fixture()
    evidence[0]["fragment"] = "Forged content without hash update"
    with pytest.raises(ValueError):
        MechanismBuilder().build(cards, evidence, labels)


def test_empty_exploitability_is_a_diagnostic_not_a_go_gate():
    cards, evidence, labels = mechanism_fixture()
    mechanism = MechanismBuilder().build(cards, evidence, labels)
    requirements = {"schema_version": "1.0", "id": "req", "task_id": "task", "tags": [], "source": "valid",
                    "card_id": "valid", "frozen": True, "diagnostics": ["routing_representation_failure"]}
    profiles = [{"schema_version": "1.0", "id": "profile", "task_id": "task", "worker_id": "w0", "tags": [], "tag_sources": {}}]
    result = diagnose_exploitability(mechanism, profiles, requirements)
    assert result["informed_exploitable_workers"] == []
    assert result["diagnostics"] == ["empty_exploitability", "routing_representation_failure"]


def test_panel_freeze_closes_pending_card_import(tmp_path):
    from little_pom.routing import PanelBuilder

    inputs = fixture_inputs()
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY)
    applicability(workflow, inputs)
    _, _, valid = apply(workflow, "valid", inputs)
    packet = workflow.export("def", {"task_id": "task"})[0]
    profiles = [
        {"schema_version": "1.0", "id": f"profile-{i}", "task_id": "task",
         "worker_id": f"w{i}", "tags": [], "tag_sources": {}}
        for i in range(2)
    ]
    PanelBuilder(tmp_path).build(["w0", "w1"], profiles, valid["requirements"], 1)
    result = workflow.import_response(packet["id"], subscription_response(packet))
    assert result["status"] == "incomplete"
    assert "panel freeze" in result["error"]["message"]


def test_runtime_freeze_store_is_used_and_resumable(tmp_path):
    from little_pom.runtime import FreezeStore
    from little_pom.routing import PanelBuilder

    store = FreezeStore(tmp_path)
    workflow = CardWorkflow(tmp_path, DEFAULT_VOCABULARY, store)
    inputs = fixture_inputs()
    applicability(workflow, inputs)
    workflow.begin_workers("task")
    workflow.begin_workers("task")
    _, _, valid = apply(workflow, "valid", inputs)
    apply(workflow, "def", {"task_id": "task"})
    profiles = [
        {"schema_version": "1.0", "id": f"profile-{i}", "task_id": "task",
         "worker_id": f"w{i}", "tags": [], "tag_sources": {}}
        for i in range(2)
    ]
    panels = PanelBuilder(tmp_path, store).build(["w0", "w1"], profiles, valid["requirements"], 1)
    assert PanelBuilder(tmp_path, store).build(["w0", "w1"], profiles, valid["requirements"], 1) == panels
    assert list((tmp_path / ".freezes").glob("*.json"))
