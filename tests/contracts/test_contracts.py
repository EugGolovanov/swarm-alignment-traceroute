"""Offline behavioral specifications for every shared boundary."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from little_pom.contracts import (
    ContractError, canonical_json, derive_seed, domain_hash, evidence_id,
    get_schema, request_hash, seeded_order, sha256_bytes, sha256_hash, validate,
)

FIXTURES = Path(__file__).parent / "fixtures"
BOUNDARIES = sorted(path for path in FIXTURES.glob("*.json") if path.name != "golden.json")


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["positive"][0]


@pytest.mark.parametrize("path", BOUNDARIES, ids=lambda path: path.stem)
def test_positive_and_negative_boundary_fixtures(path):
    data = json.loads(path.read_text())
    for positive in data["positive"]:
        before = deepcopy(positive)
        assert validate(data["schema"], positive) is positive
        assert positive == before
    for negative in data["negative"]:
        with pytest.raises(ContractError):
            validate(data["schema"], negative)


@pytest.mark.parametrize("name", [path.stem for path in BOUNDARIES])
def test_exported_schema_matches_runtime_schema(name):
    path = Path(__file__).parents[2] / "src/little_pom/contracts/json" / f"{name}.schema.json"
    assert json.loads(path.read_text()) == get_schema(name)


def test_schema_lookup_does_not_expose_mutable_global():
    schema = get_schema("Review")
    schema["properties"]["status"]["enum"].append("fabricated")
    assert "fabricated" not in get_schema("Review")["properties"]["status"]["enum"]


@pytest.mark.parametrize("path", [(), ("task",), ("card",), ("worker_context",)])
def test_blind_packet_rejects_metadata_at_every_nested_boundary(path):
    packet = fixture("ReviewerPacket")
    target = packet
    for key in path:
        target = target[key]
    target["target_defect"] = "unmet_precondition"
    with pytest.raises(ContractError):
        validate("ReviewerPacket", packet)


def test_blind_evidence_nested_metadata_is_rejected():
    packet = fixture("TerraPacket")
    packet["cited_evidence"][0]["worker_id"] = "w1"
    with pytest.raises(ContractError):
        validate("TerraPacket", packet)


@pytest.mark.parametrize("text", ["condition=Fresh", "variant:def", "routing_score:0.9", "RoutedHistory"])
def test_textual_metadata_is_rejected_not_silently_sanitized(text):
    packet = fixture("TerraPacket")
    packet["claim"] = text
    with pytest.raises(ContractError):
        validate("TerraPacket", packet)
    assert packet["claim"] == text


def test_scientific_uses_of_valid_and_defect_are_preserved():
    packet = fixture("TerraPacket")
    packet["claim"] = "The valid calibration reveals a crystal defect in the fresh sample."
    validate("TerraPacket", packet)


@pytest.mark.parametrize("packet_id", ["task-valid-1", "Fresh-123", "x-def-y", "RoutedHistory"])
def test_packet_id_must_not_encode_condition_or_variant(packet_id):
    packet = fixture("ReviewerPacket")
    packet["id"] = packet_id
    with pytest.raises(ContractError):
        validate("Packet", packet)


def test_hex_packet_identity_is_not_mistaken_for_variant_metadata():
    packet = fixture("ReviewerPacket")
    packet["id"] = "543def431edcfb4452d9ca2bc5ec6f92"
    assert validate("Packet", packet) is packet


def test_internal_metadata_requires_execution_id_and_request_hash():
    value = fixture("InternalMetadata")
    for key in ("execution_id", "request_hash"):
        missing = dict(value)
        del missing[key]
        with pytest.raises(ContractError):
            validate("InternalMetadata", missing)


def test_evidence_identity_and_fragment_content_are_verified():
    value = fixture("Evidence")
    for field in ("evidence_id", "fragment_sha256"):
        forged = {**value, field: "0" * 64}
        with pytest.raises(ContractError):
            validate("Evidence", forged)
    forged = {**value, "fragment": "Unsupported claim"}
    with pytest.raises(ContractError):
        validate("Evidence", forged)


def test_panel_status_and_counts_must_agree():
    value = fixture("PanelOutcome")
    for changed in ({"status": "complete"}, {"n_observed": 0}, {"missingness": []}):
        with pytest.raises(ContractError):
            validate("PanelOutcome", {**value, **changed})


def test_fresh_panel_cannot_reference_a_worker():
    value = fixture("Panel")
    value["reviewer_specs"][0]["worker_id"] = "w1"
    with pytest.raises(ContractError):
        validate("Panel", value)


def test_partial_service_preserves_observed_panel():
    value = {"status": "incomplete", "data": fixture("PanelOutcome"), "error": {"code": "one_missing"}}
    validate("ServiceResult", value)
    assert value["data"]["n_observed"] == 1


def test_jcs_hash_and_seed_golden_vectors():
    golden = json.loads((FIXTURES / "golden.json").read_text())
    for vector in golden["jcs"]:
        assert canonical_json(vector["value"]) == vector["canonical"].encode("utf-8")
        assert sha256_hash(vector["value"]) == vector["sha256"]
    vector = golden["seed"]
    args = vector["seed"], vector["domain"], *vector["parts"]
    assert domain_hash(*args) == vector["domain_hash"]
    assert derive_seed(*args) == vector["derived_seed"]
    evidence = golden["evidence"]
    assert sha256_bytes(evidence["source"].encode()) == evidence["source_sha256"]
    assert evidence_id(evidence["source_sha256"], evidence["fragment_sha256"]) == evidence["evidence_id"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**64, {1: "bad"}, ("not", "json")])
def test_non_jcs_values_are_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        canonical_json(value)


def test_seed_domains_are_separate_and_order_independent():
    assert derive_seed(42, "review_order", "x") != derive_seed(42, "random_panel", "x")
    assert derive_seed(42, "x", "ab", "c") != derive_seed(42, "x", "a", "bc")
    assert seeded_order(["b", "a", "c"], 42, "task_order") == seeded_order(["c", "b", "a"], 42, "task_order")
    assert 0 <= derive_seed(42, "x") < 2**32
    with pytest.raises(ValueError):
        seeded_order(["a", "a"], 42, "task_order")


def test_request_hash_covers_configuration_history_schema_and_transport():
    request = {"model": "qwen3.8-27b", "config": {"temperature": 0}, "instructions": "Work.",
               "packet": {"text": "Task."}, "history": [], "output_budget": 100}
    transport = {"backend": "mock", "max_retries": 0}
    baseline = request_hash(request, "WorkerTurn", transport)
    for key, value in (("config", {"temperature": 1}), ("instructions", "Other."),
                       ("history", [{"role": "user", "content": "Other"}]), ("output_budget", 101)):
        assert request_hash({**request, key: value}, "WorkerTurn", transport) != baseline
    assert request_hash(request, "ProposalCard", transport) != baseline
    assert request_hash(request, "WorkerTurn", {**transport, "backend": "adaption"}) != baseline
    with pytest.raises(ValueError):
        request_hash({**request, "config": {"api_key": "not-read-from-disk"}}, "WorkerTurn", transport)


def test_secrets_are_rejected_recursively():
    value = fixture("ServiceResult")
    value["data"]["nested"] = {"authorization": "secret-value"}
    with pytest.raises(ContractError):
        validate("ServiceResult", value)
