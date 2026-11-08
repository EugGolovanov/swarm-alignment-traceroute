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
    assert value["models"]["swarm"]["model"] == "qwen3.8-27b"
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
    lambda value: value["prompts"]["worker"].update(text="Modified after snapshot"),
    lambda value: value["budgets"].update(api_key="never-store-credentials"),
])
def test_invalid_or_drifting_config_is_rejected(mutation):
    value = config()
    mutation(value)
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
