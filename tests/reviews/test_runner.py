import copy
import threading

import pytest

from little_pom.contracts import canonical_json
from little_pom.reviews import BlindingError, REVIEW_PROMPT, ReviewRunner, RunList
from little_pom.reviews.fixtures import review_fixture
from little_pom.runtime import ArtifactStore, Ledger, MockBackend, SwarmClient


def one_panel():
    fixture = review_fixture(task_count=1)
    panel = fixture["panels"][0]
    return fixture, panel, fixture["packets"][panel["id"]]


def runtime(tmp_path, responses, **options):
    backend = MockBackend(responses=responses, **options)
    return SwarmClient(ledger=Ledger(tmp_path / "runtime"), backend=backend), backend, ArtifactStore(tmp_path / "artifacts")


def test_full_three_task_count_is_derived_and_order_is_seeded():
    fixture = review_fixture(task_count=3)
    panels = fixture["panels"]
    slots = RunList.build(panels, 17)
    assert len(panels) == 36
    assert len(slots) == 72
    assert len(RunList.build(panels[:3], 17)) == 6
    assert RunList.build(list(reversed(panels)), 17) == slots
    assert RunList.build(panels, 18) != slots
    assert len({slot["id"] for slot in slots}) == len(slots)
    assert len({slot["packet_id"] for slot in slots}) == len(slots)
    assert not {slot["id"] for slot in slots} & {slot["packet_id"] for slot in slots}
    assert [slot["position"] for slot in slots] == list(range(len(slots)))


def test_complete_panel_resume_does_not_sample_twice(tmp_path):
    fixture, panel, packets = one_panel()
    client, backend, store = runtime(tmp_path, fixture["responses"])
    runner = ReviewRunner(seed=17)
    first = runner.run(panel, packets, client, store)
    second = runner.run(panel, packets, client, store)
    assert first == second
    assert len(backend.calls) == 2
    assert first["status"] == "ok"
    outcome = first["data"]
    assert outcome["status"] == "complete"
    assert (outcome["n_requested"], outcome["n_observed"]) == (2, 2)
    assert outcome["missingness"] == []
    assert all(review["evidence_ids"] == ["e-public"] for review in outcome["reviews"])
    assert {call["request"]["instructions"] for call in backend.calls} == {REVIEW_PROMPT}
    for call in backend.calls:
        packet = call["request"]["packet"]
        assert call["request"]["history"] == []
        assert call["execution_id"] != packet["id"]
        assert "worker_context" not in packet
        assert "condition" not in packet
        assert "panel_id" not in packet


@pytest.mark.parametrize("failed_count, expected_status, observed", [(1, "partial", 1), (2, "incomplete", 0)])
def test_missing_reviews_have_denominators_not_negative_labels(tmp_path, failed_count, expected_status, observed):
    fixture, panel, packets = one_panel()
    responses = copy.deepcopy(fixture["responses"])
    slots = RunList.build([panel], 17)
    for slot in slots[:failed_count]:
        responses[slot["id"]] = {"invalid": "not a review"}
    client, backend, store = runtime(tmp_path, responses)
    runner = ReviewRunner(seed=17)
    result = runner.run(panel, packets, client, store)
    assert result["status"] == "incomplete"
    outcome = result["data"]
    assert outcome["status"] == expected_status
    assert outcome["n_requested"] == 2
    assert outcome["n_observed"] == observed
    assert len(outcome["reviews"]) == observed
    assert len(outcome["missingness"]) == failed_count
    assert all(item["reason"] for item in outcome["missingness"])
    assert "D" not in outcome and "detection" not in outcome
    assert runner.run(panel, packets, client, store) == result
    assert len(backend.calls) == 2


def test_all_panel_histories_are_independent_and_inputs_remain_bitwise_identical(tmp_path):
    fixture = review_fixture(task_count=1)
    before = canonical_json(fixture["packets"])
    client, backend, store = runtime(tmp_path, fixture["responses"])
    results = ReviewRunner(seed=17).run_all(fixture["panels"], fixture["packets"], client, store)
    assert len(results) == 12
    assert len(backend.calls) == 24
    assert canonical_json(fixture["packets"]) == before
    histories = [call["request"]["packet"]["worker_context"]["history"]
                 for call in backend.calls if "worker_context" in call["request"]["packet"]]
    assert histories and all(history == histories[0] for history in histories)
    assert all(len(history) == 1 for history in histories)
    assert all(call["request"]["instructions"] == REVIEW_PROMPT for call in backend.calls)


def test_dispatch_with_concurrency_one_follows_frozen_global_order(tmp_path):
    fixture = review_fixture(task_count=1)
    client, backend, store = runtime(tmp_path, fixture["responses"])
    ReviewRunner(seed=17, concurrency=1).run_all(fixture["panels"], fixture["packets"], client, store)
    assert [call["execution_id"] for call in backend.calls] == [slot["id"] for slot in fixture["slots"]]


def test_configured_concurrency_allows_independent_calls_to_overlap(tmp_path):
    fixture, panel, packets = one_panel()

    class ConcurrentBackend(MockBackend):
        def __init__(self):
            super().__init__(responses=fixture["responses"])
            self.barrier = threading.Barrier(2)
            self.active = 0
            self.maximum = 0
            self.counter_lock = threading.Lock()

        def send(self, request, schema_id, execution_id):
            with self.counter_lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            try:
                self.barrier.wait(timeout=5)
                return super().send(request, schema_id, execution_id)
            finally:
                with self.counter_lock:
                    self.active -= 1

    backend = ConcurrentBackend()
    client = SwarmClient(ledger=Ledger(tmp_path / "runtime"), backend=backend)
    result = ReviewRunner(seed=17, concurrency=2).run(panel, packets, client, ArtifactStore(tmp_path / "artifacts"))
    assert result["data"]["status"] == "complete"
    assert backend.maximum == 2


def test_changed_frozen_configuration_cannot_replace_manifest(tmp_path):
    fixture, panel, packets = one_panel()
    client, backend, store = runtime(tmp_path, fixture["responses"])
    ReviewRunner(seed=17, concurrency=1).run(panel, packets, client, store)
    with pytest.raises(ValueError, match="Immutable review artifact conflict"):
        ReviewRunner(seed=17, concurrency=2).run(panel, packets, client, store)
    assert len(backend.calls) == 2


@pytest.mark.parametrize("mutation", ["fresh_history", "missing_packet", "duplicate_id", "metadata"])
def test_invalid_plan_fails_before_any_model_dispatch(tmp_path, mutation):
    fixture, panel, packets = one_panel()
    keys = list(packets)
    if mutation == "fresh_history":
        packets[keys[0]]["worker_context"] = {"summary": "Work", "history": [], "evidence": []}
    elif mutation == "missing_packet":
        del packets[keys[0]]
    elif mutation == "duplicate_id":
        packets[keys[1]]["id"] = packets[keys[0]]["id"]
    else:
        packets[keys[0]]["task"]["text"] = "condition: Fresh"
    client, backend, store = runtime(tmp_path, fixture["responses"])
    with pytest.raises((ValueError, BlindingError)):
        ReviewRunner(seed=17).run(panel, packets, client, store)
    assert backend.calls == []


def test_ambiguous_runtime_timeout_is_missing_not_retried(tmp_path):
    fixture, panel, packets = one_panel()
    client, backend, store = runtime(tmp_path, fixture["responses"], scenario="timeout")
    runner = ReviewRunner(seed=17)
    first = runner.run(panel, packets, client, store)
    assert first["data"]["status"] == "incomplete"
    assert first["data"]["n_observed"] == 0
    assert len(backend.calls) == 2
    assert runner.run(panel, packets, client, store) == first
    assert len(backend.calls) == 2


def test_runlist_rejects_duplicate_panels_and_reviewers():
    fixture, panel, _ = one_panel()
    with pytest.raises(ValueError, match="Duplicate panel ID"):
        RunList.build([panel, panel], 17)
    panel["reviewer_specs"][1]["reviewer_key"] = panel["reviewer_specs"][0]["reviewer_key"]
    with pytest.raises(ValueError):
        RunList.build([panel], 17)


def test_whole_run_can_resume_one_panel_without_resampling(tmp_path):
    fixture = review_fixture(task_count=1)
    client, backend, store = runtime(tmp_path, fixture["responses"])
    runner = ReviewRunner(seed=17)
    outcomes = runner.run_all(fixture["panels"], fixture["packets"], client, store)
    for panel, expected in zip(fixture["panels"], outcomes, strict=True):
        assert runner.run(panel, fixture["packets"][panel["id"]], client, store) == expected
    assert len(backend.calls) == 24


def test_changed_packet_payload_cannot_reuse_frozen_dispatch(tmp_path):
    fixture, panel, packets = one_panel()
    client, backend, store = runtime(tmp_path, fixture["responses"])
    runner = ReviewRunner(seed=17)
    runner.run(panel, packets, client, store)
    next(iter(packets.values()))["card"]["text"] = "A different scientific action."
    with pytest.raises(ValueError, match="Immutable review artifact conflict"):
        runner.run(panel, packets, client, store)
    assert len(backend.calls) == 2


def test_crash_after_intent_resumes_as_missing_without_second_sample(tmp_path):
    from little_pom.runtime.backends import SimulatedCrash

    fixture, panel, packets = one_panel()
    client, backend, store = runtime(tmp_path, fixture["responses"], scenario="crash")
    runner = ReviewRunner(seed=17)
    with pytest.raises(SimulatedCrash):
        runner.run(panel, packets, client, store)
    assert len(backend.calls) == 2
    resumed = runner.run(panel, packets, client, store)
    assert resumed["data"]["status"] == "incomplete"
    assert resumed["data"]["n_observed"] == 0
    assert len(backend.calls) == 2
