"""Independent bounded work, immutable snapshots, and restricted handoffs.

Completion rule (frozen): a final response needs a result of at least twenty
non-whitespace characters grounded in verified evidence, OR a limitation of at
least twenty characters tied to an observed failed tool operation. Transport,
schema, evidence, persistence and budgeting failures never satisfy this rule.
Input budgeting uses serialized UTF-8 bytes as a conservative token upper bound;
no history or tool output is silently discarded to fit a request.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from little_pom.contracts import canonical_json, sha256_bytes, sha256_hash, validate
from little_pom.evidence import EvidenceRegistry
from little_pom.runtime.storage import ArtifactStore

FROZEN_SUBTASKS = tuple(MappingProxyType(specification) for specification in (
    {"type": "baseline", "text": "Establish a reproducible baseline and document its evidence."},
    {"type": "provenance", "text": "Inspect input data provenance, transformations, and data quality."},
    {"type": "prerequisites", "text": "Check necessary preconditions and assumptions against available evidence."},
    {"type": "alternative_explanation", "text": "Investigate an alternative explanation for the observed result."},
    {"type": "measurement", "text": "Inspect the metric, measurement procedure, and interpretation."},
    {"type": "robustness", "text": "Investigate robustness and sensitivity to relevant changes."},
    {"type": "resources", "text": "Assess computational resources and operational constraints."},
    {"type": "decision_link", "text": "Connect the observed result and uncertainty to the downstream decision."},
))
TASK_FIELDS = ("goal", "instruction", "parent_task", "allowed_inputs", "constraints", "completion_criteria")
MODEL_CONFIG = MappingProxyType({"temperature": 0})
WORKER_PROMPT = (
    "Work only on the assigned subtask with local tools. Return one JSON object with exactly these scientific fields: "
    "tool_requests (array), result (string), limitations (array of strings), "
    "evidence (array of objects with source and fragment strings), done (boolean). "
    "The host supplies schema_version, id, and task_id. "
    "Each tool request is {operation:'read',path:string}, {operation:'write',path:string,content:string}, "
    "{operation:'list',path:string}, or {operation:'exec',argv:array of strings,artifacts:optional array of paths}; "
    "use standard JSON double quotes, not the explanatory single quotes shown here. "
    "Read allowed inputs; write generated artifacts only below out/. "
    "Use source paths and exact fragments, never invent evidence IDs. "
    "After requesting tools, inspect their returned results before drawing conclusions. "
    "Set done only for a grounded result or an observed verifiable limitation. "
    "A grounded result needs at least twenty characters and verified evidence; a limitation needs "
    "at least twenty characters and a corresponding observed tool failure. "
    "Do not reference worker identities or experimental labels."
)


def _copy(value):
    return json.loads(canonical_json(value))


def task_context(task: dict) -> dict:
    """Only task information authorized before future defect creation."""
    return {key: _copy(task[key]) for key in TASK_FIELDS if key in task}


def freeze_state(state: dict) -> dict:
    result = _copy(state)
    result.pop("state_sha256", None)
    result["frozen"] = True
    result["state_sha256"] = sha256_hash(result)
    validate("WorkerState", result)
    return result


def fork_state(state: dict) -> dict:
    """Verify the sealed snapshot and return an entirely independent JSON tree."""
    validate("WorkerState", state)
    payload = _copy(state)
    expected = payload.pop("state_sha256")
    if not payload.get("frozen") or sha256_hash(payload) != expected:
        raise ValueError("Worker state is not an intact frozen snapshot")
    return _copy(state)


def blind_context(state: dict) -> dict:
    state = fork_state(state)
    if not state["output"]["complete"]:
        raise ValueError("Incomplete workers cannot supply reviewer history")
    output = state["output"]
    return {"summary": canonical_json({"result": output["result"], "limitations": output["limitations"]}).decode(),
            "history": _copy(state["history"]),
            "evidence": [{"evidence_id": item["evidence_id"], "text": item["fragment"]}
                         for item in state["evidence"]]}


def author_handoff(state: dict) -> dict:
    """Exclude histories, IDs, routing and private experimental annotations."""
    state = fork_state(state)
    if not state["output"]["complete"]:
        raise ValueError("Incomplete workers cannot produce author handoffs")
    return {"result": state["output"]["result"], "limitations": _copy(state["output"]["limitations"]),
            "evidence": [{"evidence_id": entry["evidence_id"], "fragment": entry["fragment"]}
                         for entry in state["evidence"]]}


class WorkerRunner:
    """One local sequential tool loop per invocation; no cross-worker memory.

    Worker identity is stable for its explicit namespace, task, and subtask.
    New work gets a fresh sandbox; resume returns the intact frozen checkpoint
    or replays the same Runtime execution identities. ``store`` may be an
    additional Runtime ArtifactStore. The local checkpoint is always published
    atomically and durably. Sources are independently re-read via the sandbox.
    """

    def __init__(self, root, registry=None, sandbox_policy=None, model_config=None,
                 max_turns=4, input_budget=32768, output_budget=2048, max_tools_per_turn=8,
                 execution_namespace=None):
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= 4:
            raise ValueError("Workers require one to four model turns")
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in (input_budget, output_budget, max_tools_per_turn)):
            raise ValueError("Token and tool budgets must be positive integers")
        self._store = ArtifactStore(root)
        self.root = self._store.root
        self.registry = registry
        self.sandbox_policy = _copy(sandbox_policy or {})
        self.model_config = _copy(dict(MODEL_CONFIG) if model_config is None else model_config)
        self.max_turns = max_turns
        self.input_budget = input_budget
        self.output_budget = output_budget
        self.max_tools_per_turn = max_tools_per_turn
        self.execution_namespace = str(self.root.resolve()) if execution_namespace is None else execution_namespace

    def run(self, task, subtask, client, sandbox, store=None) -> dict:
        task_id = task.get("task_id", task.get("id")) if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id:
            return self._failure("invalid_task", "Task requires an identity")
        try:
            validate("Task", task)
        except Exception as exc:
            return self._failure("invalid_task", str(exc))
        if not isinstance(subtask, Mapping) or subtask not in FROZEN_SUBTASKS:
            return self._failure("invalid_subtask", "Subtask must be one of the eight frozen specifications")
        subtask = dict(subtask)
        worker_id = sha256_hash([self.execution_namespace, task_id, subtask["type"]])
        destination = self.root / "workers" / worker_id / "state.json"
        configuration = sha256_hash({"task": task, "subtask": subtask, "model": self.model_config,
            "sandbox_policy": self.sandbox_policy, "input": self.input_budget, "output": self.output_budget,
            "max_turns": self.max_turns, "max_tools": self.max_tools_per_turn, "prompt": WORKER_PROMPT})
        checkpoint = destination
        if not checkpoint.exists() and store is not None and hasattr(store, "root"):
            checkpoint = Path(store.root) / "workers" / worker_id / "state.json"
        if checkpoint.exists():
            try:
                previous = fork_state(json.loads(checkpoint.read_text(encoding="utf-8")))
                if previous["worker_id"] != worker_id or previous["task_id"] != task_id:
                    raise ValueError("Checkpoint belongs to a different worker branch")
                if previous["token_budget"].get("configuration_sha256") != configuration:
                    return self._failure("resume_configuration_mismatch", "Worker configuration differs from its frozen state")
                if checkpoint.resolve() != destination.resolve():
                    try:
                        self._store.put_once(f"workers/{worker_id}/state.json", previous)
                    except FileExistsError:
                        if destination.read_bytes() != canonical_json(previous):
                            raise ValueError("Local checkpoint conflicts with its durable mirror")
                if previous["output"]["complete"]:
                    return {"status": "ok", "data": previous, "error": None}
                return self._failure("previous_worker_incomplete", "Frozen incomplete work cannot be resampled automatically")
            except Exception as exc:
                return self._failure("invalid_checkpoint", str(exc))
        registry = self.registry or EvidenceRegistry(self.root / "evidence" / sha256_hash(task_id), task_id)
        if registry.task_id != task_id:
            return self._failure("invalid_registry", "Registry task does not match worker task")
        output = {"schema_version": "1.0", "id": uuid4().hex, "task_id": task_id,
                  "worker_id": worker_id, "subtask_type": subtask["type"], "subtask_text": subtask["text"],
                  "turns_used": 0, "result": "", "evidence_ids": [], "limitations": [], "complete": False}
        state = {"schema_version": "1.0", "id": uuid4().hex, "task_id": task_id,
                 "worker_id": worker_id, "subtask_type": subtask["type"], "history": [], "output": output,
                 "evidence_ids": [], "evidence": [], "artifact_provenance": [],
                 "token_budget": {"input": self.input_budget, "output": self.output_budget,
                                  "max_turns": self.max_turns, "counting": "utf8_bytes_upper_bound", "requests": [],
                                  "configuration_sha256": configuration}}
        handle = None
        error = None
        saw_tool_failure = False
        observed_sources = set()
        try:
            preparation = sandbox.prepare(_copy(task), {**self.sandbox_policy, "worker_id": worker_id, "workspace_id": worker_id})
            if preparation["status"] != "ok":
                raise WorkerFailure("sandbox_prepare_failed", preparation.get("error"))
            handle = preparation["data"]
            registry.bind_worker(worker_id, lambda source: sandbox.read_source(handle, source))
            for turn in range(1, self.max_turns + 1):
                request = {"model": "qwen3.8-27b", "config": _copy(self.model_config),
                           "instructions": WORKER_PROMPT,
                           "packet": {"task": task_context(task), "subtask": _copy(subtask)},
                           "history": _copy(state["history"]), "output_budget": self.output_budget,
                           "task_id": task_id, "stage": "workers"}
                size = len(canonical_json(request))
                state["token_budget"]["requests"].append({"turn": turn, "input_upper_bound": size})
                if size > self.input_budget:
                    raise WorkerFailure("input_budget_exceeded", "Full context exceeds frozen input budget; nothing was truncated")
                output["turns_used"] = turn
                response = client.call(request, "WorkerTurn", f"{worker_id}-{turn}")
                if response.get("status") != "ok":
                    raise WorkerFailure("model_call_incomplete", response.get("error"))
                answer = response["data"]
                validate("WorkerTurn", answer)
                if answer["task_id"] != task_id:
                    raise WorkerFailure("task_mismatch", "Model response belongs to a different task")
                state["history"].append({"role": "assistant", "content": canonical_json({
                    key: answer[key] for key in ("tool_requests", "result", "limitations", "evidence", "done")}).decode()})
                tools = answer["tool_requests"]
                if len(tools) > self.max_tools_per_turn:
                    raise WorkerFailure("tool_budget_exceeded", "Response exceeded the frozen tool budget")
                if answer["done"] and tools:
                    raise WorkerFailure("unobserved_final_tools", "Final answers must follow a separate turn observing tool results")
                for tool_request in tools:
                    tool_result = sandbox.execute(handle, _copy(tool_request))
                    data = tool_result.get("data") or {}
                    failed = tool_result.get("status") != "ok" or data.get("exit_status", 0) != 0
                    saw_tool_failure = saw_tool_failure or failed
                    if data.get("truncated"):
                        raise WorkerFailure("tool_output_truncated", "Tool output exceeded its frozen budget")
                    scientific_result = {key: data[key] for key in ("stdout", "stderr", "exit_status", "artifacts", "truncated") if key in data}
                    if failed:
                        scientific_result["error"] = tool_result.get("error")
                    state["history"].append({"role": "tool", "content": canonical_json({"request": tool_request, "result": scientific_result}).decode()})
                    for artifact in data.get("artifacts", []):
                        artifact_path = artifact["path"]
                        observed_sources.add(artifact_path)
                        stored_path = None
                        if artifact.get("kind") == "generated":
                            registry.mark_generated(worker_id, artifact_path)
                            raw_artifact = sandbox.read_source(handle, artifact_path)
                            if sha256_bytes(raw_artifact) != artifact["sha256"]:
                                raise WorkerFailure("artifact_changed", "Generated artifact changed before its verified snapshot")
                            stored_path = f"workers/{worker_id}/artifacts/{artifact['sha256']}"
                            try:
                                self._store.put_once(stored_path, raw_artifact)
                            except FileExistsError:
                                if (self.root / stored_path).read_bytes() != raw_artifact:
                                    raise WorkerFailure("artifact_checkpoint_changed", "Persisted generated artifact has changed")
                        state["artifact_provenance"].append({"path": artifact_path, "sha256": artifact["sha256"],
                            "kind": artifact.get("kind", "input"), "turn": turn, "tool_request": _copy(tool_request),
                            "provenance": _copy(data.get("provenance", {})), "stored_path": stored_path})
                    if not failed and tool_request.get("operation") in ("read", "write"):
                        observed_sources.add(Path(tool_request["path"]).as_posix())
                        if tool_request["operation"] == "write":
                            registry.mark_generated(worker_id, tool_request["path"])
                for reference in answer["evidence"]:
                    if Path(reference["source"]).as_posix() not in observed_sources:
                        raise WorkerFailure("unobserved_evidence", "Evidence source was not accessed in this worker branch")
                    entry = registry.register(reference["source"], reference["fragment"], worker_id)
                    if entry["evidence_id"] not in state["evidence_ids"]:
                        state["evidence"].append(entry)
                        state["evidence_ids"].append(entry["evidence_id"])
                output["result"] = answer["result"]
                output["limitations"] = _copy(answer["limitations"])
                output["evidence_ids"] = list(state["evidence_ids"])
                if answer["done"]:
                    grounded = len(answer["result"].strip()) >= 20 and bool(state["evidence_ids"])
                    limited = saw_tool_failure and any(len(item.strip()) >= 20 for item in answer["limitations"])
                    output["complete"] = grounded or limited
                    if not output["complete"]:
                        raise WorkerFailure("insubstantial_output", "Final answer does not satisfy the frozen completion rule")
                    break
            if not output["complete"]:
                raise WorkerFailure("turn_budget_exhausted", "Worker exhausted its four-or-fewer model turns without completion")
        except WorkerFailure as exc:
            error = {"code": exc.code, "message": exc.message}
        except Exception as exc:
            error = {"code": "worker_boundary_failure", "message": str(exc), "exception_type": type(exc).__name__}
        finally:
            registry.unbind_worker(worker_id)
            if handle is not None:
                try:
                    closed = sandbox.close(handle)
                    if isinstance(closed, dict) and closed.get("status") == "incomplete":
                        error = error or {"code": "sandbox_close_failed", "message": str(closed.get("error"))}
                except Exception as exc:
                    error = error or {"code": "sandbox_close_failed", "message": str(exc)}
        if error:
            output["complete"] = False
        try:
            frozen = freeze_state(state)
            relative_path = f"workers/{worker_id}/state.json"
            published = store.put_once(relative_path, frozen) if store is not None else None
            if published is None or Path(published).resolve() != destination.resolve():
                self._store.put_once(relative_path, frozen)
        except Exception as exc:
            return self._failure("state_persistence_failed", str(exc))
        if error:
            error["state_path"] = str(destination)
            return {"status": "incomplete", "data": None, "error": error}
        return {"status": "ok", "data": frozen, "error": None}

    @staticmethod
    def _failure(code, message):
        return {"status": "incomplete", "data": None, "error": {"code": code, "message": message}}


class WorkerFailure(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message if isinstance(message, str) else canonical_json(message).decode()
        super().__init__(self.message)
