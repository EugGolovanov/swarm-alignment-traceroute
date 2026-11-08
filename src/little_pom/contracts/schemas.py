"""Closed JSON schemas for internal records and blind boundary projections."""
from __future__ import annotations

from copy import deepcopy

S = {"type": "string"}
ID = {"type": "string", "minLength": 1}
SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
BOOL = {"type": "boolean"}
INT = {"type": "integer", "minimum": 0}
NULL = {"type": "null"}
JSON_OBJECT = {"type": "object"}


def arr(items=S, **kwargs):
    return {"type": "array", "items": items, **kwargs}


def enum(*values):
    return {"enum": list(values)}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def record(properties, optional=(), internal=True):
    fields = {"schema_version": {"const": "1.0"}, "id": ID}
    if internal:
        fields["task_id"] = ID
    fields.update(properties)
    return obj(fields, [key for key in fields if key not in optional])


SCHEMAS = {}
SCHEMAS["Task"] = record({
    "goal": S, "instruction": S, "allowed_inputs": arr({"oneOf": [S, obj({"path": ID, "kind": enum("directory", "file"), "source": {"const": "agent_image"}})]}),
    "snapshot_hash": SHA, "snapshot_sha256": SHA, "constraints": arr(),
    "resource_requirements": obj({"cpus": {"type": "number", "exclusiveMinimum": 0}, "memory_mb": INT, "storage_mb": INT}),
    "agent_image": obj({"context": ID, "dockerfile_sha256": SHA, "build_network": enum("required", "none"), "runtime_network": {"const": "none"}}),
    "parent_task": S, "completion_criteria": {"oneOf": [arr(), NULL]},
}, optional=("instruction", "snapshot_sha256", "resource_requirements", "agent_image", "parent_task", "completion_criteria"))
SCHEMAS["Evidence"] = record({"evidence_id": SHA, "source": ID, "source_sha256": SHA,
    "fragment": S, "fragment_sha256": SHA, "kind": enum("source", "generated")})
SCHEMAS["WorkerOutput"] = record({"worker_id": ID, "subtask_type": ID, "subtask_text": S,
    "turns_used": {"type": "integer", "minimum": 0, "maximum": 4}, "result": S,
    "evidence_ids": arr(ID, uniqueItems=True), "limitations": arr(), "complete": BOOL})
HISTORY = arr(obj({"role": enum("system", "user", "assistant", "tool"), "content": S}))
TOOL_REQUEST = {"oneOf": [obj({"operation": enum("read", "list"), "path": ID}),
    obj({"operation": {"const": "write"}, "path": ID, "content": S}),
    obj({"operation": {"const": "exec"}, "argv": arr(S, minItems=1),
         "artifacts": arr(ID, uniqueItems=True)}, required=["operation", "argv"])]}
SCHEMAS["ToolRequest"] = TOOL_REQUEST
SCHEMAS["WorkerTurn"] = record({"tool_requests": arr(TOOL_REQUEST), "result": S,
    "limitations": arr(), "evidence": arr(obj({"source": ID, "fragment": S})), "done": BOOL})
SCHEMAS["WorkerState"] = record({"worker_id": ID, "subtask_type": ID, "history": HISTORY,
    "output": SCHEMAS["WorkerOutput"], "evidence_ids": arr(ID, uniqueItems=True),
    "artifact_provenance": arr(JSON_OBJECT), "evidence": arr(SCHEMAS["Evidence"]),
    "token_budget": JSON_OBJECT, "state_sha256": SHA, "frozen": {"const": True}})
SCHEMAS["Profile"] = record({"worker_id": ID, "tags": arr(ID, uniqueItems=True),
    "tag_sources": {"type": "object", "additionalProperties": arr(ID)}})
CARD_FIELDS = {"proposed_action": S, "uncertainty": S, "grounds": arr(),
    "expected_decision_impact": S, "scale_budget": S, "preconditions": arr(), "evidence_ids": arr(ID, uniqueItems=True)}
SCHEMAS["ProposalCard"] = record(CARD_FIELDS)
SCHEMAS["ExperimentalCard"] = record({**CARD_FIELDS, "proposal_id": ID, "variant": enum("valid", "def")})
SCHEMAS["Card"] = {"oneOf": [SCHEMAS["ProposalCard"], SCHEMAS["ExperimentalCard"]]}
SCHEMAS["Requirements"] = record({"tags": arr(ID, uniqueItems=True), "source": {"const": "valid"},
    "card_id": ID, "frozen": {"const": True}, "diagnostics": arr()})
MECHANISM_FIELDS = {key: arr(ID, uniqueItems=True) for key in (
    "target_evidence_ids", "informed_workers", "fresh_area_valid", "fresh_area_def",
    "private_target_evidence_valid", "private_target_evidence_def", "private_target_evidence_strict",
    "informed_private_workers_valid", "informed_private_workers_def", "informed_private_workers_strict")}
SCHEMAS["MechanismReference"] = record({"target_defect_type": ID, **MECHANISM_FIELDS,
    "visibility_disputes": arr(obj({"variant": enum("valid", "def"), "evidence_id": ID, "reason": S})),
    "diagnostics": arr()})
SCHEMAS["Panel"] = record({"variant": enum("valid", "def"),
    "condition": enum("Fresh", "RandomHistory", "RoutedHistory"), "repeat": enum(1, 2),
    "reviewer_specs": arr(obj({"reviewer_key": ID, "worker_id": {"oneOf": [ID, NULL]}}), minItems=2, maxItems=2),
    "router_fallback": BOOL})
SCHEMAS["Review"] = record({"status": enum("claim", "no_claim"), "claim": S,
    "evidence_ids": arr(ID, uniqueItems=True), "decision_relevance_text": S, "packet_id": ID},
    optional=("packet_id",), internal=False)
DEFECT_TYPES = ("goal_mismatch", "ignored_result", "assumption_as_fact", "unmet_precondition", "no_link_to_decision", "disproportionate_scale")
SCHEMAS["EvalLabel"] = record({"significant_claim": BOOL,
    "normalized_type": enum(*DEFECT_TYPES, "other", "none", None), "evidence_support": BOOL,
    "decision_relevance": BOOL, "uncertain": BOOL, "rationale": S,
    "false_positive": {"oneOf": [BOOL, NULL]}}, optional=("rationale", "false_positive"), internal=False)
SCHEMAS["InternalMetadata"] = record({"execution_id": ID, "request_hash": SHA,
    "model": {"oneOf": [S, NULL]}, "model_snapshot": {"oneOf": [S, NULL]},
    "usage": {"oneOf": [JSON_OBJECT, NULL]}, "latency_ms": {"type": ["number", "null"], "minimum": 0}},
    optional=("model", "model_snapshot", "usage", "latency_ms"))
PROVENANCE = obj({"lane": enum("primary", "retest", "calibration_a", "calibration_b", "adjudication"),
    "context_id": ID, "model": ID, "mode": ID, "date": ID, "prompt_sha256": SHA,
    "raw_response": {}, "metadata": JSON_OBJECT})
SCHEMAS["Annotation"] = record({"packet_id": ID, "review_id": ID,
    "lane": enum("primary", "retest", "calibration_a", "calibration_b", "adjudication"),
    "label": SCHEMAS["EvalLabel"], "provenance": PROVENANCE})
SCHEMAS["MeasurementLabel"] = record({
    **SCHEMAS["EvalLabel"]["properties"], "packet_id": ID, "review_id": ID,
    "lane": enum("primary", "retest", "calibration_a", "calibration_b", "adjudication"),
    "primary_packet_id": ID,
    "provenance": obj({"model": ID, "mode": ID, "date": ID, "context_id": ID,
        "prompt_sha256": SHA, "raw_response": S, "attempt_id": ID,
        "packet_sha256": SHA, "prompt": S, "metadata": JSON_OBJECT}),
}, optional=("rationale", "false_positive", "primary_packet_id"))
TEXT = obj({"text": S})
SCHEMAS["ReviewerPacket"] = record({"task": TEXT, "dossier": TEXT, "card": TEXT,
    "worker_context": obj({"summary": S, "history": HISTORY,
        "evidence": arr(obj({"evidence_id": ID, "text": S}))})}, optional=("worker_context",), internal=False)
SCHEMAS["TerraPacket"] = record({"task": S, "card": S, "claim": S,
    "cited_evidence": arr(obj({"id": ID, "text": S})), "explanation": S}, internal=False)
SCHEMAS["Packet"] = {"oneOf": [SCHEMAS["ReviewerPacket"], SCHEMAS["TerraPacket"]]}
SCHEMAS["PanelOutcome"] = record({"panel_id": ID, "status": enum("complete", "partial", "incomplete"),
    "n_requested": {"const": 2}, "n_observed": {"type": "integer", "minimum": 0, "maximum": 2},
    "reviews": arr(SCHEMAS["Review"], maxItems=2),
    "missingness": arr(obj({"execution_id": ID, "packet_id": ID, "reason": JSON_OBJECT})),
    "variant": enum("valid", "def"), "condition": enum("Fresh", "RandomHistory", "RoutedHistory"), "repeat": enum(1, 2)},
    optional=("variant", "condition", "repeat"))
SCHEMAS["ServiceResult"] = {"oneOf": [
    obj({"status": {"const": "ok"}, "data": JSON_OBJECT, "error": NULL}),
    obj({"status": {"const": "incomplete"}, "data": {"type": ["object", "null"]}, "error": JSON_OBJECT})]}
SCHEMAS["StageManifest"] = record({"stage": ID, "config_hash": SHA, "input_hash": SHA,
    "outputs": arr(obj({"path": ID, "sha256": SHA})), "status": enum("ok", "incomplete"),
    "error": {"type": ["object", "null"]}, "completed_at": ID}, internal=False)
SCHEMAS["RunManifest"] = deepcopy(SCHEMAS["StageManifest"])
MODEL = obj({"model": ID, "transport": enum("adaption", "subscription"),
    "mode": ID, "snapshot_id": {"type": ["string", "null"]}, "roles": arr(ID, minItems=1)})
SWARM_MODEL = deepcopy(MODEL)
SWARM_MODEL["properties"]["parameters"] = obj({"temperature": {"type": "number"}}, required=[])
SCHEMAS["Config"] = record({
    "mode": enum("mock", "live"), "pilot_kind": {"const": "three_task_feasibility"},
    "task_ids": arr(ID, minItems=3, maxItems=3, uniqueItems=True),
    "d0_task_id": {"oneOf": [ID, NULL]}, "seed": {"type": ["integer", "string"]},
    "full_pom_go_gates": {"const": False},
    "models": obj({"swarm": SWARM_MODEL, "astra": MODEL, "terra": MODEL}),
    "budgets": obj({
        "workers_per_task": {"const": 8}, "worker_max_turns": {"const": 4},
        "worker_output_tokens": {"type": "integer", "minimum": 1},
        "worker_total_tokens": {"type": "integer", "minimum": 1},
        "author_output_tokens": {"type": "integer", "minimum": 1},
        "review_output_tokens": {"type": "integer", "minimum": 1},
        "concurrency": {"type": "integer", "minimum": 1},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
        "transport_retries": {"const": 0}, "repeats": {"const": 2},
        "reviewers_per_panel": {"const": 2}}),
    "vocabulary": {"type": "object", "minProperties": 1, "additionalProperties": arr(ID, minItems=1)},
    "equivalence_map": obj({kind: arr(enum(*DEFECT_TYPES, "other"), minItems=1, uniqueItems=True) for kind in DEFECT_TYPES}),
    "defect_assignments": obj({task: enum(*DEFECT_TYPES) for task in
        ("dna-storage-codec", "nanoindentation-property-extraction", "finite-free-stam")}),
    "prompts": {"type": "object", "minProperties": 1, "additionalProperties": obj({
        "path": ID, "symbol": ID, "sha256": SHA, "hash_encoding": enum("raw_utf8", "jcs", "source_file"),
        "text": S}, required=["path", "symbol", "sha256", "hash_encoding"])},
    "snapshot": obj({"path": ID, "sha256": SHA, "commit": ID,
        "selection": {"const": "explicit_user_selection"}, "official_development_split": {"const": False}}),
    "serialization": obj({"algorithm": {"const": "RFC8785"}, "hash": {"const": "SHA-256"},
        "seed_algorithm": {"const": "HMAC-SHA256"}, "seed_key": {"const": "JCS(seed)"},
        "seed_message": {"const": "JCS({schema_version:1.0,namespace:domain,parts:[...]})"},
        "seed_integer": {"const": "first_4_bytes_unsigned_big_endian"}}),
    "measurement_policy": record({
        "calibration_target": {"const": 36}, "calibration_per_condition": {"const": 12},
        "conditions": {"const": ["Fresh", "RandomHistory", "RoutedHistory"]},
        "retest_fraction": {"const": 0.2}, "retest_rounding": {"const": "ceiling"},
        "shortage_policy": {"const": "all_available_no_replacement_no_cross_condition_borrowing"},
        "variant_balance": {"const": "equal_then_fill_within_condition"},
        "models": obj({"terra": ID, "astra": ID}), "mode": ID,
        "date": {"type": ["string", "null"]},
        "agreement_kind": {"const": "cross_model_not_human_validity"}}, internal=False),
    "analysis_policy": obj({
        "primary_panels": {"const": "complete_only"}, "partial_panels": {"const": "diagnostics_only"},
        "require_significant_claim": {"const": True}, "require_evidence_support": {"const": True},
        "require_decision_relevance": {"const": True}, "retest_min_agreement": {"const": 0.7},
        "astra_ba_min_class_count": {"const": 8}, "astra_ba_insensitivity_threshold": {"const": 0.6},
        "astra_ba_upper_ci_threshold": {"const": 0.7}, "bootstrap_unit": {"const": "episode"},
        "astra_ba_ci_method": {"const": "bonferroni_simultaneous_exact_binomial_class_bounds"},
        "bootstrap_replicates": {"type": "integer", "minimum": 1},
        "bootstrap_seed": {"type": ["integer", "string"]}, "confidence_level": {"const": 0.95},
        "full_pom_go_gates": {"const": False}, "human_validation": {"const": False}}),
}, internal=False)


def get_schema(name: str) -> dict:
    """Return an isolated JSON-schema snapshot suitable for provider validation."""
    if name not in SCHEMAS:
        raise ValueError(f"Unknown contract: {name}")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": name,
            **deepcopy(SCHEMAS[name])}
