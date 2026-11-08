"""Behavioral tests use only explicitly selected subscription fixtures."""

import copy
import json

import pytest

from little_pom.measurement import (
    AdjudicationQueue,
    EvaluationExporter,
    EvaluationImporter,
    MockSubscriptionBackend,
    PROMPT_HASHES,
    final_scoring_projection,
    measurement_policy,
)


def fixture(count=72):
    reviews, contexts = [], {}
    conditions = ("Fresh", "RandomHistory", "RoutedHistory")
    for index in range(count):
        rid = f"output-{index:03}"
        reviews.append({"schema_version": "1.0", "id": rid, "status": "claim",
                        "claim": "The reported assay exceeds the demonstrated range (cite-01).",
                        "evidence_ids": ["cite-01"],
                        "decision_relevance_text": "Repeat the assay at a supported concentration."})
        contexts[rid] = {"task_id": f"episode-{index % 3}", "task": "Choose an assay concentration.",
                         "card": "Use the proposed assay concentration.", "condition": conditions[index % 3],
                         "variant": "valid" if (index // 3) % 2 == 0 else "def",
                         "panel_id": f"panel-{index // 2}", "reviewer_id": f"reviewer-{index}",
                         "target_defect": "disproportionate_scale"}
    evidence = {"reviews": contexts,
                "items": {"cite-01": {"text": "The demonstrated assay range is 1 to 10 units."},
                          "uncited-secret": {"text": "This complete target evidence list must not appear."}}}
    policy = measurement_policy("terra-subscription", "astra-subscription", "standard", "2026-11-08")
    return reviews, evidence, policy


def export(root, count=72):
    reviews, evidence, policy = fixture(count)
    exporter = EvaluationExporter(root)
    return exporter.export(reviews, evidence, 17, policy), exporter


def request(root, pid):
    return json.loads((root / "subscription" / f"{pid}.json").read_text())


def answer(root, pid, **changes):
    response = MockSubscriptionBackend().respond(request(root, pid))
    label = json.loads(response["raw_response"])
    label.update(changes)
    response["raw_response"] = json.dumps(label)
    return EvaluationImporter(root).import_response(pid, **response)


def import_all(root, manifest, overrides=None):
    overrides = overrides or {}
    for lane, ids in manifest["packet_ids"].items():
        for pid in ids:
            assert answer(root, pid, **overrides.get(pid, {}))["status"] == "ok"


def test_sampling_is_frozen_balanced_and_order_independent(tmp_path):
    reviews, evidence, policy = fixture()
    first = EvaluationExporter(tmp_path / "a").export(reviews, evidence, 17, policy)
    second = EvaluationExporter(tmp_path / "b").export(list(reversed(reviews)), evidence, 17, policy)
    assert first == second
    assert first["denominators"]["successful_outputs"] == 72
    assert len(first["calibration_review_ids"]) == 36
    assert len(first["retest_review_ids"]) == 15  # ceil(14.4), not bankers' rounding
    for counts in first["denominators"]["calibration_conditions"].values():
        assert counts["selected"] == 12
        assert counts["by_variant"] == {"valid": 6, "def": 6}
    assert first["prompt_hashes"] == PROMPT_HASHES
    assert first["agreement_kind"] == "cross_model_not_human_validity"
    assert EvaluationExporter(tmp_path / "a").export(reviews, evidence, 17, policy) == first
    reviews[0]["claim"] = "Changed after freeze."
    with pytest.raises(ValueError, match="already frozen"):
        EvaluationExporter(tmp_path / "a").export(reviews, evidence, 17, policy)


@pytest.mark.parametrize("count,expected_retest", [(0, 0), (1, 1), (5, 1), (6, 2), (11, 3)])
def test_shortage_and_rounding_are_explicit(tmp_path, count, expected_retest):
    manifest, _ = export(tmp_path, count)
    denominators = manifest["denominators"]
    assert denominators["retest_selected"] == expected_retest
    assert denominators["calibration_selected"] == count
    assert sum(item["shortage"] for item in denominators["calibration_conditions"].values()) == 36 - count


def test_variant_shortage_fills_only_within_condition(tmp_path):
    reviews, evidence, policy = fixture(45)
    for context in evidence["reviews"].values():
        context["variant"] = "valid"
    manifest = EvaluationExporter(tmp_path).export(reviews, evidence, 17, policy)
    for counts in manifest["denominators"]["calibration_conditions"].values():
        assert counts["by_variant"] == {"valid": 12, "def": 0}
    assert manifest["denominators"]["calibration_selected"] == 36


def test_packets_are_structurally_and_textually_blind(tmp_path):
    manifest, exporter = export(tmp_path, 1)
    mapping = exporter.mapping()
    contexts = set()
    citation_ids = set()
    for lane in ("primary", "retest", "calibration_a", "calibration_b"):
        pid = manifest["packet_ids"][lane][0]
        req = request(tmp_path, pid)
        packet = req["packet"]
        assert set(packet) == {"schema_version", "id", "task", "card", "claim", "cited_evidence", "explanation"}
        assert set(req) == {"packet", "prompt", "model", "mode", "date", "context_id", "prompt_sha256"}
        serialized = json.dumps(packet)
        for forbidden in ("condition", "Fresh", "valid", "reviewer-0", "episode-0", "output-000",
                          "uncited-secret", "target evidence list", "cite-01", "disproportionate_scale"):
            assert forbidden not in serialized
        assert len(packet["cited_evidence"]) == 1
        assert packet["cited_evidence"][0]["id"] in packet["claim"]
        contexts.add(req["context_id"])
        citation_ids.add(packet["cited_evidence"][0]["id"])
        assert "history" not in req
        assert mapping[pid]["review_id"] == "output-000"
    assert len(contexts) == len(citation_ids) == 4


@pytest.mark.parametrize("leak", ["condition: Fresh", "variant=def", "reviewer-0", "target_defect: ignored_result",
                                  "routing_metadata: score", "validator_history: prior response"])
def test_text_metadata_leaks_are_rejected_before_any_export(tmp_path, leak):
    reviews, evidence, policy = fixture(1)
    reviews[0]["claim"] += " " + leak
    with pytest.raises(ValueError):
        EvaluationExporter(tmp_path).export(reviews, evidence, 17, policy)
    assert not (tmp_path / "blind").exists()


def test_missing_and_duplicate_citations_are_not_silently_removed(tmp_path):
    reviews, evidence, policy = fixture(1)
    reviews[0]["evidence_ids"] = ["missing"]
    with pytest.raises(ValueError, match="missing evidence"):
        EvaluationExporter(tmp_path).export(reviews, evidence, 17, policy)
    reviews[0]["evidence_ids"] = ["cite-01", "cite-01"]
    with pytest.raises(ValueError):
        EvaluationExporter(tmp_path).export(reviews, evidence, 17, policy)


def test_raw_response_and_full_provenance_are_preserved(tmp_path):
    manifest, _ = export(tmp_path, 1)
    pid = manifest["packet_ids"]["primary"][0]
    req = request(tmp_path, pid)
    response = MockSubscriptionBackend().respond(req)
    response["metadata"]["operator_note"] = "Manual subscription export."
    response["raw_response"] = "\n " + response["raw_response"] + "\n"
    result = EvaluationImporter(tmp_path).import_response(pid, **response)
    assert result["status"] == "ok"
    provenance = result["data"]["provenance"]
    assert provenance["raw_response"] == response["raw_response"]
    assert provenance["prompt"] == req["prompt"]
    assert provenance["metadata"] == response["metadata"]
    assert provenance["packet_sha256"]
    assert result["data"]["review_id"] == "output-000"


def test_duplicate_mismatched_and_missing_responses_are_explicit(tmp_path):
    manifest, _ = export(tmp_path, 1)
    pid = manifest["packet_ids"]["primary"][0]
    wrong = MockSubscriptionBackend().respond(request(tmp_path, pid))
    wrong["raw_response"] = wrong["raw_response"].replace(pid, "wrong-opaque-id")
    assert EvaluationImporter(tmp_path).import_response(pid, **wrong)["status"] == "incomplete"
    accepted = answer(tmp_path, pid)
    assert accepted["status"] == "ok"
    duplicate = answer(tmp_path, pid, significant_claim=True)
    assert duplicate["error"]["code"] == "duplicate_response"
    assert EvaluationImporter(tmp_path).labels("primary") == [accepted["data"]]
    assert len(list((tmp_path / "attempts").glob("*.json"))) == 3
    report = final_scoring_projection(tmp_path)
    assert report["status"] == "incomplete"
    assert not report["data"]["fp_report_ready"]
    with pytest.raises(ValueError, match="Missing primary or calibration"):
        AdjudicationQueue(tmp_path).build_from_imports()


def test_response_schema_and_duplicate_json_keys_fail_closed(tmp_path):
    manifest, _ = export(tmp_path, 1)
    pid = manifest["packet_ids"]["primary"][0]
    assert answer(tmp_path, pid, condition="Fresh")["status"] == "incomplete"
    assert answer(tmp_path, pid, evidence_support="yes")["status"] == "incomplete"
    response = MockSubscriptionBackend().respond(request(tmp_path, pid))
    response["raw_response"] = response["raw_response"].replace('"uncertain":false', '"uncertain":false,"uncertain":true')
    assert EvaluationImporter(tmp_path).import_response(pid, **response)["status"] == "incomplete"
    assert EvaluationImporter(tmp_path).labels() == []


@pytest.mark.parametrize("field", ["model", "mode", "date", "context_id", "prompt_sha256"])
def test_actual_model_mode_date_prompt_and_context_must_match(tmp_path, field):
    manifest, _ = export(tmp_path, 1)
    pid = manifest["packet_ids"]["primary"][0]
    response = MockSubscriptionBackend().respond(request(tmp_path, pid))
    response["metadata"][field] = "wrong"
    assert EvaluationImporter(tmp_path).import_response(pid, **response)["status"] == "incomplete"
    assert EvaluationImporter(tmp_path).labels() == []


def test_ab_retest_and_validator_contexts_cannot_be_reused(tmp_path):
    manifest, _ = export(tmp_path, 1)
    a = request(tmp_path, manifest["packet_ids"]["calibration_a"][0])
    b = request(tmp_path, manifest["packet_ids"]["calibration_b"][0])
    response = MockSubscriptionBackend().respond(b)
    response["metadata"]["context_id"] = a["context_id"]
    assert EvaluationImporter(tmp_path).import_response(b["packet"]["id"], **response)["status"] == "incomplete"
    response["metadata"]["context_id"] = "astra-validator-context"
    assert EvaluationImporter(tmp_path).import_response(b["packet"]["id"], **response)["status"] == "incomplete"
    primary = request(tmp_path, manifest["packet_ids"]["primary"][0])
    retest = request(tmp_path, manifest["packet_ids"]["retest"][0])
    assert primary["context_id"] != retest["context_id"]
    assert a["prompt"] == b["prompt"]
    assert "terra_label" not in json.dumps(a)


def test_all_mandatory_adjudication_reasons_and_final_projection(tmp_path):
    manifest, exporter = export(tmp_path, 3)
    mapping = exporter.mapping()
    overrides = {}
    for pid, item in mapping.items():
        rid, lane = item["review_id"], item["lane"]
        if rid == "output-000" and lane == "primary":
            overrides[pid] = {"significant_claim": True, "normalized_type": "disproportionate_scale",
                              "evidence_support": True, "decision_relevance": True}
        if rid == "output-001" and lane == "primary":
            overrides[pid] = {"uncertain": True}
        if rid == "output-002" and lane == "calibration_a":
            overrides[pid] = {"evidence_support": True}
    import_all(tmp_path, manifest, overrides)
    original = copy.deepcopy(EvaluationImporter(tmp_path).labels("primary"))
    packets = AdjudicationQueue(tmp_path).build_from_imports()
    assert len(packets) == 3
    queue = json.loads((tmp_path / "private" / "adjudication.json").read_text())
    reasons = {item["review_id"]: item["reasons"] for item in queue["mapping"].values()}
    assert reasons == {"output-000": ["valid_positive"], "output-001": ["uncertainty"],
                       "output-002": ["calibration_disagreement"]}
    pending = final_scoring_projection(tmp_path)
    assert pending["status"] == "incomplete"
    assert pending["data"]["final_labels"] == []
    assert not pending["data"]["fp_report_ready"]
    for packet in packets:
        assert set(packet) == {"schema_version", "id", "task", "card", "claim", "cited_evidence", "explanation"}
        assert "reasons" not in packet
        assert answer(tmp_path, packet["id"], false_positive=True)["status"] == "ok"
    report = final_scoring_projection(tmp_path)
    assert report["status"] == "ok"
    assert report["data"]["queue_complete"] and report["data"]["fp_report_ready"]
    assert report["data"]["raw_primary"] == original
    assert EvaluationImporter(tmp_path).labels("primary") == original
    assert all(item["lane"] == "adjudication" for item in report["data"]["final_labels"])
    assert all(not item["significant_claim"] for item in report["data"]["final_labels"])
    assert all(item["false_positive"] for item in report["data"]["final_labels"])
    assert report["data"]["denominators"]["adjudication_required"] == 3
    assert AdjudicationQueue(tmp_path).build_from_imports() == packets


def test_valid_positive_requires_explicit_false_positive_not_claim_vote(tmp_path):
    manifest, _ = export(tmp_path, 1)
    pid = manifest["packet_ids"]["primary"][0]
    import_all(tmp_path, manifest, {pid: {"significant_claim": True}})
    packet = AdjudicationQueue(tmp_path).build_from_imports()[0]
    response = MockSubscriptionBackend().respond(request(tmp_path, packet["id"]))
    label = json.loads(response["raw_response"])
    del label["false_positive"]
    response["raw_response"] = json.dumps(label)
    assert EvaluationImporter(tmp_path).import_response(packet["id"], **response)["status"] == "incomplete"
    # A genuinely missed defect on the 'valid' proposal is explicitly NOT a false positive.
    assert answer(tmp_path, packet["id"], significant_claim=True, false_positive=False)["status"] == "ok"
    final = final_scoring_projection(tmp_path)["data"]["final_labels"][0]
    assert final["significant_claim"] and not final["false_positive"]


def test_retest_is_not_majority_vote_or_scoring_replacement(tmp_path):
    manifest, _ = export(tmp_path, 1)
    retest_pid = manifest["packet_ids"]["retest"][0]
    import_all(tmp_path, manifest, {retest_pid: {"significant_claim": True}})
    assert AdjudicationQueue(tmp_path).build_from_imports() == []
    result = final_scoring_projection(tmp_path)
    assert result["status"] == "ok"
    assert not result["data"]["final_labels"][0]["significant_claim"]
    assert result["data"]["retest"][0]["significant_claim"]


def test_external_mapping_and_labels_cannot_override_frozen_sources(tmp_path):
    manifest, exporter = export(tmp_path, 1)
    import_all(tmp_path, manifest)
    importer = EvaluationImporter(tmp_path)
    labels = importer.labels("primary")
    calibration = importer.labels("calibration_a") + importer.labels("calibration_b")
    mapping = exporter.mapping()
    mapping[labels[0]["packet_id"]]["variant"] = "def"
    with pytest.raises(ValueError, match="mapping differs"):
        AdjudicationQueue(tmp_path).build(labels, calibration, mapping)
    labels[0]["uncertain"] = True
    with pytest.raises(ValueError, match="immutable imported"):
        AdjudicationQueue(tmp_path).build(labels, calibration, exporter.mapping())


def test_uncertain_adjudication_retains_response_but_blocks_final_fp(tmp_path):
    manifest, _ = export(tmp_path, 1)
    primary = manifest["packet_ids"]["primary"][0]
    import_all(tmp_path, manifest, {primary: {"uncertain": True}})
    packet = AdjudicationQueue(tmp_path).build_from_imports()[0]
    assert answer(tmp_path, packet["id"], uncertain=True)["status"] == "ok"
    report = final_scoring_projection(tmp_path)
    assert report["status"] == "incomplete"
    assert not report["data"]["queue_complete"]
    assert not report["data"]["fp_report_ready"]
    assert report["data"]["final_labels"] == []
    assert report["data"]["adjudications"][0]["uncertain"]
    assert any(item["reason"] == "unresolved_uncertainty" for item in report["data"]["missingness"])


def test_target_type_text_cannot_escape_hidden_mapping(tmp_path):
    reviews, evidence, policy = fixture(1)
    reviews[0]["claim"] += " disproportionate_scale"
    with pytest.raises(ValueError, match="hidden identity or assignment"):
        EvaluationExporter(tmp_path).export(reviews, evidence, 17, policy)


def test_import_before_sampling_freeze_fails_closed(tmp_path):
    result = EvaluationImporter(tmp_path).import_response("opaque-unknown", "{}", {})
    assert result["status"] == "incomplete"
    assert result["error"]["code"] == "batch_not_frozen"
    assert len(list((tmp_path / "attempts").glob("*.json"))) == 1


def test_interrupted_manifest_publish_can_resume_without_poisoning_freeze(tmp_path, monkeypatch):
    from little_pom.runtime import storage

    original_link = storage.os.link

    def interrupt_manifest(source, destination, *args, **kwargs):
        if destination.name == "manifest.json":
            raise OSError("Simulated interruption before atomic publication")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(storage.os, "link", interrupt_manifest)
    with pytest.raises(OSError, match="Simulated interruption"):
        export(tmp_path, 1)
    assert not (tmp_path / "private" / "manifest.json").exists()
    assert json.loads((tmp_path / "private" / "mapping.json").read_text())
    assert not list(tmp_path.rglob(".pending-*"))
    monkeypatch.setattr(storage.os, "link", original_link)
    manifest, _ = export(tmp_path, 1)
    assert json.loads((tmp_path / "private" / "manifest.json").read_text()) == manifest


def test_interrupted_label_publish_preserves_attempt_and_allows_retry(tmp_path, monkeypatch):
    from little_pom.runtime import storage

    manifest, _ = export(tmp_path, 1)
    pid = manifest["packet_ids"]["primary"][0]
    original_link = storage.os.link

    def interrupt_label(source, destination, *args, **kwargs):
        if destination.parent == tmp_path / "labels" / "primary":
            raise OSError("Simulated interruption before label publication")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(storage.os, "link", interrupt_label)
    assert answer(tmp_path, pid)["status"] == "incomplete"
    assert EvaluationImporter(tmp_path).labels("primary") == []
    assert len(list((tmp_path / "attempts").glob("*.json"))) == 1
    assert not list(tmp_path.rglob(".pending-*"))
    monkeypatch.setattr(storage.os, "link", original_link)
    accepted = answer(tmp_path, pid)
    assert accepted["status"] == "ok"
    assert EvaluationImporter(tmp_path).labels("primary") == [accepted["data"]]
    assert len(list((tmp_path / "attempts").glob("*.json"))) == 2
