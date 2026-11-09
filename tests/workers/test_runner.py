import copy
import json

import pytest

from little_pom.contracts import canonical_json, sha256_bytes, sha256_hash
from little_pom.sandbox import Sandbox
from little_pom.workers import FROZEN_SUBTASKS, WorkerRunner, author_handoff, blind_context, fork_state
from little_pom.workers.fixtures import eight_state_fixture, worker_responses


class RecordingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, request, schema_id, execution_id):
        self.calls.append((copy.deepcopy(request), schema_id, execution_id))
        answer = self.responses.pop(0)
        if "status" in answer:
            return copy.deepcopy(answer)
        return {"status": "ok", "data": copy.deepcopy(answer), "error": None}


def task():
    return {"schema_version": "1.0", "id": "task-record", "task_id": "task",
            "goal": "Check baseline measurements.", "allowed_inputs": ["input/data.txt"],
            "constraints": [], "snapshot_hash": sha256_bytes(b"baseline: 10\n")}


def turn(**fields):
    return {"schema_version": "1.0", "id": "response", "task_id": "task", "tool_requests": [],
            "result": "", "limitations": [], "evidence": [], "done": False, **fields}


def success_responses():
    return worker_responses("task", "input/data.txt", "baseline: 10", "The verified baseline measurement has value ten.")


def fake_sandbox():
    return Sandbox(backend="fake", fake_inputs={"input/data.txt": "baseline: 10\n"})


def test_real_loop_passes_tool_results_in_next_local_history(tmp_path):
    client = RecordingClient(success_responses())
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["status"] == "ok", result
    state = result["data"]
    assert state["output"]["complete"]
    assert state["output"]["turns_used"] == 2
    assert client.calls[0][0]["history"] == []
    assert "baseline: 10" in client.calls[1][0]["history"][1]["content"]
    assert state["evidence"][0]["fragment"] == "baseline: 10"
    assert state["artifact_provenance"]
    assert all(call[0]["model"] == "openai-codex/gpt-6-luna" for call in client.calls)
    assert all(call[0]["config"] == {"reasoning_effort": "medium"} for call in client.calls)
    assert all("previous_response_id" not in call[0] and "store" not in call[0] for call in client.calls)


def test_eight_real_states_share_only_common_evidence(tmp_path):
    fixture = eight_state_fixture(tmp_path)
    states = fixture["states"]
    assert len(states) == 8
    assert len({state["worker_id"] for state in states}) == 8
    assert len({state["subtask_type"] for state in states}) == 8
    assert len(fixture["shared_evidence_ids"]) == 1
    assert len(fixture["evidence"]) == 9
    shared = fixture["shared_evidence_ids"][0]
    assert len([record for record in fixture["provenance"] if record["evidence_id"] == shared]) == 8
    for state in states:
        assert state["output"]["turns_used"] == 3
        assert shared in state["evidence_ids"]
        own_private = fixture["private_evidence_ids"][state["worker_id"]]
        assert len(own_private) == 1
        for other in states:
            if other["worker_id"] != state["worker_id"]:
                assert own_private[0] not in other["evidence_ids"]
                assert own_private[0] not in canonical_json(other["history"]).decode()


def test_shared_sandbox_instance_has_no_cross_worker_files_or_history(tmp_path):
    sandbox = fake_sandbox()
    first_client = RecordingClient([
        turn(tool_requests=[{"operation": "write", "path": "out/secret.txt", "content": "branch-only secret"}]),
        turn(result="This branch generated a private artifact for analysis.", evidence=[{"source": "out/secret.txt", "fragment": "branch-only secret"}], done=True),
    ])
    first = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], first_client, sandbox, None)
    assert first["status"] == "ok", first
    second_client = RecordingClient([
        turn(tool_requests=[{"operation": "read", "path": "out/secret.txt"}]),
        turn(limitations=["The requested artifact is absent from this independent workspace."], done=True),
    ])
    second = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[1], second_client, sandbox, None)
    assert second["status"] == "ok", second
    assert second_client.calls[0][0]["history"] == []
    assert "branch-only secret" not in canonical_json(second["data"]).decode()
    assert not second["data"]["evidence_ids"]


def test_turn_budget_is_enforced_and_failure_state_is_auditable(tmp_path):
    client = RecordingClient([turn() for _ in range(5)])
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["status"] == "incomplete"
    assert result["data"] is None
    assert result["error"]["code"] == "turn_budget_exhausted"
    assert len(client.calls) == 4
    with open(result["error"]["state_path"]) as stream:
        state = json.load(stream)
    assert not state["output"]["complete"]
    assert state["output"]["turns_used"] == 4
    with pytest.raises(ValueError, match="Incomplete"):
        blind_context(state)


def test_completion_on_fourth_turn_is_allowed(tmp_path):
    responses = success_responses()
    client = RecordingClient([responses[0], turn(), turn(), responses[1]])
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["status"] == "ok", result
    assert result["data"]["output"]["turns_used"] == 4


def test_model_error_is_never_a_successful_worker(tmp_path):
    client = RecordingClient([{"status": "incomplete", "data": None, "error": {"code": "timeout", "message": "raw failure"}}])
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["status"] == "incomplete"
    assert result["error"]["code"] == "model_call_incomplete"
    state = json.loads(__import__("pathlib").Path(result["error"]["state_path"]).read_text())
    assert not state["output"]["complete"]
    assert state["output"]["result"] == ""


def test_input_budget_fails_before_call_without_truncation(tmp_path):
    client = RecordingClient(success_responses())
    result = WorkerRunner(tmp_path, input_budget=1).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["status"] == "incomplete"
    assert result["error"]["code"] == "input_budget_exceeded"
    assert not client.calls


def test_fabricated_fragment_and_unobserved_source_are_rejected(tmp_path):
    responses = success_responses()
    responses[-1]["evidence"][0]["fragment"] = "baseline: 999"
    result = WorkerRunner(tmp_path / "bad-fragment").run(task(), FROZEN_SUBTASKS[0], RecordingClient(responses), fake_sandbox(), None)
    assert result["status"] == "incomplete"
    result = WorkerRunner(tmp_path / "unobserved").run(task(), FROZEN_SUBTASKS[0], RecordingClient([success_responses()[-1]]), fake_sandbox(), None)
    assert result["error"]["code"] == "unobserved_evidence"


def test_unverified_limitation_or_empty_result_cannot_complete(tmp_path):
    client = RecordingClient([turn(limitations=["I could not investigate the baseline for an unspecified reason."], done=True)])
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["error"]["code"] == "insubstantial_output"


def test_review_forks_never_contaminate_frozen_state_or_other_repeats(tmp_path):
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], RecordingClient(success_responses()), fake_sandbox(), None)
    state = result["data"]
    original = canonical_json(state)
    valid = fork_state(state)
    defective = fork_state(state)
    valid["history"].append({"role": "assistant", "content": "valid review result"})
    defective["output"]["limitations"].append("def review result")
    repeat = fork_state(state)
    assert canonical_json(state) == original == canonical_json(repeat)
    assert "valid review result" not in canonical_json(defective).decode()
    with pytest.raises(ValueError, match="intact"):
        fork_state(valid)
    context = blind_context(state)
    assert set(context) == {"summary", "history", "evidence"}
    assert state["worker_id"] not in canonical_json(context).decode()
    assert set(author_handoff(state)) == {"result", "limitations", "evidence"}


def test_restart_reuses_frozen_state_without_sampling_again(tmp_path):
    runner = WorkerRunner(tmp_path)
    first = runner.run(task(), FROZEN_SUBTASKS[0], RecordingClient(success_responses()), fake_sandbox(), None)
    client = RecordingClient([])
    second = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert second == first
    assert not client.calls
    mismatch = WorkerRunner(tmp_path, output_budget=999).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert mismatch["error"]["code"] == "resume_configuration_mismatch"
    mismatch = WorkerRunner(tmp_path, model="qwen3.5-397b").run(
        task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert mismatch["error"]["code"] == "resume_configuration_mismatch"
    assert not client.calls


@pytest.mark.parametrize("budget", [0, 5, True, -1])
def test_invalid_turn_budgets_are_rejected(tmp_path, budget):
    with pytest.raises(ValueError):
        WorkerRunner(tmp_path, max_turns=budget)


def test_frozen_subtask_specifications_cannot_be_mutated():
    with pytest.raises(TypeError):
        FROZEN_SUBTASKS[0]["text"] = "A different post-outcome task"


def test_truncated_tool_output_is_not_treated_as_a_limitation(tmp_path):
    client = RecordingClient(success_responses())
    runner = WorkerRunner(tmp_path, sandbox_policy={"output_limit_bytes": 4})
    result = runner.run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert result["status"] == "incomplete"
    assert result["error"]["code"] == "tool_output_truncated"
    assert len(client.calls) == 1


def test_growing_history_is_not_silently_trimmed_to_fit_budget(tmp_path):
    content = "large observed source " * 200
    sandbox = Sandbox(backend="fake", fake_inputs={"input/data.txt": content})
    client = RecordingClient(worker_responses("task", "input/data.txt", content, "A grounded final result from the large source."))
    runner = WorkerRunner(tmp_path, input_budget=8000)
    result = runner.run(task(), FROZEN_SUBTASKS[0], client, sandbox, None)
    assert result["status"] == "incomplete"
    assert result["error"]["code"] == "input_budget_exceeded"
    assert len(client.calls) == 1
    with open(result["error"]["state_path"]) as stream:
        saved = json.load(stream)
    assert content in saved["history"][1]["content"]


def test_failed_checkpoint_cannot_be_automatically_resampled(tmp_path):
    runner = WorkerRunner(tmp_path, max_turns=1)
    first = runner.run(task(), FROZEN_SUBTASKS[0], RecordingClient([turn()]), fake_sandbox(), None)
    assert first["status"] == "incomplete"
    client = RecordingClient(success_responses())
    second = runner.run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert second["error"]["code"] == "previous_worker_incomplete"
    assert not client.calls


def test_default_checkpoint_publication_recovers_without_new_model_samples(tmp_path, monkeypatch):
    from little_pom.runtime import Ledger, MockBackend, SwarmClient, storage

    backend = MockBackend(responses=success_responses())
    client = SwarmClient(ledger=Ledger(tmp_path / "ledger"), backend=backend)
    real_link = storage.os.link

    def interrupted_link(source, destination, **kwargs):
        if destination.name == "state.json":
            raise OSError("Simulated interruption publishing worker state")
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(storage.os, "link", interrupted_link)
    first = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert first["error"]["code"] == "state_persistence_failed"
    assert not list((tmp_path / "workers").glob("*/state.json"))
    assert len(backend.calls) == 2
    monkeypatch.setattr(storage.os, "link", real_link)
    recovered = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox(), None)
    assert recovered["status"] == "ok", recovered
    assert len(backend.calls) == 2
    assert fork_state(recovered["data"]) == recovered["data"]


def test_relative_input_aliases_preserve_source_kind(tmp_path):
    responses = worker_responses("task", "./input/data.txt", "baseline: 10", "The baseline remains an immutable input observation.")
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], RecordingClient(responses), fake_sandbox(), None)
    assert result["status"] == "ok", result
    assert result["data"]["evidence"][0]["source"] == "input/data.txt"
    assert result["data"]["evidence"][0]["kind"] == "source"


def test_final_response_must_observe_tools_in_an_earlier_turn(tmp_path):
    response = success_responses()[1]
    response["tool_requests"] = [{"operation": "read", "path": "input/data.txt"}]
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], RecordingClient([response]), fake_sandbox(), None)
    assert result["error"]["code"] == "unobserved_final_tools"


def test_generated_artifacts_survive_workspace_cleanup_without_becoming_claims(tmp_path):
    responses = success_responses()
    responses[0]["tool_requests"].append({
        "operation": "write", "path": "out/uncited.txt", "content": "Generated artifact, not a scientific claim."
    })
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], RecordingClient(responses), fake_sandbox(), None)
    assert result["status"] == "ok", result
    state = result["data"]
    generated = next(record for record in state["artifact_provenance"] if record["kind"] == "generated")
    raw = (tmp_path / generated["stored_path"]).read_bytes()
    assert raw == b"Generated artifact, not a scientific claim."
    assert sha256_bytes(raw) == generated["sha256"]
    assert all(entry["source"] != "out/uncited.txt" for entry in state["evidence"])


def test_external_store_checkpoint_recovers_interrupted_local_mirror(tmp_path, monkeypatch):
    from little_pom.runtime import ArtifactStore, storage

    local = tmp_path / "local"
    mirror = ArtifactStore(tmp_path / "mirror")
    real_link = storage.os.link

    def interrupted_link(source, destination, **kwargs):
        if destination.name == "state.json" and destination.is_relative_to(local):
            raise OSError("Simulated interruption publishing local mirror")
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(storage.os, "link", interrupted_link)
    first = WorkerRunner(local).run(task(), FROZEN_SUBTASKS[0], RecordingClient(success_responses()), fake_sandbox(), mirror)
    assert first["error"]["code"] == "state_persistence_failed"
    assert len(list((tmp_path / "mirror" / "workers").glob("*/state.json"))) == 1
    monkeypatch.setattr(storage.os, "link", real_link)
    recording = RecordingClient([])
    recovered = WorkerRunner(local).run(task(), FROZEN_SUBTASKS[0], recording, fake_sandbox(), mirror)
    assert recovered["status"] == "ok", recovered
    assert not recording.calls
    assert len(list((local / "workers").glob("*/state.json"))) == 1


@pytest.mark.parametrize("max_turns", [1, 2, 4])
def test_requests_freeze_actual_environment_and_remaining_budget(tmp_path, max_turns):
    client = RecordingClient([turn() for _ in range(max_turns)])
    result = WorkerRunner(tmp_path, max_turns=max_turns, max_tools_per_turn=3).run(
        task(), FROZEN_SUBTASKS[0], client, fake_sandbox())
    assert result["error"]["code"] == "turn_budget_exhausted"
    with open(result["error"]["state_path"]) as stream:
        state = json.load(stream)
    budget = state["token_budget"]
    environment = budget["environment"]
    assert environment["input_files"] == [{"path": "input/data.txt", "writable": False}]
    assert environment["input_file_count"] == 1
    assert environment["public_task_file"] not in [entry["path"] for entry in environment["input_files"]]
    assert budget["environment_sha256"] == sha256_hash(environment)
    assert budget["max_tools_per_turn"] == 3
    for current, (request, _, _) in enumerate(client.calls, 1):
        assert request["packet"]["environment"] == environment
        assert request["packet"]["worker_protocol_version"] == budget["worker_protocol_version"]
        assert request["packet"]["environment_contract_version"] == budget["environment_contract_version"]
        assert request["packet"]["turn_budget"] == {
            "current_turn": current, "max_turns": max_turns,
            "remaining_turns_after_current": max_turns - current, "max_tools_per_turn": 3,
        }
        assert budget["requests"][current - 1]["request_sha256"] == sha256_hash(request)


def test_empty_input_construction_grounds_requirements_in_real_public_file(tmp_path):
    public_instruction = "Construct an encoder and decoder; exact round trips are required."
    construction = {**task(), "allowed_inputs": [], "instruction": public_instruction}

    class ConstructionClient(RecordingClient):
        def call(self, request, schema_id, execution_id):
            environment = request["packet"]["environment"]
            assert environment["input_file_count"] == 0
            assert environment["input_files"] == []
            assert not environment["input_files_truncated"]
            source = environment["public_task_file"]
            current = request["packet"]["turn_budget"]["current_turn"]
            if current == 1:
                response = turn(tool_requests=[{"operation": "read", "path": source}])
            else:
                observation = json.loads(request["history"][1]["content"])
                assert observation["request"] == {"operation": "read", "path": source}
                assert observation["result"]["stdout"] == public_instruction
                response = turn(
                    result="The specification requires an encoder and decoder with exact round trips; this is a requirement, not measured codec performance.",
                    evidence=[{"source": source, "fragment": public_instruction}], done=True)
            self.responses.append(response)
            return super().call(request, schema_id, execution_id)

    client = ConstructionClient([])
    result = WorkerRunner(tmp_path).run(
        construction, FROZEN_SUBTASKS[2], client, Sandbox(backend="fake", fake_inputs={}))
    assert result["status"] == "ok", result
    state = result["data"]
    assert state["output"]["turns_used"] == 2
    assert state["evidence"][0]["source"] == client.calls[0][0]["packet"]["environment"]["public_task_file"]
    assert state["evidence"][0]["kind"] == "source"
    assert not state["output"]["limitations"]
    assert [entry["role"] for entry in state["history"]] == ["assistant", "tool", "assistant"]


def test_batch_write_then_read_observes_real_generated_file_before_final_turn(tmp_path):
    content = "Derived baseline analysis: ten plus two equals twelve; not a measured experiment."
    client = RecordingClient([
        turn(tool_requests=[
            {"operation": "read", "path": "input/data.txt"},
            {"operation": "write", "path": "out/analysis.txt", "content": content},
            {"operation": "read", "path": "out/analysis.txt"},
        ]),
        turn(result="The analysis derives twelve from the documented baseline ten; no experiment was performed.",
             evidence=[{"source": "out/analysis.txt", "fragment": content}], done=True),
    ])
    result = WorkerRunner(tmp_path, max_turns=2, max_tools_per_turn=3).run(
        task(), FROZEN_SUBTASKS[0], client, fake_sandbox())
    assert result["status"] == "ok", result
    history = client.calls[1][0]["history"]
    assert [json.loads(entry["content"])["request"]["operation"] for entry in history[1:]] == [
        "read", "write", "read"]
    assert json.loads(history[-1]["content"])["result"]["stdout"] == content
    assert result["data"]["evidence"][0]["kind"] == "generated"
    artifact = next(item for item in result["data"]["artifact_provenance"] if item["kind"] == "generated")
    assert (tmp_path / artifact["stored_path"]).read_text() == content
    assert not json.loads(result["data"]["history"][-1]["content"])["tool_requests"]


@pytest.mark.parametrize("source", [".", "out", "list .", "tool result stdout", "task.goal", "packet.task.goal"])
def test_directory_and_tool_observations_are_never_file_evidence(tmp_path, source):
    client = RecordingClient([
        turn(tool_requests=[{"operation": "list", "path": "."}, {"operation": "list", "path": "out"}]),
        turn(result="This unsupported claim tries to cite a directory or tool observation.",
             evidence=[{"source": source, "fragment": "out"}], done=True),
    ])
    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], client, fake_sandbox())
    assert result["error"]["code"] == "unobserved_evidence"
    assert len(client.calls) == 2


def test_empty_input_manifest_and_empty_directory_do_not_verify_limitation(tmp_path):
    construction = {**task(), "allowed_inputs": []}
    client = RecordingClient([
        turn(tool_requests=[{"operation": "list", "path": "out"}]),
        turn(limitations=["No preloaded files were supplied, so I cannot perform this construction task."], done=True),
    ])
    result = WorkerRunner(tmp_path).run(
        construction, FROZEN_SUBTASKS[0], client, Sandbox(backend="fake", fake_inputs={}))
    assert result["error"]["code"] == "insubstantial_output"
    assert len(client.calls) == 2


def test_tool_budget_rejects_whole_batch_without_executing_hidden_tools(tmp_path):
    client = RecordingClient([turn(tool_requests=[
        {"operation": "read", "path": "input/data.txt"},
        {"operation": "write", "path": "out/should-not-exist.txt", "content": "not executed"},
    ])])
    result = WorkerRunner(tmp_path, max_tools_per_turn=1).run(
        task(), FROZEN_SUBTASKS[0], client, fake_sandbox())
    assert result["error"]["code"] == "tool_budget_exceeded"
    with open(result["error"]["state_path"]) as stream:
        state = json.load(stream)
    assert not state["artifact_provenance"]
    assert not state["evidence"]
    assert [entry["role"] for entry in state["history"]] == ["assistant"]


def test_public_task_source_is_not_observed_just_by_receiving_manifest(tmp_path):
    class UnobservedClient(RecordingClient):
        def call(self, request, schema_id, execution_id):
            self.responses.append(turn(
                result="The requirements were supplied, but the public file was never accessed.",
                evidence=[{"source": request["packet"]["environment"]["public_task_file"],
                           "fragment": task()["goal"]}], done=True))
            return super().call(request, schema_id, execution_id)

    result = WorkerRunner(tmp_path).run(task(), FROZEN_SUBTASKS[0], UnobservedClient([]), fake_sandbox())
    assert result["error"]["code"] == "unobserved_evidence"


def test_last_turn_tools_cannot_trigger_implicit_finalization_or_repair(tmp_path):
    client = RecordingClient([
        turn(tool_requests=[{"operation": "read", "path": "input/data.txt"}]),
        success_responses()[1],
    ])
    result = WorkerRunner(tmp_path, max_turns=1).run(
        task(), FROZEN_SUBTASKS[0], client, fake_sandbox())
    assert result["error"]["code"] == "turn_budget_exhausted"
    assert len(client.calls) == 1
    assert len(client.responses) == 1
    with open(result["error"]["state_path"]) as stream:
        state = json.load(stream)
    assert not state["output"]["complete"]
    assert not state["evidence_ids"]
    assert [entry["role"] for entry in state["history"]] == ["assistant", "tool"]
