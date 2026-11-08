"""Seeded independent review execution; all transport and resume belong to Runtime."""

from __future__ import annotations

import copy
import random
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from little_pom.contracts import canonical_json, derive_seed, sha256_hash, validate
from .packets import BlindingError, assert_blind


REVIEW_PROMPT = """Review the proposed scientific action using the task, common dossier, card,
and any supplied working context. Work independently and assess whether a
substantive problem changes the next scientific step. Explain the claimed
problem freely; do not select from a prescribed taxonomy. Cite only evidence
actually available in the supplied context, using its exact evidence IDs.
Do not invent evidence or treat missing context as evidence of a defect.
Return one JSON object with schema_version='1.0', id equal to the packet id,
status='claim' or 'no_claim', claim (your substantive assessment), evidence_ids
(a list of cited evidence IDs), and decision_relevance_text (why the assessment
changes the next step, or why no significant change is justified)."""


def _opaque(domain: str, *values: Any) -> str:
    return sha256_hash({"domain": domain, "values": list(values)})[:32]


def _publish(store: Any, path: str, payload: dict) -> None:
    """Permit byte-equivalent resume, never replace an immutable artifact."""
    try:
        store.put_once(path, payload)
    except FileExistsError:
        if canonical_json(store.read(path)) != canonical_json(payload):
            raise ValueError(f"Immutable review artifact conflict: {path}") from None


class RunList:
    """An internal mapping, never an exported model packet."""

    @staticmethod
    def build(panels: list[dict], seed: int) -> list[dict]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        slots = []
        panel_ids: set[str] = set()
        for panel in sorted(panels, key=lambda value: value["id"]):
            validate("Panel", panel)
            if panel["id"] in panel_ids:
                raise ValueError("Duplicate panel ID")
            panel_ids.add(panel["id"])
            specs = panel["reviewer_specs"]
            if len(specs) != 2 or len({spec["reviewer_key"] for spec in specs}) != 2:
                raise ValueError("A panel requires two distinct reviewer slots")
            for spec in sorted(specs, key=lambda value: value["reviewer_key"]):
                identity = (seed, panel["id"], spec["reviewer_key"])
                slots.append({
                    "schema_version": "1.0",
                    "id": _opaque("review-execution", *identity),
                    "task_id": panel["task_id"],
                    "panel_id": panel["id"],
                    "reviewer_key": spec["reviewer_key"],
                    "worker_id": spec["worker_id"],
                    "packet_id": _opaque("review-packet", *identity),
                })
        random.Random(derive_seed(seed, "review-dispatch")).shuffle(slots)
        for position, slot in enumerate(slots):
            slot["position"] = position
        return slots


class ReviewRunner:
    """Consume frozen packets and publish all requested/observed denominators.

    Use ``run_all`` to preserve seeded dispatch across the whole pilot. ``run``
    provides the required single-panel API with the same immutable execution IDs.
    Resumption always calls Runtime, which reuses its ledger rather than sampling
    again. No transport exception or invalid output triggers a repair call here.
    """

    def __init__(self, *, seed: int = 0, concurrency: int = 2,
                 model_config: dict | None = None, output_budget: int = 4096):
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
            raise ValueError("concurrency must be a positive integer")
        if isinstance(output_budget, bool) or not isinstance(output_budget, int) or output_budget < 1:
            raise ValueError("output_budget must be a positive integer")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        self.seed = seed
        self.concurrency = concurrency
        self.model_config = copy.deepcopy(model_config or {})
        self.output_budget = output_budget

    def run(self, panel: dict, packets: dict[str, dict], client: Any, store: Any) -> dict:
        return self.run_all([panel], {panel["id"]: packets}, client, store)[0]

    def run_all(self, panels: list[dict], packets: dict[str, dict[str, dict]],
                client: Any, store: Any) -> list[dict]:
        """Dispatch a complete frozen plan; return outcomes in input panel order.

        ``packets[panel_id][reviewer_key]`` is a ReviewerPacket. The RunList IDs
        should be used when constructing packets; arbitrary opaque packet IDs
        are also accepted and recorded in the private dispatch mapping.
        """
        panels = copy.deepcopy(panels)
        slots = RunList.build(panels, self.seed)
        panel_map = {panel["id"]: panel for panel in panels}
        if set(packets) != set(panel_map):
            raise ValueError("Packet panel IDs must exactly match the requested panels")
        prepared: list[tuple[dict, dict]] = []
        packet_ids: set[str] = set()
        # Validate every boundary before any request is submitted.
        for panel in panels:
            expected = {spec["reviewer_key"] for spec in panel["reviewer_specs"]}
            if set(packets[panel["id"]]) != expected:
                raise ValueError("Packet reviewer keys must exactly match the frozen panel")
        for slot in slots:
            packet = copy.deepcopy(packets[slot["panel_id"]][slot["reviewer_key"]])
            validate("ReviewerPacket", packet)
            assert_blind(packet)
            if packet["id"] in packet_ids or packet["id"] == slot["id"]:
                raise BlindingError("Every slot requires its own distinct opaque packet ID")
            packet_ids.add(packet["id"])
            panel = panel_map[slot["panel_id"]]
            has_state = "worker_context" in packet
            if panel["condition"] == "Fresh" and has_state:
                raise BlindingError("Fresh packets must not contain a worker context")
            if panel["condition"] != "Fresh" and not has_state:
                raise BlindingError("History panels require their own worker context")
            slot["packet_id"] = packet["id"]
            slot["packet_sha256"] = sha256_hash(packet)
            prepared.append((slot, packet))
        dispatch_id = _opaque("review-dispatch-manifest", self.seed, sorted(panel_map))
        _publish(store, f"reviews/dispatch/{dispatch_id}.json", {
            "schema_version": "1.0", "id": dispatch_id,
            "seed": self.seed, "concurrency": self.concurrency,
            "prompt_sha256": sha256_hash(REVIEW_PROMPT),
            "model_config": copy.deepcopy(self.model_config),
            "output_budget": self.output_budget, "slots": slots,
        })
        # Submission order is frozen; completion order never determines results.
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [executor.submit(self._execute, slot, packet, client, store)
                       for slot, packet in prepared]
            results = [future.result() for future in futures]
        grouped: dict[str, list[tuple[dict, dict]]] = {panel["id"]: [] for panel in panels}
        for (slot, _), result in zip(prepared, results, strict=True):
            grouped[slot["panel_id"]].append((slot, result))
        return [self._outcome(panel, grouped[panel["id"]], store) for panel in panels]

    def _execute(self, slot: dict, packet: dict, client: Any, store: Any) -> dict:
        request = {
            "model": "qwen3.8-27b", "config": copy.deepcopy(self.model_config),
            "instructions": REVIEW_PROMPT, "packet": copy.deepcopy(packet),
            # The entire scientific local history is already in worker_context;
            # never repeat it or append any prior review to it.
            "history": [], "output_budget": self.output_budget,
            "task_id": slot["task_id"], "stage": "reviews",
        }
        try:
            result = client.call(request, "Review", slot["id"])
            validate("ServiceResult", result)
            if result["status"] == "ok":
                review = copy.deepcopy(result["data"])
                validate("Review", review)
                # Provider-supplied IDs are not trusted provenance.
                review["id"] = slot["id"]
                review["packet_id"] = packet["id"]
                validate("Review", review)
                result = {"status": "ok", "data": review, "error": None}
        except Exception as exc:
            # Never retry an ambiguous invocation, even when the client raises.
            result = {"status": "incomplete", "data": None, "error": {
                "code": "review_execution_error", "message": str(exc),
                "exception_type": type(exc).__name__,
            }}
        _publish(store, f"reviews/results/{slot['id']}.json", result)
        return result

    def _outcome(self, panel: dict, results: list[tuple[dict, dict]], store: Any) -> dict:
        reviews, missingness = [], []
        for slot, result in sorted(results, key=lambda item: item[0]["reviewer_key"]):
            if result["status"] == "ok":
                reviews.append(result["data"])
            else:
                missingness.append({
                    "execution_id": slot["id"], "packet_id": slot["packet_id"],
                    "reason": result["error"] or {"code": "missing_review"},
                })
        observed, requested = len(reviews), len(results)
        outcome = {
            "schema_version": "1.0", "id": _opaque("panel-outcome", self.seed, panel["id"]),
            "task_id": panel["task_id"], "panel_id": panel["id"],
            "condition": panel["condition"], "variant": panel["variant"],
            "repeat": panel["repeat"], "n_requested": requested, "n_observed": observed,
            "status": "complete" if observed == requested else "partial" if observed else "incomplete",
            "reviews": reviews, "missingness": missingness,
        }
        validate("PanelOutcome", outcome)
        _publish(store, f"reviews/panels/{outcome['id']}.json", outcome)
        return {
            "status": "ok" if outcome["status"] == "complete" else "incomplete",
            "data": outcome,
            "error": None if outcome["status"] == "complete" else {
                "code": "missing_reviews", "message": "Not all requested reviews were observed",
            },
        }
