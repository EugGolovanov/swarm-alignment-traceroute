"""Explicit offline review fixtures, never selected by production runners."""

from __future__ import annotations

from .packets import ReviewerPacketBuilder
from .runner import RunList


def review_fixture(*, seed: int = 17, task_count: int = 3) -> dict:
    """Return panels, blind packets, and Runtime MockBackend response mappings.

    The full pilot has 3 tasks x 3 conditions x 2 variants x 2 repeats x
    2 reviewers = 72 slots. The count always comes from the supplied panels.
    Partial and incomplete behavior is exercised by the explicit client fixture
    in tests rather than silently injected into this successful mock plan.
    """
    panels = []
    for task_index in range(task_count):
        for condition in ("Fresh", "RandomHistory", "RoutedHistory"):
            for variant in ("valid", "def"):
                for repeat in (1, 2):
                    panel_id = f"p{task_index}{condition}{variant}{repeat}"
                    panels.append({
                        "schema_version": "1.0", "id": panel_id,
                        "task_id": f"t{task_index}", "condition": condition,
                        "variant": variant, "repeat": repeat, "router_fallback": False,
                        "reviewer_specs": [
                            {"reviewer_key": f"r{index}",
                             "worker_id": None if condition == "Fresh" else f"w{index}"}
                            for index in range(2)
                        ],
                    })
    slots = RunList.build(panels, seed)
    panel_by_id = {panel["id"]: panel for panel in panels}
    packets: dict[str, dict] = {panel["id"]: {} for panel in panels}
    responses = {}
    for slot in slots:
        panel = panel_by_id[slot["panel_id"]]
        packet = ReviewerPacketBuilder(packet_id=slot["packet_id"]).build(
            {"text": "Determine whether the assay can detect the prespecified concentration."},
            {"text": "The public calibration e-public reports a detection limit of 12 units."},
            {"text": "Use a 3-unit concentration and interpret a negative assay as absence."},
        )
        if panel["condition"] != "Fresh":
            packet["worker_context"] = {
                "summary": "Measured assay sensitivity using calibration standards.",
                "history": [{"role": "assistant", "content": "The 3-unit standard was undetected."}],
                "evidence": [{"evidence_id": "e-private", "text": "3-unit standard: signal below threshold."}],
            }
        packets[panel["id"]][slot["reviewer_key"]] = packet
        cited = "e-public" if panel["condition"] == "Fresh" else "e-private"
        responses[slot["id"]] = {
            "schema_version": "1.0", "id": slot["packet_id"], "status": "claim",
            "claim": f"A negative result cannot establish absence at 3 units; see {cited}.",
            "evidence_ids": [cited],
            "decision_relevance_text": "Increase assay sensitivity before interpreting absence.",
        }
    return {"panels": panels, "packets": packets, "slots": slots, "responses": responses,
            "evidence": {"e-public": {"text": "Calibration detection limit: 12 units."},
                         "e-private": {"text": "3-unit standard: signal below threshold."}}}


def outcome_fixtures() -> list[dict]:
    """Canonical explicit mock outcomes for measurement and analysis consumers."""
    from little_pom.contracts import sha256_hash, validate

    fixture = review_fixture(task_count=1)
    outcomes = []
    for panel, observed in zip(fixture["panels"][:3], (2, 1, 0), strict=True):
        slots = sorted(
            (slot for slot in fixture["slots"] if slot["panel_id"] == panel["id"]),
            key=lambda slot: slot["reviewer_key"],
        )
        reviews = []
        for slot in slots[:observed]:
            review = dict(fixture["responses"][slot["id"]])
            review["id"] = slot["id"]
            review["packet_id"] = slot["packet_id"]
            reviews.append(review)
        outcome = {
            "schema_version": "1.0",
            "id": sha256_hash({"domain": "mock-panel-outcome", "panel": panel["id"]})[:32],
            "task_id": panel["task_id"], "panel_id": panel["id"],
            "condition": panel["condition"], "variant": panel["variant"], "repeat": panel["repeat"],
            "status": ("incomplete", "partial", "complete")[observed],
            "n_requested": len(slots), "n_observed": observed, "reviews": reviews,
            "missingness": [
                {"execution_id": slot["id"], "packet_id": slot["packet_id"],
                 "reason": {"code": "ambiguous_remote_execution",
                            "message": "Durable intent has no response; resampling is forbidden"}}
                for slot in slots[observed:]
            ],
        }
        validate("PanelOutcome", outcome)
        outcomes.append(outcome)
    return outcomes
