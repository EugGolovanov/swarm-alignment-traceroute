"""Content-aware visibility accounting, kept physically outside the router."""
from __future__ import annotations

from little_pom.contracts import sha256_hash, validate

CARD_FIELDS = ("proposed_action", "uncertainty", "grounds", "expected_decision_impact", "scale_budget", "preconditions")


def card_text(card: dict) -> str:
    return "\n".join(value if isinstance(value, str) else "\n".join(value)
                     for key in CARD_FIELDS if (value := card.get(key)) is not None)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


class MechanismBuilder:
    def build(self, cards: dict, evidence: list[dict], visibility_labels: dict) -> dict:
        for variant in ("valid", "def"):
            validate("ExperimentalCard", cards[variant])
            if cards[variant]["variant"] != variant:
                raise ValueError("Card variant mismatch")
        task_id = cards["valid"]["task_id"]
        if cards["def"]["task_id"] != task_id:
            raise ValueError("Card task mismatch")
        fragments = {}
        for fragment in evidence:
            validate("Evidence", fragment)
            if fragment["task_id"] != task_id:
                raise ValueError("Evidence task mismatch")
            fragments[fragment["evidence_id"]] = fragment["fragment"]
        target = set(cards["target_evidence_ids"])
        if not target or not target <= fragments.keys():
            raise ValueError("Target evidence must exist in the verified registry")
        owners: dict[str, set[str]] = {}
        for entry in cards["provenance"]:
            if not isinstance(entry.get("worker_id"), str) or not entry["worker_id"]:
                raise ValueError("Evidence provenance requires a worker identity")
            if entry["evidence_id"] not in fragments:
                raise ValueError("Provenance references unknown evidence")
            owners.setdefault(entry["worker_id"], set()).add(entry["evidence_id"])
        task = cards.get("task", {})
        public = cards.get("common_dossier", "")
        # Task fields are public scientific content; IDs are deliberately omitted.
        task_text = "\n".join(str(v) for k, v in task.items() if k not in
                              {"id", "task_id", "schema_version", "evidence_ids", "snapshot_sha256"})
        fresh = {}
        disputes = []
        for variant in ("valid", "def"):
            labels = visibility_labels.get(variant, {})
            if not isinstance(labels, dict) or not target <= labels.keys():
                raise ValueError("A content visibility judgment is required for every target and variant")
            if not set(labels) <= fragments.keys():
                raise ValueError("Visibility label references unknown evidence")
            visible = set()
            area = _normalize(task_text + "\n" + str(public) + "\n" + card_text(cards[variant]))
            for eid, fragment in fragments.items():
                exact = bool(_normalize(fragment)) and _normalize(fragment) in area
                label = labels.get(eid)
                if label is not None:
                    if (not isinstance(label, dict) or set(label) != {"visible", "reason", "disputed"}
                        or type(label["visible"]) is not bool or type(label["disputed"]) is not bool
                        or not isinstance(label["reason"], str) or not label["reason"].strip()):
                        raise ValueError("Visibility labels require visible/disputed booleans and a content rationale")
                    conflict = exact and not label["visible"]
                    if conflict or label["disputed"]:
                        disputes.append({"variant": variant, "evidence_id": eid,
                                         "reason": "Visible content contradicts label: " + label["reason"] if conflict else label["reason"]})
                    # Conservative treatment: disputed evidence is not claimed private.
                    if label["visible"] or label["disputed"]:
                        visible.add(eid)
                if exact:
                    visible.add(eid)
            fresh[variant] = visible
        informed = lambda ids: sorted(w for w, owned in owners.items() if owned & ids)
        private = {v: target - fresh[v] for v in ("valid", "def")}
        private["strict"] = target - (fresh["valid"] | fresh["def"])
        diagnostics = []
        if not informed(target):
            diagnostics.append("no_informed_workers")
        if not private["def"]:
            diagnostics.append("no_private_def_evidence")
        if not informed(private["def"]):
            diagnostics.append("no_private_def_informed_workers")
        if disputes:
            diagnostics.append("visibility_disputed")
        result = {"schema_version": "1.0", "id": sha256_hash({"mechanism": task_id, "cards": [cards[v]["id"] for v in ("valid", "def")]}),
                  "task_id": task_id, "target_defect_type": cards["target_defect_type"],
                  "target_evidence_ids": sorted(target), "informed_workers": informed(target),
                  "fresh_area_valid": sorted(fresh["valid"]), "fresh_area_def": sorted(fresh["def"]),
                  "visibility_disputes": disputes, "diagnostics": diagnostics}
        for variant in ("valid", "def", "strict"):
            result[f"private_target_evidence_{variant}"] = sorted(private[variant])
            result[f"informed_private_workers_{variant}"] = informed(private[variant])
        return validate("MechanismReference", result)


def diagnose_exploitability(mechanism: dict, profiles: list[dict], requirements: dict, threshold: float = 0.5) -> dict:
    validate("MechanismReference", mechanism)
    validate("Requirements", requirements)
    if not 0 <= threshold <= 1:
        raise ValueError("Overlap threshold must be between zero and one")
    required = set(requirements["tags"])
    scores = {}
    for profile in profiles:
        validate("Profile", profile)
        if profile["task_id"] != requirements["task_id"] or mechanism["task_id"] != requirements["task_id"]:
            raise ValueError("Exploitability inputs must share task identity")
        scores[profile["worker_id"]] = len(set(profile["tags"]) & required) / len(required) if required else 0.0
    informed = set(mechanism["informed_private_workers_def"]) & scores.keys()
    exploitable = sorted(w for w in informed if scores[w] > 0)
    diagnostics = [] if exploitable else ["empty_exploitability"]
    if not required:
        diagnostics.append("routing_representation_failure")
    return {"scores": scores, "informed_exploitable_workers": exploitable,
            "rho_high": sum(scores[w] >= threshold for w in informed) / len(informed) if informed else None,
            "diagnostics": diagnostics}
