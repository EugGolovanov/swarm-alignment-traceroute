"""Extract frozen vocabulary tags from factual work, not experimental labels."""
from __future__ import annotations

import re
from typing import Any

from little_pom.contracts import sha256_hash, validate


def extract_tags(fields: dict[str, Any], vocabulary: dict[str, list[str]]) -> dict[str, list[str]]:
    if not isinstance(vocabulary, dict) or any(
        not isinstance(tag, str) or not tag or not isinstance(phrases, list)
        or not phrases or any(not isinstance(p, str) or not p.strip() for p in phrases)
        for tag, phrases in vocabulary.items()
    ):
        raise ValueError("Vocabulary must map nonempty tags to nonempty literal phrase lists")
    sources = {}
    for tag, phrases in sorted(vocabulary.items()):
        matched = []
        for field, value in fields.items():
            texts = value if isinstance(value, list) else [value]
            if any(not isinstance(text, str) for text in texts):
                raise ValueError(f"{field} must contain text")
            if any(re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", text.casefold())
                   for text in texts for phrase in phrases):
                matched.append(field)
        if matched:
            sources[tag] = sorted(matched)
    return sources


class ProfileBuilder:
    """Use only observed output fields; assigned roles cannot produce tags."""

    def build(self, worker_output: dict, vocabulary: dict) -> dict:
        for key in ("task_id", "worker_id", "result", "evidence_ids", "limitations"):
            if key not in worker_output:
                raise ValueError(f"Worker output missing {key}")
        sources = extract_tags({key: worker_output[key] for key in
                                ("result", "evidence_ids", "limitations")}, vocabulary)
        profile = {"schema_version": "1.0", "id": sha256_hash({"profile": worker_output["worker_id"],
                   "task_id": worker_output["task_id"], "sources": sources}),
                   "task_id": worker_output["task_id"], "worker_id": worker_output["worker_id"],
                   "tags": sorted(sources), "tag_sources": sources}
        return validate("Profile", profile)


class RequirementsBuilder:
    """A valid card is the sole source of the pre-injection representation."""

    def build(self, valid: dict, vocabulary: dict) -> dict:
        validate("ExperimentalCard", valid)
        if valid["variant"] != "valid":
            raise ValueError("Requirements must be extracted from valid, before defect injection")
        fields = {key: valid[key] for key in ("proposed_action", "uncertainty", "grounds",
                  "expected_decision_impact", "scale_budget", "preconditions")}
        tags = sorted(extract_tags(fields, vocabulary))
        requirements = {"schema_version": "1.0", "id": sha256_hash({"requirements": valid["id"], "tags": tags}),
                        "task_id": valid["task_id"], "tags": tags, "source": "valid",
                        "card_id": valid["id"], "frozen": True,
                        "diagnostics": [] if tags else ["routing_representation_failure"]}
        return validate("Requirements", requirements)
