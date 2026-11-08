"""Offline subscription measurement with immutable, independently blinded contexts.

Calibration freezes 12 outputs per condition (six per variant when possible),
filling variant shortages only within that condition. Condition shortages are
reported, never borrowed or replaced. Retest uses ceil(N / 5) successful outputs.
Adjudication replaces all five scoring axes for queued reviews, never the original
Terra/Astra labels. Retest is exclusively a stability observation, not a vote.
Agreement is cross-model calibration, not human validity or independent experts.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import date as Date
from pathlib import Path
from typing import Any

from little_pom.contracts import canonical_json, derive_seed, sha256_hash, validate
from little_pom.runtime import ArtifactStore

CONDITIONS = ("Fresh", "RandomHistory", "RoutedHistory")
AXES = ("significant_claim", "normalized_type", "evidence_support", "decision_relevance", "uncertain")
LANES = ("primary", "retest", "calibration_a", "calibration_b", "adjudication")
PROMPT = (
    "Evaluate only the supplied scientific review and its actually cited evidence. "
    "Do not infer experiment assignments. Decide whether the review makes a substantive "
    "significant-defect claim, normalize its type, assess evidence support and whether "
    "it should change the next decision, and flag uncertainty. Return one JSON object "
    "with schema_version '1.0', id equal to packet.id, significant_claim (boolean), "
    "normalized_type (goal_mismatch, ignored_result, assumption_as_fact, "
    "unmet_precondition, no_link_to_decision, disproportionate_scale, other, or none), "
    "evidence_support (boolean), decision_relevance (boolean), uncertain (boolean), "
    "and rationale (string). Start a fresh context; do not consult other conversations."
)
ADJUDICATION_PROMPT = PROMPT + (
    " Independently resolve this case without prior evaluator labels. Also return "
    "false_positive (boolean): true only if the review falsely asserts a significant "
    "defect; false if the alleged defect is genuine or no significant defect is alleged. "
    "An originally accepted proposal may still contain a genuine missed defect."
)
PROMPT_HASHES = {"evaluation": sha256_hash(PROMPT), "adjudication": sha256_hash(ADJUDICATION_PROMPT)}
# These indicate experimental metadata, not ordinary uses of scientific 'valid'.
FORBIDDEN_TEXT = re.compile(
    r"\b(?:RandomHistory|RoutedHistory|Fresh)\b|"
    r"\b(?:condition|variant|reviewer(?:_id)?|worker_id|panel_id|target[_ ]defect|"
    r"target[_ ]evidence|informed[_ ]workers|routing[_ ](?:score|metadata)|"
    r"validator[_ ]history|terra[_ ]label)\s*[:=]",
    re.IGNORECASE,
)


def _opaque(seed: Any, domain: str, *parts: Any) -> str:
    return sha256_hash([derive_seed(seed, domain, *parts), domain, *parts])[:32]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _put(path: Path, value: dict) -> None:
    data = canonical_json(value)
    try:
        ArtifactStore(path.parent).put_once(path.name, data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"Immutable artifact conflict: {path.name}") from None


def _result(data: dict | None = None, code: str | None = None, message: str = "") -> dict:
    return {"status": "incomplete" if code else "ok", "data": data,
            "error": {"code": code, "message": message} if code else None}


def measurement_policy(terra_model: str, astra_model: str, mode: str, date: str) -> dict:
    """Freeze actual subscription model identifiers, not assumed UI availability."""
    for value in (terra_model, astra_model, mode):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Actual model identifiers and mode must be nonempty")
    Date.fromisoformat(date)
    body = {"schema_version": "1.0", "calibration_target": 36,
            "calibration_per_condition": 12, "conditions": list(CONDITIONS),
            "retest_fraction": 0.2, "retest_rounding": "ceiling",
            "shortage_policy": "all_available_no_replacement_no_cross_condition_borrowing",
            "variant_balance": "equal_then_fill_within_condition",
            "models": {"terra": terra_model, "astra": astra_model}, "mode": mode,
            "date": date, "agreement_kind": "cross_model_not_human_validity"}
    return {**body, "id": sha256_hash(body)[:32]}


def _check_policy(policy: dict) -> None:
    expected = measurement_policy(policy["models"]["terra"], policy["models"]["astra"],
                                  policy["mode"], policy["date"])
    if policy != expected:
        raise ValueError("Measurement policy must be explicitly frozen without modified sampling rules")


def _check_blind(packet: dict, hidden_tokens: list[str]) -> None:
    validate("TerraPacket", packet)
    text = "\n".join([packet["task"], packet["card"], packet["claim"], packet["explanation"],
                       *(item["text"] for item in packet["cited_evidence"])])
    if FORBIDDEN_TEXT.search(text):
        raise ValueError("Blind packet contains experimental metadata in text")
    for token in hidden_tokens:
        if token and re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text, re.IGNORECASE):
            raise ValueError("Blind packet contains hidden identity or assignment text")


class EvaluationExporter:
    """Export local files only; the operator opens each request in a fresh subscription context."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def mapping(self) -> dict:
        return _read(self.root / "private" / "mapping.json")

    def export(self, reviews: list[dict], evidence: dict, seed: Any, policy: dict) -> dict:
        _check_policy(policy)
        if set(evidence) != {"reviews", "items"}:
            raise ValueError("Evidence input must separate review contexts and evidence items")
        by_id: dict[str, dict] = {}
        for review in reviews:
            validate("Review", review)
            if review["id"] in by_id:
                raise ValueError("Duplicate review ID")
            by_id[review["id"]] = review
        if set(by_id) != set(evidence["reviews"]):
            raise ValueError("Review contexts must match successful review IDs exactly")
        # Freeze even the content hash, so a re-export cannot change bodies after labels.
        input_hash = sha256_hash({"reviews": sorted(reviews, key=lambda x: x["id"]),
                                  "evidence": evidence, "seed": seed, "policy": policy})
        manifest_path = self.root / "private" / "manifest.json"
        if manifest_path.exists():
            existing = _read(manifest_path)
            if existing["input_hash"] != input_hash:
                raise ValueError("Measurement batch is already frozen")
            return existing
        contexts = evidence["reviews"]
        for review_id, context in contexts.items():
            required = {"task_id", "task", "card", "condition", "variant", "panel_id", "reviewer_id"}
            if not required <= set(context):
                raise ValueError(f"Missing measurement context for {review_id}")
            if context["condition"] not in CONDITIONS or context["variant"] not in ("valid", "def"):
                raise ValueError("Unknown condition or variant")
            if not all(isinstance(context[key], str) and context[key] for key in required):
                raise ValueError("Measurement context fields must be nonempty strings")
        rank = lambda rid, domain: (derive_seed(seed, domain, rid), rid)
        calibration: list[str] = []
        counts = {}
        for condition in CONDITIONS:
            candidates = [rid for rid in by_id if contexts[rid]["condition"] == condition]
            chosen: list[str] = []
            for variant in ("valid", "def"):
                group = [rid for rid in candidates if contexts[rid]["variant"] == variant]
                chosen.extend(sorted(group, key=lambda rid: rank(rid, "calibration"))[:6])
            remaining = sorted(set(candidates) - set(chosen), key=lambda rid: rank(rid, "calibration"))
            chosen.extend(remaining[:12 - len(chosen)])
            calibration.extend(chosen)
            counts[condition] = {"available": len(candidates), "selected": len(chosen),
                                 "shortage": max(0, 12 - len(chosen)),
                                 "by_variant": {v: sum(contexts[r]["variant"] == v for r in chosen)
                                                for v in ("valid", "def")}}
        retest = sorted(by_id, key=lambda rid: rank(rid, "retest"))[:(len(by_id) + 4) // 5]
        packets: dict[str, dict] = {}
        requests: dict[str, dict] = {}
        mapping: dict[str, dict] = {}
        # All reviewer/panel/task identity tokens are checked, not just this packet's.
        hidden_tokens = sorted({ctx[key] for ctx in contexts.values()
                                for key in ("reviewer_id", "panel_id", "task_id")})
        hidden_tokens.extend(ctx["target_defect"] for ctx in contexts.values()
                             if isinstance(ctx.get("target_defect"), str) and ctx["target_defect"])
        for rid in sorted(by_id):
            review, context = by_id[rid], contexts[rid]
            citation_ids = review["evidence_ids"]
            if len(set(citation_ids)) != len(citation_ids):
                raise ValueError("Duplicate evidence citation")
            if any(cid not in evidence["items"] for cid in citation_ids):
                raise ValueError("Review cites missing evidence")
            lanes = ["primary"]
            if rid in retest:
                lanes.append("retest")
            if rid in calibration:
                lanes.extend(("calibration_a", "calibration_b"))
            for lane in lanes:
                pid = _opaque(seed, "measurement-packet", input_hash, rid, lane)
                replacements = {cid: _opaque(seed, "measurement-citation", pid, cid) for cid in citation_ids}
                def scrub(text: str) -> str:
                    if not isinstance(text, str):
                        raise ValueError("Blind scientific content must be text")
                    for cid in sorted(replacements, key=len, reverse=True):
                        text = text.replace(cid, replacements[cid])
                    return text
                packet = {"schema_version": "1.0", "id": pid, "task": scrub(context["task"]),
                          "card": scrub(context["card"]), "claim": scrub(review["claim"]),
                          "cited_evidence": [{"id": replacements[cid], "text": scrub(evidence["items"][cid]["text"])}
                                             for cid in citation_ids],
                          "explanation": scrub(review["decision_relevance_text"])}
                _check_blind(packet, hidden_tokens + [rid])
                request = self._request(packet, lane, policy, seed)
                packets[pid], requests[pid] = packet, request
                mapping[pid] = {"schema_version": "1.0", "id": pid, "task_id": context["task_id"],
                                "review_id": rid, "lane": lane, "context_id": request["context_id"],
                                "condition": context["condition"], "variant": context["variant"],
                                "panel_id": context["panel_id"], "reviewer_id": context["reviewer_id"],
                                "target_defect": context.get("target_defect"),
                                "citation_mapping": replacements}
        manifest = {"schema_version": "1.0", "id": input_hash[:32], "input_hash": input_hash,
                    "policy": policy, "prompt_hashes": PROMPT_HASHES,
                    "calibration_review_ids": calibration, "retest_review_ids": retest,
                    "packet_ids": {lane: [pid for pid, item in mapping.items() if item["lane"] == lane]
                                   for lane in LANES},
                    "denominators": {"successful_outputs": len(by_id), "calibration_target": 36,
                                     "calibration_selected": len(calibration), "calibration_conditions": counts,
                                     "retest_selected": len(retest), "retest_rounding": "ceiling"},
                    "agreement_kind": "cross_model_not_human_validity"}
        # Validate the entire batch before publishing any exports.
        _put(self.root / "private" / "mapping.json", mapping)
        for pid in packets:
            _put(self.root / "blind" / f"{pid}.json", packets[pid])
            _put(self.root / "subscription" / f"{pid}.json", requests[pid])
        _put(manifest_path, manifest)
        return manifest

    @staticmethod
    def _request(packet: dict, lane: str, policy: dict, seed: Any) -> dict:
        prompt = ADJUDICATION_PROMPT if lane == "adjudication" else PROMPT
        return {"packet": packet, "prompt": prompt,
                "model": policy["models"]["terra" if lane in ("primary", "retest") else "astra"],
                "mode": policy["mode"], "date": policy["date"],
                "context_id": _opaque(seed, "independent-subscription-context", packet["id"]),
                "prompt_sha256": sha256_hash(prompt)}


class EvaluationImporter:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def labels(self, lane: str | None = None) -> list[dict]:
        if lane is not None and lane not in LANES:
            raise ValueError("Unknown measurement lane")
        records = [_read(path) for path in sorted((self.root / "labels").glob("*/*.json"))]
        return [record for record in records if lane is None or record["lane"] == lane]

    def import_response(self, packet_id: str, raw_response: str, metadata: dict) -> dict:
        # Every received attempt, including malformed/duplicate/mismatched responses,
        # is retained separately; accepted labels are never overwritten.
        attempt = {"schema_version": "1.0", "id": uuid.uuid4().hex, "packet_id": packet_id,
                   "raw_response": raw_response, "metadata": metadata}
        try:
            canonical_json(attempt)
        except (TypeError, ValueError) as exc:
            return _result(code="invalid_boundary", message=str(exc))
        _put(self.root / "attempts" / f"{attempt['id']}.json", attempt)
        try:
            if not (self.root / "private" / "manifest.json").exists():
                return _result(code="batch_not_frozen", message="Sampling must freeze before any response import")
            mapping = _read(self.root / "private" / "mapping.json")
            queue_path = self.root / "private" / "adjudication.json"
            if queue_path.exists():
                mapping.update(_read(queue_path)["mapping"])
            if packet_id not in mapping:
                return _result(code="unknown_packet", message="No frozen packet matches this response")
            record = mapping[packet_id]
            destination = self.root / "labels" / record["lane"] / f"{packet_id}.json"
            if destination.exists():
                return _result(code="duplicate_response", message="Original label retained; attempt logged")
            request = _read(self.root / "subscription" / f"{packet_id}.json")
            required = {"model", "mode", "date", "context_id", "prompt_sha256"}
            if not isinstance(metadata, dict) or not required <= set(metadata):
                raise ValueError("Missing actual subscription provenance")
            for key in required:
                if metadata[key] != request[key]:
                    raise ValueError(f"Subscription provenance mismatch: {key}")
            if not isinstance(raw_response, str):
                raise ValueError("Raw subscription response must be preserved as text")
            def unique_keys(pairs: list[tuple]) -> dict:
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("Duplicate JSON response key")
                    obj[key] = value
                return obj
            label = json.loads(raw_response, object_pairs_hook=unique_keys)
            validate("EvalLabel", label)
            if label["id"] != packet_id:
                raise ValueError("Response ID does not match exported opaque packet")
            if record["lane"] == "adjudication" and record["variant"] == "valid":
                if type(label.get("false_positive")) is not bool:
                    raise ValueError("Valid-card adjudication requires explicit false_positive")
            enriched = {**label, "task_id": record["task_id"], "packet_id": packet_id,
                        "review_id": record["review_id"], "lane": record["lane"],
                        "provenance": {**{key: metadata[key] for key in required}, "raw_response": raw_response,
                                       "attempt_id": attempt["id"], "packet_sha256": sha256_hash(request["packet"]),
                                       "prompt": request["prompt"], "metadata": copy.deepcopy(metadata)}}
            validate("MeasurementLabel", enriched)
            try:
                ArtifactStore(destination.parent).put_once(destination.name, canonical_json(enriched))
            except FileExistsError:
                return _result(code="duplicate_response", message="Concurrent original label retained")
            return _result(enriched)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return _result(code="invalid_response", message=str(exc))


class AdjudicationQueue:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def build_from_imports(self) -> list[dict]:
        importer = EvaluationImporter(self.root)
        return self.build(importer.labels("primary"),
                          importer.labels("calibration_a") + importer.labels("calibration_b"),
                          EvaluationExporter(self.root).mapping())

    def build(self, labels: list[dict], calibration_labels: list[dict], hidden_mapping: dict) -> list[dict]:
        frozen = EvaluationExporter(self.root).mapping()
        if hidden_mapping != frozen:
            raise ValueError("Adjudication mapping differs from frozen measurement mapping")
        if any(item.get("lane") != "primary" for item in labels):
            raise ValueError("Primary labels must be supplied separately")
        if any(item.get("lane") not in ("calibration_a", "calibration_b") for item in calibration_labels):
            raise ValueError("Calibration A/B labels must be supplied separately")
        expected = {pid for pid, item in frozen.items()
                    if item["lane"] in ("primary", "calibration_a", "calibration_b")}
        supplied: dict[str, dict] = {}
        for label in labels + calibration_labels:
            pid = label["packet_id"]
            if pid in supplied or pid not in expected:
                raise ValueError("Duplicate or unexpected adjudication source label")
            if label["review_id"] != frozen[pid]["review_id"] or label["lane"] != frozen[pid]["lane"]:
                raise ValueError("Adjudication source identity mismatch")
            persisted = self.root / "labels" / frozen[pid]["lane"] / f"{pid}.json"
            if not persisted.exists() or _read(persisted) != label:
                raise ValueError("Adjudication requires immutable imported source labels")
            supplied[pid] = label
        if set(supplied) != expected:
            raise ValueError("Missing primary or calibration responses; adjudication cannot freeze")
        manifest = _read(self.root / "private" / "manifest.json")
        primary = {value["review_id"]: value for value in labels}
        calibration = {(value["review_id"], value["lane"]): value for value in calibration_labels}
        packets, mapping = [], {}
        for rid in sorted(primary):
            label = primary[rid]
            source = frozen[label["packet_id"]]
            reasons = []
            if label["uncertain"]:
                reasons.append("uncertainty")
            if source["variant"] == "valid" and label["significant_claim"]:
                reasons.append("valid_positive")
            a, b = calibration.get((rid, "calibration_a")), calibration.get((rid, "calibration_b"))
            if a is not None and b is not None and any(a[key] != b[key] for key in AXES):
                reasons.append("calibration_disagreement")
            if not reasons:
                continue
            packet = _read(self.root / "blind" / f"{label['packet_id']}.json")
            pid = _opaque(manifest["id"], "adjudication-packet", rid)
            packet["id"] = pid
            # Re-key evidence as well; never reuse the original context's citation IDs.
            citation_mapping = dict(source["citation_mapping"])
            for item in packet["cited_evidence"]:
                old, new = item["id"], _opaque(manifest["id"], "adjudication-citation", pid, item["id"])
                citation_mapping = {key: new if value == old else value
                                    for key, value in citation_mapping.items()}
                for field in ("task", "card", "claim", "explanation"):
                    packet[field] = packet[field].replace(old, new)
                for cited in packet["cited_evidence"]:
                    cited["text"] = cited["text"].replace(old, new)
                item["id"] = new
            _check_blind(packet, [])
            request = EvaluationExporter._request(packet, "adjudication", manifest["policy"], manifest["id"])
            mapping[pid] = {**source, "id": pid, "lane": "adjudication", "reasons": reasons,
                            "context_id": request["context_id"], "primary_packet_id": label["packet_id"],
                            "citation_mapping": citation_mapping}
            packets.append(packet)
            _put(self.root / "blind" / f"{pid}.json", packet)
            _put(self.root / "subscription" / f"{pid}.json", request)
        _put(self.root / "private" / "adjudication.json",
             {"schema_version": "1.0", "id": sha256_hash(mapping)[:32], "mapping": mapping,
              "source_labels_sha256": sha256_hash(supplied), "required_count": len(mapping)})
        return packets


def final_scoring_projection(root: str | Path) -> dict:
    """Project adjudicated scoring axes without mutating originals or voting with retest."""
    root = Path(root)
    manifest = _read(root / "private" / "manifest.json")
    importer = EvaluationImporter(root)
    lanes = {lane: importer.labels(lane) for lane in LANES}
    mapping = EvaluationExporter(root).mapping()
    queue_path = root / "private" / "adjudication.json"
    queue = _read(queue_path) if queue_path.exists() else None
    missingness = []
    for lane in LANES[:-1]:
        observed = {item["packet_id"] for item in lanes[lane]}
        missingness.extend({"lane": lane, "packet_id": pid, "reason": "missing_response"}
                           for pid in manifest["packet_ids"][lane] if pid not in observed)
    if queue is None:
        missingness.append({"lane": "adjudication", "packet_id": None, "reason": "queue_not_frozen"})
    queued = queue["mapping"] if queue else {}
    adjudicated = {item["packet_id"]: item for item in lanes["adjudication"]}
    missingness.extend({"lane": "adjudication", "packet_id": pid, "reason": "missing_response"}
                       for pid in queued if pid not in adjudicated)
    missingness.extend({"lane": "adjudication", "packet_id": pid, "reason": "unresolved_uncertainty"}
                       for pid, item in adjudicated.items() if item["uncertain"])
    by_review = {item["review_id"]: item for item in lanes["adjudication"] if not item["uncertain"]}
    required_reviews = {item["review_id"] for item in queued.values()}
    final = []
    for original in lanes["primary"]:
        rid = original["review_id"]
        if rid in required_reviews and rid not in by_review:
            continue  # Missing mandatory adjudication is not silently scored as Terra.
        selected = copy.deepcopy(by_review.get(rid, original))
        selected["primary_packet_id"] = original["packet_id"]
        if mapping[original["packet_id"]]["variant"] == "valid":
            if rid in by_review:
                if type(selected.get("false_positive")) is not bool:
                    missingness.append({"lane": "adjudication", "packet_id": selected["packet_id"],
                                        "reason": "missing_false_positive_resolution"})
                    continue
            else:
                selected["false_positive"] = False
        validate("MeasurementLabel", selected)
        final.append(selected)
    queue_complete = queue is not None and all(
        pid in adjudicated and not adjudicated[pid]["uncertain"] for pid in queued)
    fp_ready = queue_complete and not any(item["lane"] != "retest" for item in missingness)
    report = {"schema_version": "1.0", "id": manifest["id"],
              "raw_primary": lanes["primary"], "retest": lanes["retest"],
              "calibration_a": lanes["calibration_a"], "calibration_b": lanes["calibration_b"],
              "adjudications": lanes["adjudication"], "final_labels": final,
              "missingness": missingness, "queue_complete": queue_complete, "fp_report_ready": fp_ready,
              "denominators": {**manifest["denominators"],
                               "observed": {lane: len(records) for lane, records in lanes.items()},
                               "adjudication_required": len(queued), "final_scored": len(final)},
              "agreement_kind": "cross_model_not_human_validity",
              "projection_policy": "Required adjudication replaces all scoring axes; original labels retained; retest never votes"}
    return _result(report, "measurement_incomplete" if missingness else None,
                   "Required measurement responses or frozen queue missing" if missingness else "")


class MockSubscriptionBackend:
    """Explicit offline fixture transport; never selected by a production importer."""

    def __init__(self, response: dict | None = None):
        self.response = response

    def respond(self, request: dict) -> dict:
        label = copy.deepcopy(self.response) if self.response is not None else {
            "schema_version": "1.0", "significant_claim": False, "normalized_type": "none",
            "evidence_support": False, "decision_relevance": False, "uncertain": False,
            "rationale": "Explicit offline subscription fixture; no scientific validity claim.",
            "false_positive": False}
        label["id"] = request["packet"]["id"]
        return {"raw_response": canonical_json(label).decode("utf-8"),
                "metadata": {key: request[key] for key in ("model", "mode", "date", "context_id", "prompt_sha256")}}


__all__ = ["EvaluationExporter", "EvaluationImporter", "AdjudicationQueue", "measurement_policy",
           "final_scoring_projection", "MockSubscriptionBackend", "PROMPT_HASHES"]
