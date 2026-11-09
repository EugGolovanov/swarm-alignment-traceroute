"""Configuration and immutable freeze lifecycle tests; no external calls."""
from copy import deepcopy
import json

import pytest

from little_pom.contracts import (
    ConfigFreeze, ContractError, PILOT_TASK_IDS, default_config_path, load_config,
    sha256_hash, validate_config,
)


def config():
    return load_config(default_config_path())


def d0_config():
    value = config()
    value["d0_task_id"] = "explicit-development-task"
    return value


def finish_d0(store, value):
    store.freeze_d0(value, value["d0_task_id"])
    store.record_d0_result({"status": "ok", "data": {"task_id": value["d0_task_id"], "trace": "observed"}, "error": None})
    store.open_fix_window()


def test_defaults_are_explicit_three_task_mock_not_live_confirmation():
    value = config()
    assert set(value["task_ids"]) == set(PILOT_TASK_IDS)
    assert value["d0_task_id"] is None
    assert value["mode"] == "mock"
    assert value["full_pom_go_gates"] is False
    assert value["models"]["swarm"]["model"] == "qwen3.5-397b"
    assert value["models"]["swarm"]["roles"] == ["author", "reviewer"]
    assert value["models"]["worker"] == {
        "model": "openai-codex/gpt-6-luna", "transport": "omp_subscription",
        "mode": "subscription", "snapshot_id": None, "roles": ["worker"],
        "parameters": {"reasoning_effort": "medium"},
    }
    assert value["models"]["astra"]["transport"] == "subscription"
    assert value["models"]["terra"]["transport"] == "subscription"
    assert all(model["snapshot_id"] is None for model in value["models"].values())
    assert value["analysis_policy"]["human_validation"] is False
    assert value["analysis_policy"]["astra_ba_min_class_count"] == 8
    assert value["analysis_policy"]["bootstrap_unit"] == "episode"


def test_default_measurement_policy_matches_frozen_identity():
    value = config()["measurement_policy"]
    body = {key: child for key, child in value.items() if key != "id"}
    assert value["id"] == sha256_hash(body)[:32]
    assert value["retest_rounding"] == "ceiling"


def test_no_automatic_d0_selection(tmp_path):
    store = ConfigFreeze(tmp_path)
    value = config()
    with pytest.raises(ContractError):
        store.freeze_d0(value, "")
    with pytest.raises(ContractError):
        store.freeze_d0(value, value["task_ids"][0])
    with pytest.raises(ContractError):
        store.freeze_d0(value, "unrecorded-task")
    assert not (tmp_path / "d0_freeze_manifest.json").exists()


def test_live_final_requires_d0_then_result_then_fix_window(tmp_path):
    store = ConfigFreeze(tmp_path)
    value = d0_config()
    with pytest.raises(ContractError):
        store.freeze_final(value)
    store.freeze_d0(value, value["d0_task_id"])
    with pytest.raises(ContractError):
        store.open_fix_window()
    with pytest.raises(ContractError):
        store.record_d0_result({"status": "incomplete", "data": None, "error": {"code": "missing"}})
    with pytest.raises(ContractError):
        store.record_d0_result({"status": "ok", "data": {"task_id": "wrong-task"}, "error": None})
    store.record_d0_result({"status": "ok", "data": {"task_id": value["d0_task_id"]}, "error": None})
    with pytest.raises(ContractError):
        store.freeze_final(value)
    store.open_fix_window()
    final = store.freeze_final(value)
    assert final["consumed_task_ids"] == [value["d0_task_id"]]
    assert final["independent_confirmation"] is False


def test_fix_window_allows_changes_but_final_freeze_detects_them(tmp_path):
    store = ConfigFreeze(tmp_path)
    value = d0_config()
    finish_d0(store, value)
    revised = deepcopy(value)
    revised["budgets"]["concurrency"] = 1
    store.freeze_final(revised, {"prompt.txt": "a" * 64})
    assert store.verify("d0", value)
    assert store.verify("final", revised, {"prompt.txt": "a" * 64})
    with pytest.raises(ContractError):
        store.verify("final", value)
    with pytest.raises(ContractError):
        store.verify("final", revised, {"prompt.txt": "b" * 64})
    with pytest.raises(ContractError):
        store.freeze_final(value)
    with pytest.raises(ContractError):
        store.open_fix_window()


def test_stages_do_not_rewrite_final_manifest(tmp_path):
    store = ConfigFreeze(tmp_path)
    value = d0_config()
    finish_d0(store, value)
    store.freeze_final(value)
    path = tmp_path / "final_freeze_manifest.json"
    before = path.read_bytes()
    first = store.add_stage("workers", {"outputs_hash": "a" * 64})
    assert store.add_stage("workers", {"outputs_hash": "a" * 64}) == first
    store.add_stage("panels", {"outputs_hash": "b" * 64})
    assert path.read_bytes() == before
    with pytest.raises(ContractError):
        store.add_stage("workers", {"outputs_hash": "c" * 64})
    with pytest.raises(ContractError):
        store.add_stage("../outside", {})


def test_manifest_tampering_is_detected(tmp_path):
    value = config()
    store = ConfigFreeze(tmp_path)
    store.freeze_mock(value)
    path = tmp_path / "mock_freeze_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["config"]["seed"] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(ContractError):
        store.verify("mock", value)


def test_mock_freeze_is_independent_and_does_not_designate_d0(tmp_path):
    value = config()
    store = ConfigFreeze(tmp_path)
    manifest = store.freeze_mock(value)
    assert manifest["live_ready"] is False
    assert not (tmp_path / "d0_freeze_manifest.json").exists()
    assert not (tmp_path / "final_freeze_manifest.json").exists()
    assert store.freeze_mock(value) == manifest
    value["mode"] = "live"
    with pytest.raises(ContractError):
        store.freeze_mock(value)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(full_pom_go_gates=True),
    lambda value: value["task_ids"].append("fourth-task"),
    lambda value: value.update(d0_task_id=value["task_ids"][0]),
    lambda value: value["models"]["astra"].update(transport="adaption"),
    lambda value: value["models"]["swarm"].update(model="other-model"),
    lambda value: value["models"]["swarm"]["roles"].append("worker"),
    lambda value: value["models"]["worker"].update(model="qwen3.5-397b"),
    lambda value: value["models"]["worker"].update(transport="adaption"),
    lambda value: value["models"]["worker"].update(mode="api"),
    lambda value: value["models"]["worker"].update(roles=["worker", "author"]),
    lambda value: value["models"]["worker"].update(parameters={"reasoning_effort": "high"}),
    lambda value: value["models"]["worker"].update(parameters={"reasoning_effort": "medium", "temperature": 0}),
    lambda value: value["models"].pop("worker"),
    lambda value: value["prompts"]["worker"].update(text="Modified after snapshot"),
    lambda value: value["budgets"].update(api_key="never-store-credentials"),
])
def test_invalid_or_drifting_config_is_rejected(mutation):
    value = config()
    mutation(value)
    with pytest.raises(ContractError):
        validate_config(value)


def test_explicit_unlimited_client_timeout_and_expanded_worker_budget():
    value = config()
    assert value['budgets']['timeout_seconds'] is None
    assert value['budgets']['worker_output_tokens'] == 8192
    assert value['budgets']['worker_total_tokens'] == 32768
    assert value['budgets']['author_input_bytes'] == 32768
    validate_config(value)
    value['budgets']['timeout_seconds'] = 120
    validate_config(value)
    value['budgets']['timeout_seconds'] = 0
    with pytest.raises(ContractError):
        validate_config(value)


def test_only_supported_swarm_parameters_are_accepted():
    value = config()
    assert value["models"]["swarm"]["parameters"] == {}
    value["models"]["swarm"]["parameters"] = {"temperature": 0}
    validate_config(value)
    value["models"]["swarm"]["parameters"]["top_p"] = 0.9
    with pytest.raises(ContractError):
        validate_config(value)


def terra_config():
    value = config()
    value['models']['worker']['model'] = 'openai-codex/gpt-6.1-terra'
    value['models']['swarm'].update(
        model='openai-codex/gpt-6.1-terra', transport='omp_subscription', mode='subscription',
        parameters={'reasoning_effort': 'medium'})
    value['models']['astra'].update(model='openai-codex/gpt-6-astra', mode='subscription')
    value['models']['terra'].update(model='openai-codex/gpt-6.1-sol', mode='subscription')
    return value


def test_explicit_terra_sol_configuration_preserves_provider_identity():
    value = terra_config()
    before = deepcopy(value)
    assert validate_config(value) == before
    assert value['models']['astra']['model'] == 'openai-codex/gpt-6-astra'
    assert config()['models']['astra']['model'] == 'gpt6-astra'


@pytest.mark.parametrize('change', [
    {'transport': 'adaption'}, {'mode': 'provider_default'}, {'parameters': {}},
    {'parameters': {'reasoning_effort': 'high'}},
    {'parameters': {'reasoning_effort': 'medium', 'temperature': 0}},
    {'roles': ['author', 'worker']},
])
def test_terra_swarm_requires_subscription_medium_and_assigned_roles(change):
    value = terra_config()
    value['models']['swarm'].update(change)
    with pytest.raises(ContractError):
        validate_config(value)


def sci_config():
    from little_pom.tasks import SCI_TASK_IDS

    value = terra_config()
    value.update(pilot_kind='sci_agent_exam_exploratory', task_ids=list(SCI_TASK_IDS),
                 defect_assignments={task: 'unmet_precondition' for task in SCI_TASK_IDS})
    return value


def test_sci_exploratory_freeze_preserves_distinct_corpus(tmp_path):
    value = sci_config()
    frozen = ConfigFreeze(tmp_path).freeze_exploratory(value)
    assert frozen['task_order'] == value['task_ids']
    assert frozen['independent_confirmation'] is False
    assert frozen['d0_completed'] is False
    assert ConfigFreeze(tmp_path).verify('exploratory', value)


@pytest.mark.parametrize('change', [
    lambda value: value['task_ids'].__setitem__(0, PILOT_TASK_IDS[0]),
    lambda value: value['defect_assignments'].pop(value['task_ids'][0]),
    lambda value: value['defect_assignments'].update({PILOT_TASK_IDS[0]: 'unmet_precondition'}),
    lambda value: value.update(d0_task_id='invented-d0'),
    lambda value: value.update(pilot_kind='three_task_feasibility'),
])
def test_sci_rejects_mixed_corpus_and_false_d0(change):
    value = sci_config()
    change(value)
    with pytest.raises(ContractError):
        validate_config(value)


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, None])
def test_author_input_budget_requires_explicit_positive_bytes(budget):
    value = config()
    value["budgets"]["author_input_bytes"] = budget
    with pytest.raises(ContractError):
        validate_config(value)


def test_missing_author_input_budget_is_not_silently_migrated():
    value = config()
    del value["budgets"]["author_input_bytes"]
    with pytest.raises(ContractError):
        validate_config(value)
