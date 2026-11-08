"""Explicitly selected fake subscription responses, never a production fallback."""
from __future__ import annotations

import copy
from little_pom.contracts import sha256_hash
from .mechanism import card_text

DEFAULT_VOCABULARY = {
    "validation": ["validation", "validate", "check", "test"],
    "data": ["data", "dataset", "sample", "measurement"],
    "budget": ["budget", "cost", "resource"],
    "artifact": ["artifact", "output", "result"],
}


def subscription_response(packet: dict) -> dict:
    """Construct transparent fake judgments for offline integration only.

    The fake injector introduces an explicit unsupported universality claim. It
    does not claim to scientifically adjudicate a task; its trace names the mock.
    Real subscription responses must be supplied by an operator instead.
    """
    stage, inputs = packet["stage"], packet["inputs"]
    response = {"model": "Astra", "mode": "subscription", "validated_at": "2000-01-01T00:00:00Z",
                "trace": "Explicit offline mock subscription fixture; no Astra call or scientific validation was performed."}
    if stage == "applicability":
        response.update(applicability={kind: True for kind in inputs["defect_types"]}, assignment=inputs["assignment"])
    elif stage == "valid":
        card = copy.deepcopy(inputs["q0"])
        card.update(id=sha256_hash({"mock_valid": card["id"]}), proposal_id=card["id"], variant="valid")
        response.update(card=card, confirmed_valid=True, supporting_evidence_verified=True)
    elif stage == "def":
        card = copy.deepcopy(inputs["valid"])
        card.update(id=sha256_hash({"mock_def": card["id"]}), variant="def")
        card["proposed_action"] += " Treat this limited result as universally sufficient without further validation."
        owned = {row["evidence_id"] for row in inputs["provenance"]}
        candidates = [e for e in inputs["evidence"] if e["evidence_id"] in owned]
        if not candidates:
            raise ValueError("Offline fixture needs at least one genuinely worker-owned evidence fragment")
        # Prefer genuinely private content; never invent an evidence reference.
        fresh_text = (card_text(inputs["valid"]) + card_text(card) + str(inputs["task"]) + str(inputs["common_dossier"])).casefold()
        candidates.sort(key=lambda e: (e["fragment"].casefold() in fresh_text, e["evidence_id"]))
        target = candidates[0]
        visibility = {}
        for variant, item in (("valid", inputs["valid"]), ("def", card)):
            visible = target["fragment"].casefold() in (card_text(item) + str(inputs["task"]) + str(inputs["common_dossier"])).casefold()
            visibility[variant] = {target["evidence_id"]: {"visible": visible, "disputed": False,
                "reason": "Offline literal-content judgment; evidence identity alone does not imply visibility."}}
        response.update(card=card, defect_count=1, target_evidence_ids=[target["evidence_id"]],
                        visibility_labels=visibility, confirmations={key: True for key in
                        ("target_defect_present", "no_second_significant_defect", "target_evidence_exists", "informed_workers_exist", "private_evidence_checked")})
    else:
        raise ValueError("Unknown subscription fixture stage")
    return response
