import copy

import pytest

from little_pom.contracts import derive_seed, sha256_hash
from little_pom.routing import PanelBuilder, select_routed


def profile(worker, tags):
    return {"schema_version": "1.0", "id": sha256_hash(worker), "task_id": "task", "worker_id": worker,
            "tags": tags, "tag_sources": {tag: ["result"] for tag in tags}}


def requirements(tags):
    return {"schema_version": "1.0", "id": "requirements", "task_id": "task", "tags": tags,
            "source": "valid", "card_id": "valid-card", "frozen": True,
            "diagnostics": [] if tags else ["routing_representation_failure"]}


def test_max_overlap_then_complementary_golden():
    profiles = [profile("first", ["a", "b", "c"]), profile("redundant", ["a", "b"]),
                profile("complement", ["d"]), profile("empty", [])]
    assert select_routed(profiles, requirements(["a", "b", "c", "d"]), 42) == (["first", "complement"], False)


def test_total_overlap_fallback_does_not_flag_positive_total():
    profiles = [profile("first", ["a", "b"]), profile("second", ["a"]), profile("empty", [])]
    assert select_routed(profiles, requirements(["a", "b"]), 42) == (["first", "second"], False)


def test_zero_overlap_frozen_tie_break():
    profiles = [profile(w, []) for w in ("a", "b", "c")]
    first = min(("a", "b", "c"), key=lambda w: (derive_seed(42, "routing_tie", "task", 1, w), w))
    second = min(set(("a", "b", "c")) - {first}, key=lambda w: (derive_seed(42, "routing_tie", "task", 2, w), w))
    assert select_routed(profiles, requirements([]), 42) == ([first, second], True)
    assert select_routed(list(reversed(profiles)), requirements([]), 42) == ([first, second], True)


def test_pairs_are_variant_paired_and_fresh_has_no_state(tmp_path):
    pool = [f"w{i}" for i in range(8)]
    profiles = [profile(w, ["a"] if i < 4 else ["b"]) for i, w in enumerate(pool)]
    panels = PanelBuilder(tmp_path).build(pool, profiles, requirements(["a", "b"]), 123)
    assert len(panels) == 12
    routed_pairs = set()
    for panel in panels:
        pair = tuple(s["worker_id"] for s in panel["reviewer_specs"])
        if panel["condition"] == "Fresh":
            assert pair == (None, None)
        else:
            assert len(set(pair)) == 2
        if panel["condition"] == "RoutedHistory":
            routed_pairs.add(pair)
        if panel["condition"] == "RandomHistory":
            expected = sorted(pool, key=lambda w: (derive_seed(123, "random_panel", "task", panel["repeat"], w), w))[:2]
            assert list(pair) == expected
    assert len(routed_pairs) == 1
    for repeat in (1, 2):
        matched = [p for p in panels if p["condition"] == "RandomHistory" and p["repeat"] == repeat]
        assert [s["worker_id"] for s in matched[0]["reviewer_specs"]] == [s["worker_id"] for s in matched[1]["reviewer_specs"]]


def test_panel_freeze_survives_restart_and_return_mutation(tmp_path):
    pool = ["a", "b", "c"]
    profiles = [profile(w, [w]) for w in pool]
    builder = PanelBuilder(tmp_path)
    original = builder.build(pool, profiles, requirements(["a"]), 42)
    original[0]["reviewer_specs"][0]["worker_id"] = "mutated"
    restored = PanelBuilder(tmp_path).build(pool, profiles, requirements(["a"]), 42)
    assert restored[0]["reviewer_specs"][0]["worker_id"] is None
    with pytest.raises(ValueError, match="freeze|frozen"):
        PanelBuilder(tmp_path).build(pool, profiles, requirements(["b"]), 42)


def test_router_rejects_target_leakage_and_filters_incomplete_pool():
    profiles = [profile("a", ["x"]), profile("b", ["y"])]
    leaked = copy.deepcopy(profiles)
    leaked[0]["target_defect_type"] = "hidden"
    with pytest.raises(ValueError):
        select_routed(leaked, requirements(["x"]), 1)
    pool = [{"worker_id": "a", "complete": True}, {"worker_id": "b", "complete": False}]
    with pytest.raises(ValueError, match="two distinct"):
        PanelBuilder().build(pool, profiles, requirements(["x"]), 1)


def test_empty_requirements_keep_diagnostic_panels():
    panels = PanelBuilder().build(["a", "b"], [profile("a", []), profile("b", [])], requirements([]), 1)
    assert len(panels) == 12
    assert all(p["router_fallback"] for p in panels if p["condition"] == "RoutedHistory")
