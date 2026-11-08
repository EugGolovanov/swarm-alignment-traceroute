import copy

import pytest

from little_pom.profiles import ProfileBuilder, RequirementsBuilder


VOCABULARY = {"measurement": ["measurement"], "budget": ["budget"], "role": ["assigned specialist"]}


def output():
    return {"schema_version": "1.0", "id": "output", "task_id": "task", "worker_id": "worker",
            "subtask_type": "assigned specialist", "subtask_text": "assigned specialist budget",
            "turns_used": 2, "result": "A MEASUREMENT establishes the fact.", "evidence_ids": ["evidence-one"],
            "limitations": ["The budget is unknown."], "complete": True}


def valid():
    return {"schema_version": "1.0", "id": "card", "task_id": "task", "proposal_id": "proposal", "variant": "valid",
            "proposed_action": "Repeat the measurement", "uncertainty": "Uncertain", "grounds": [],
            "expected_decision_impact": "Decide", "scale_budget": "Small budget", "preconditions": [], "evidence_ids": []}


def test_tags_have_factual_sources_not_assigned_role():
    profile = ProfileBuilder().build(output(), VOCABULARY)
    assert profile["tags"] == ["budget", "measurement"]
    assert profile["tag_sources"] == {"budget": ["limitations"], "measurement": ["result"]}
    changed = output()
    changed["subtask_type"] = "Another role"
    changed["subtask_text"] = "No role"
    assert ProfileBuilder().build(changed, VOCABULARY) == profile


def test_target_and_informed_labels_cannot_change_tags():
    worker = output()
    expected = ProfileBuilder().build(worker, VOCABULARY)
    worker.update(target_defect_type="assigned specialist", informed_workers=["worker"], outcome="assigned specialist")
    assert ProfileBuilder().build(worker, VOCABULARY) == expected


def test_matching_is_literal_bounded_and_deterministic():
    worker = output()
    worker.update(result="measurements are not a whole-word match", limitations=[])
    assert ProfileBuilder().build(worker, VOCABULARY)["tags"] == []
    worker["evidence_ids"] = ["measurement"]
    assert ProfileBuilder().build(worker, VOCABULARY)["tag_sources"] == {"measurement": ["evidence_ids"]}
    with pytest.raises(ValueError):
        ProfileBuilder().build(worker, {"measurement": []})


def test_requirements_only_from_valid_and_empty_is_diagnostic():
    builder = RequirementsBuilder()
    result = builder.build(valid(), VOCABULARY)
    assert result["tags"] == ["budget", "measurement"]
    assert result["frozen"] is True
    assert result["source"] == "valid"
    broken = valid()
    broken["variant"] = "def"
    with pytest.raises(ValueError, match="before defect"):
        builder.build(broken, VOCABULARY)
    empty = builder.build(valid(), {"unmatched": ["zebra"]})
    assert empty["diagnostics"] == ["routing_representation_failure"]
