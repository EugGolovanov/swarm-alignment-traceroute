"""Fail-closed reviewer boundary: scientific text is never silently sanitized."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from little_pom.contracts import canonical_json, validate


class BlindingError(ValueError):
    """A reviewer input contains forbidden metadata or an unknown projection."""


# Match experimental annotations, not ordinary scientific uses of e.g. "valid".
_METADATA = re.compile(
    r"(?ix)"
    r"\b(?:Fresh|RandomHistory|RoutedHistory)\b|"
    r"\b(?:condition|variant(?:[_ -]label)?|target[_ -](?:defect|evidence)|"
    r"informed(?:[_ -](?:flag|label|workers?))?|routing[_ -](?:score|metadata)|"
    r"panel[_ -](?:id|membership|composition)|reviewer[_ -](?:id|identity|key)|"
    r"worker[_ -]id|experimental[_ -](?:label|condition))[\"']?\s*[=:]|"
    r"\b(?:valid|def)[/_ -](?:variant|card)\b|"
    r"\b(?:condition|variant)\s+(?:is\s+)?(?:valid|def|fresh|randomhistory|routedhistory)\b|"
    r"\b(?:target[_ -](?:defect|evidence)|informed[_ -](?:flag|label|workers?)|"
    r"routing[_ -](?:score|metadata)|panel[_ -](?:membership|composition)|"
    r"other[_ -]reviewer)\b"
)
_FORBIDDEN_KEYS = frozenset({
    "condition", "variant", "variant_label", "target_defect", "target_evidence",
    "target_evidence_ids", "informed", "informed_flags", "informed_workers",
    "routing_scores", "routing_score", "panel_id", "panel_membership",
    "reviewer_id", "reviewer_key", "worker_id", "other_reviewer",
})


def assert_blind(value: Any, *, forbidden_values: tuple[str, ...] = ()) -> None:
    """Reject annotations at any nesting level without altering their contents.

    Additional exact hidden labels can be supplied by the orchestration layer;
    ordinary words such as 'valid' are deliberately not global banned tokens.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_").replace(" ", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise BlindingError(f"Forbidden metadata field: {key}")
            assert_blind(item, forbidden_values=forbidden_values)
    elif isinstance(value, list):
        for item in value:
            assert_blind(item, forbidden_values=forbidden_values)
    elif isinstance(value, str):
        if _METADATA.search(value):
            raise BlindingError("Experimental metadata annotation in scientific context")
        if any(token and token.casefold() in value.casefold() for token in forbidden_values):
            raise BlindingError("Hidden label in scientific context")


def _text_projection(value: dict, name: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"text"}:
        raise BlindingError(f"{name} must be an explicit {{'text': ...}} scientific projection")
    if not isinstance(value["text"], str):
        raise BlindingError(f"{name}.text must be text")
    if name != "dossier" and not value["text"].strip():
        raise BlindingError(f"{name}.text must be nonempty text")
    return copy.deepcopy(value)


class ReviewerPacketBuilder:
    """Build a closed packet from explicit public text and an optional frozen state.

    Internal Task/Card records must be projected by their owners before entry.
    Rejecting extra fields prevents accidental metadata removal from appearing to
    be a safe scientific-content-preserving sanitization.
    """

    def __init__(self, *, packet_id: str | None = None, forbidden_values: tuple[str, ...] = ()):
        self.packet_id = packet_id
        self.forbidden_values = tuple(forbidden_values)

    def build(self, task: dict, dossier: dict, card: dict, worker_state: dict | None = None) -> dict:
        packet = {
            "schema_version": "1.0",
            "id": self.packet_id or uuid.uuid4().hex,
            "task": _text_projection(task, "task"),
            "dossier": _text_projection(dossier, "dossier"),
            "card": _text_projection(card, "card"),
        }
        if worker_state is not None:
            from little_pom.workers import blind_context, fork_state

            before = canonical_json(worker_state)
            fork = fork_state(worker_state)
            packet["worker_context"] = copy.deepcopy(blind_context(fork))
            if canonical_json(worker_state) != before:
                raise BlindingError("Worker snapshot changed while creating a review fork")
        assert_blind(packet, forbidden_values=self.forbidden_values)
        validate("ReviewerPacket", packet)
        return packet
