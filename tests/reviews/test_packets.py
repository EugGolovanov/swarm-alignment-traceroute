import copy

import pytest

from little_pom.contracts import canonical_json, evidence_id, sha256_bytes
from little_pom.reviews import BlindingError, ReviewerPacketBuilder, assert_blind
from little_pom.workers import freeze_state


def frozen_worker():
    fragment = "The 3-unit sample was below the 12-unit detection limit."
    source_hash = sha256_bytes(b"private calibration table")
    fragment_hash = sha256_bytes(fragment.encode())
    citation = evidence_id(source_hash, fragment_hash)
    evidence = {
        "schema_version": "1.0", "id": "evidence-one", "task_id": "task-one",
        "evidence_id": citation, "source": "calibration.txt", "source_sha256": source_hash,
        "fragment": fragment, "fragment_sha256": fragment_hash, "kind": "generated",
    }
    return freeze_state({
        "schema_version": "1.0", "id": "snapshot-one", "task_id": "task-one",
        "worker_id": "worker-one", "subtask_type": "calibration",
        "history": [{"role": "assistant", "content": "The assay needs a 12-unit concentration."}],
        "output": {
            "schema_version": "1.0", "id": "output-one", "task_id": "task-one",
            "worker_id": "worker-one", "subtask_type": "calibration",
            "subtask_text": "Measure assay sensitivity.", "turns_used": 1,
            "result": "The detection limit is 12 units.", "evidence_ids": [citation],
            "limitations": ["No data below the detection threshold."], "complete": True,
        },
        "evidence_ids": [citation], "evidence": [evidence], "artifact_provenance": [],
        "token_budget": {"requested": 4096},
    })


def test_golden_fresh_packet_has_only_scientific_fields():
    result = ReviewerPacketBuilder(packet_id="opaque-packet").build(
        {"text": "Estimate assay sensitivity."}, {"text": "Calibration uses known standards."},
        {"text": "Measure a 3-unit sample."},
    )
    assert result == {
        "schema_version": "1.0", "id": "opaque-packet",
        "task": {"text": "Estimate assay sensitivity."},
        "dossier": {"text": "Calibration uses known standards."},
        "card": {"text": "Measure a 3-unit sample."},
    }
    assert "worker_context" not in result


@pytest.mark.parametrize("field", ["condition", "variant", "target_defect", "informed", "routing_scores"])
def test_unknown_internal_fields_are_rejected_not_sanitized(field):
    with pytest.raises(BlindingError):
        ReviewerPacketBuilder().build(
            {"text": "Scientific task", field: "hidden"},
            {"text": "Scientific dossier"}, {"text": "Scientific card"},
        )


@pytest.mark.parametrize("text", [
    "condition: Fresh", "This is the RoutedHistory assessment.",
    "variant = def", "target_defect: ignored_result", "target evidence = e001",
    "routing_score: 0.8", "informed_workers = w1,w2", "panel_membership: w1,w2",
    "reviewer_identity: w1", "valid variant", "variant is def",
    '{"variant": "def"}', "The target defect is ignored_result.",
    "The other reviewer found a concern.", "The informed workers are w1 and w2.",
])
def test_text_annotations_fail_closed(text):
    with pytest.raises(BlindingError):
        ReviewerPacketBuilder().build(
            {"text": "Scientific task"}, {"text": text}, {"text": "Scientific card"},
        )


def test_scientific_words_and_private_evidence_are_preserved():
    text = "The valid concentration range depends on the assay condition and defect density."
    packet = ReviewerPacketBuilder().build({"text": text}, {"text": text}, {"text": text})
    assert packet["task"]["text"] == text
    assert_blind({"evidence": [{"evidence_id": "private-measurement", "text": text}]})


def test_explicit_hidden_labels_are_checked_without_removal():
    with pytest.raises(BlindingError):
        ReviewerPacketBuilder(forbidden_values=("secret-variant-token",)).build(
            {"text": "Task"}, {"text": "secret-variant-token was inserted"}, {"text": "Card"},
        )


def test_forks_leave_original_snapshot_bitwise_unchanged_and_do_not_share_history():
    state = frozen_worker()
    before = canonical_json(state)
    arguments = ({"text": "Task"}, {"text": "Dossier"}, {"text": "Card"})
    first = ReviewerPacketBuilder(packet_id="first").build(*arguments, state)
    second = ReviewerPacketBuilder(packet_id="second").build(*arguments, state)
    assert canonical_json(state) == before
    first["worker_context"]["history"].append({"role": "assistant", "content": "Prior review"})
    assert second["worker_context"]["history"] == state["history"]
    assert canonical_json(state) == before
    assert "worker_id" not in canonical_json(second).decode()
    assert "snapshot-one" not in canonical_json(second).decode()
    assert second["worker_context"]["evidence"] == [
        {"evidence_id": state["evidence"][0]["evidence_id"], "text": state["evidence"][0]["fragment"]}
    ]


def test_tampered_snapshot_is_rejected():
    state = frozen_worker()
    state["history"][0]["content"] = "Changed after freeze"
    with pytest.raises(ValueError, match="frozen snapshot"):
        ReviewerPacketBuilder().build({"text": "Task"}, {"text": "Dossier"}, {"text": "Card"}, state)


def test_structural_leak_in_nested_history_is_rejected():
    with pytest.raises(BlindingError):
        assert_blind({"worker_context": {"history": [{"content": {"target_defect": "x"}}]}})


def test_input_text_dicts_are_not_shared():
    task = {"text": "Scientific task"}
    original = copy.deepcopy(task)
    packet = ReviewerPacketBuilder().build(task, {"text": "Dossier"}, {"text": "Card"})
    packet["task"]["text"] = "Changed"
    assert task == original


def test_empty_common_dossier_is_preserved_without_invented_content():
    packet = ReviewerPacketBuilder().build(
        {"text": "Scientific task"}, {"text": ""}, {"text": "Scientific card"},
    )
    assert packet["dossier"] == {"text": ""}


@pytest.mark.parametrize("field", ["task", "card"])
def test_empty_task_or_card_remains_invalid(field):
    inputs = {
        "task": {"text": "Scientific task"},
        "dossier": {"text": ""},
        "card": {"text": "Scientific card"},
    }
    inputs[field] = {"text": ""}
    with pytest.raises(BlindingError, match="nonempty"):
        ReviewerPacketBuilder().build(**inputs)


def test_nontext_dossier_remains_invalid():
    with pytest.raises(BlindingError, match="must be text"):
        ReviewerPacketBuilder().build(
            {"text": "Scientific task"}, {"text": None}, {"text": "Scientific card"},
        )
