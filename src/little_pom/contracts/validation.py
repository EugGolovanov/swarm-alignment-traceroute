"""Validate JSON-compatible service boundaries without mutating input."""
from __future__ import annotations

import re

from jsonschema import Draft202012Validator

from .hashing import canonical_json, evidence_id, reject_secrets, sha256_bytes
from .schemas import get_schema


class ContractError(ValueError):
    """A boundary violates its declared schema or semantic invariants."""


_BLIND_MARKERS = re.compile(
    r"(?:\b(?:condition|variant|target_defect(?:_type)?|target_evidence_ids|informed_workers|"
    r"routing_scores?|panel_membership|worker_id|reviewer_id)\s*[=:]|"
    r"\b(?:RandomHistory|RoutedHistory)\b)", re.I)
_OPAQUE_MARKERS = re.compile(r"(?:^|[^a-z0-9])(?:valid|def|fresh|randomhistory|routedhistory|condition|variant)(?:$|[^a-z0-9])", re.I)


def validate_blind_text(value) -> None:
    """Reject explicit metadata markers, rather than deleting scientific text."""
    if isinstance(value, dict):
        for child in value.values():
            validate_blind_text(child)
    elif isinstance(value, list):
        for child in value:
            validate_blind_text(child)
    elif isinstance(value, str) and _BLIND_MARKERS.search(value):
        raise ContractError("Blind content contains an experimental metadata marker")


def validate(name: str, value: dict) -> dict:
    """Validate a named boundary and return the unchanged caller-owned dictionary."""
    try:
        canonical_json(value)
        reject_secrets(value)
        errors = sorted(Draft202012Validator(get_schema(name)).iter_errors(value),
                        key=lambda error: tuple(str(part) for part in error.path))
        if errors:
            error = errors[0]
            location = "/".join(str(part) for part in error.path) or "<root>"
            # Do not echo values: failed inputs may contain sensitive content.
            raise ContractError(f"{name} at {location}: violates {error.validator}")
        if name in ("Packet", "ReviewerPacket", "TerraPacket"):
            if _OPAQUE_MARKERS.search(value["id"]):
                raise ContractError("Packet ID must not encode condition or variant")
            validate_blind_text(value)
        if name == "Evidence":
            if sha256_bytes(value["fragment"].encode("utf-8")) != value["fragment_sha256"]:
                raise ContractError("Evidence fragment hash mismatch")
            if evidence_id(value["source_sha256"], value["fragment_sha256"]) != value["evidence_id"]:
                raise ContractError("Evidence identity mismatch")
        if name == "Panel":
            workers = [spec["worker_id"] for spec in value["reviewer_specs"]]
            keys = [spec["reviewer_key"] for spec in value["reviewer_specs"]]
            if len(set(keys)) != 2:
                raise ContractError("Reviewer keys must be distinct")
            if value["condition"] == "Fresh":
                if workers != [None, None]:
                    raise ContractError("Fresh panel must not carry worker state")
            elif None in workers or len(set(workers)) != 2:
                raise ContractError("History panels require two distinct workers")
        if name == "PanelOutcome":
            count = value["n_observed"]
            if len(value["reviews"]) != count or len(value["missingness"]) != 2 - count:
                raise ContractError("Panel outcome counts disagree")
            if value["status"] != ("incomplete", "partial", "complete")[count]:
                raise ContractError("Panel outcome status disagrees with observations")
        if name == "WorkerState":
            output = value["output"]
            if output["worker_id"] != value["worker_id"] or output["task_id"] != value["task_id"]:
                raise ContractError("Worker state and output identity disagree")
            for item in value["evidence"]:
                validate("Evidence", item)
        return value
    except ContractError:
        raise
    except (ValueError, TypeError, OverflowError) as exc:
        raise ContractError(f"{name}: invalid JSON-compatible boundary") from exc
