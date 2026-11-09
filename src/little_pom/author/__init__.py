"""Proposal author restricted to pre-defect task information and handoffs."""
from __future__ import annotations

import json
from uuid import uuid4

from little_pom.contracts import canonical_json, sha256_hash, validate
from little_pom.evidence import EvidenceReferenceError, EvidenceScope
from little_pom.workers import MODEL_CONFIG, _copy, task_context
from little_pom.runtime.storage import ArtifactStore

AUTHOR_PROMPT = (
    "Create the initial proposal q0 from this task and these authorized worker handoffs. "
    "Return one JSON object with proposed_action (string), uncertainty (string), grounds (array of strings), "
    "expected_decision_impact (string), scale_budget (string), preconditions (array of strings), "
    "and evidence_ids (array of strings). The host supplies schema_version, id, and task_id. "
    "Do not create, infer, or mention a future experimental defect. "
    "Cite only supplied short evidence IDs ([[ev:1]], [[ev:2]], ...), never hashes. State uncertainty and decision relevance."
)


class AuthorRunner:
    """One configured author call with no worker history or experimental annotations.

    Handoffs are the explicit {result, limitations, evidence} projection returned
    by workers.author_handoff. Additional fields are rejected rather than silently
    exposing hidden labels. Proposal evidence IDs must be in those handoffs.
    """

    def __init__(self, root, model_config=None, input_budget=32768, output_budget=2048, execution_namespace=None,
                 *, model="qwen3.5-397b"):
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in (input_budget, output_budget)):
            raise ValueError("Author budgets must be positive integers")
        self.store = ArtifactStore(root)
        self.root = self.store.root
        self.model = model
        self.model_config = _copy(dict(MODEL_CONFIG) if model_config is None else model_config)
        self.input_budget = input_budget
        self.output_budget = output_budget
        self.execution_namespace = str(self.root.resolve()) if execution_namespace is None else execution_namespace

    @staticmethod
    def _scope(handoffs):
        return EvidenceScope([item for handoff in handoffs for item in handoff["evidence"]])

    def _request(self, task, handoffs):
        return {"model": self.model, "config": _copy(self.model_config),
                "instructions": AUTHOR_PROMPT,
                "packet": {"task": task_context(task), "handoffs": self._scope(handoffs).project(handoffs)}, "history": [],
                "output_budget": self.output_budget, "task_id": task["task_id"], "stage": "author"}

    def inspect(self, task, handoffs, *, cited_evidence_ids=None) -> dict:
        """Inspect the exact host request, not provider tokens or wire-envelope bytes.

        Citation membership is checked for the explicit proposal evidence_ids when
        supplied. Before generation, only evidence availability can be inspected.
        This method never calls a model, writes a report, or changes the inputs.
        """
        validate("Task", task)
        if not isinstance(handoffs, list) or not handoffs:
            raise ValueError("Author requires at least one complete worker handoff")
        allowed_ids = set()
        contributions = []
        for index, handoff in enumerate(handoffs):
            if not isinstance(handoff, dict) or set(handoff) != {"result", "limitations", "evidence"}:
                raise ValueError(f"Handoff {index} contains unapproved fields")
            if (not isinstance(handoff["result"], str) or not isinstance(handoff["limitations"], list)
                    or not all(isinstance(value, str) for value in handoff["limitations"])):
                raise ValueError(f"Handoff {index} result and limitations must be text")
            if not isinstance(handoff["evidence"], list):
                raise ValueError(f"Handoff {index} evidence must be a list")
            for item in handoff["evidence"]:
                if (not isinstance(item, dict) or set(item) != {"evidence_id", "fragment"}
                        or not all(isinstance(value, str) and value for value in item.values())):
                    raise ValueError(f"Handoff {index} evidence must contain only a verified ID and fragment")
                allowed_ids.add(item["evidence_id"])
            contributions.append({"handoff_index": index,
                "serialized_bytes": len(canonical_json(handoff)),
                "result_bytes": len(canonical_json(handoff["result"])),
                "limitations_bytes": len(canonical_json(handoff["limitations"])),
                "evidence_bytes": len(canonical_json(handoff["evidence"])),
                "evidence_reference_count": len(handoff["evidence"]),
                "evidence_ids": [item["evidence_id"] for item in handoff["evidence"]]})
        if cited_evidence_ids is not None and (
                not isinstance(cited_evidence_ids, list)
                or not all(isinstance(value, str) for value in cited_evidence_ids)):
            raise ValueError("Cited evidence IDs must be an array of strings")
        cited = sorted(set(cited_evidence_ids or []))
        missing = sorted(set(cited) - allowed_ids)
        request = self._request(task, handoffs)
        size = len(canonical_json(request))
        issues = []
        if size > self.input_budget:
            issues.append({"code": "input_budget_exceeded",
                "message": f"Exact request exceeds author input budget by {size - self.input_budget} UTF-8 bytes.",
                "action": "Use a new explicitly budgeted run or change worker allocations before sampling; nothing is truncated."})
        if missing:
            issues.append({"code": "unauthorized_evidence",
                "message": "Proposal cites IDs absent from the supplied handoffs.",
                "action": "Reject this proposal; do not repair citations or resample a frozen run."})
        return {"schema_version": "1.0", "kind": "author_exact_preflight", "task_id": task["task_id"],
                "counting": "canonical_json_utf8_bytes", "measurement": "exact_host_request",
                "request_sha256": sha256_hash(request), "serialized_request_bytes": size,
                "input_budget_bytes": self.input_budget, "remaining_bytes": self.input_budget - size,
                "within_budget": size <= self.input_budget, "handoff_count": len(handoffs),
                "evidence_reference_count": sum(item["evidence_reference_count"] for item in contributions),
                "unique_evidence_count": len(allowed_ids), "contributions": contributions,
                "request_overhead_bytes": size - sum(item["serialized_bytes"] for item in contributions),
                "citations": {"checked": cited_evidence_ids is not None, "cited_evidence_ids": cited,
                             "available_evidence_ids": sorted(allowed_ids), "missing_evidence_ids": missing},
                "accepted": not issues, "issues": issues}

    def plan(self, task, *, worker_count, worker_output_tokens, worker_max_turns,
             worker_handoff_bytes=None) -> dict:
        """Detect allocation risk before sampling without inventing a token/byte bound.

        Empty handoffs give a structural lower bound. Provider output tokens do
        not bound serialized handoff bytes, and evidence can accumulate across
        turns. The allocation comparison is a warning, not an exact fit verdict.
        An optional per-handoff serialized byte cap gives a conditional upper
        bound: the caller must enforce that cap without truncation or repair.
        """
        validate("Task", task)
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in (worker_count, worker_output_tokens, worker_max_turns)):
            raise ValueError("Worker planning allocations must be positive integers")
        empty_size = len(canonical_json({"result": "", "limitations": [], "evidence": []}))
        if worker_handoff_bytes is not None and (
                not isinstance(worker_handoff_bytes, int) or isinstance(worker_handoff_bytes, bool)
                or worker_handoff_bytes < empty_size):
            raise ValueError("Handoff byte cap must accommodate a serialized empty handoff")
        empty = [{"result": "", "limitations": [], "evidence": []} for _ in range(worker_count)]
        minimum = len(canonical_json(self._request(task, empty)))
        maximum = (None if worker_handoff_bytes is None else
                   minimum + worker_count * (worker_handoff_bytes - empty_size))
        remaining = self.input_budget - minimum
        final_allocation = worker_count * worker_output_tokens
        risk = final_allocation > remaining
        return {"schema_version": "1.0", "kind": "author_budget_plan", "task_id": task["task_id"],
                "measurement": "pre_worker_bound_and_allocation", "input_budget_bytes": self.input_budget,
                "minimum_request_bytes": minimum, "maximum_request_bytes": maximum,
                "exact_request_bytes": None, "worker_count": worker_count,
                "worker_output_tokens": worker_output_tokens, "worker_max_turns": worker_max_turns,
                "worker_handoff_bytes": worker_handoff_bytes,
                "upper_bound_condition": ("Caller enforces each canonical serialized handoff byte cap."
                                          if maximum is not None else None),
                "worker_final_output_allocation_tokens": final_allocation,
                "worker_total_output_allocation_tokens": final_allocation * worker_max_turns,
                "remaining_handoff_bytes": remaining, "allocation_risk": risk,
                "accepted": minimum <= self.input_budget and (maximum is None or maximum <= self.input_budget),
                "warnings": (["Worker final-output token allocation exceeds remaining author byte capacity. "
                    "This is allocation risk, not a prediction of realized handoff size."] if risk else []),
                "limitations": ["No finite serialized upper bound follows from output tokens alone.",
                    "Accumulated evidence and JSON escaping add bytes; exact inspection is required after workers."],
                "action": "Review allocations before worker calls; do not truncate or repair completed handoffs."}

    def save_report(self, report) -> str:
        """Append a content-addressed report; repeated inspection is idempotent."""
        relative = f"author/preflight/{sha256_hash(report)}.json"
        try:
            self.store.put_once(relative, report)
        except FileExistsError:
            if self.store.read(relative) != report:
                raise ValueError("Author preflight report has changed")
        return relative

    def run(self, task, handoffs, client) -> dict:
        try:
            report = self.inspect(task, handoffs)
            report_path = self.save_report(report)
            if not report["accepted"]:
                return self._failure("input_budget_exceeded", report["issues"][0]["message"],
                                     report_path=report_path)
            task_id = task["task_id"]
            execution_id = sha256_hash([self.execution_namespace, task_id, "author"])
            request = self._request(task, handoffs)
            size = report["serialized_request_bytes"]
            destination = self.root / "author" / f"{execution_id}.json"
            budget_path = destination.with_suffix(".budget.json")
            if destination.exists():
                saved_budget = json.loads(budget_path.read_text(encoding="utf-8"))
                if saved_budget["request_sha256"] != sha256_hash(request):
                    raise ValueError("Author resume context differs from the frozen request")
                proposal = json.loads(destination.read_text(encoding="utf-8"))
                validate("ProposalCard", proposal)
                if sha256_hash(proposal) != saved_budget["proposal_sha256"]:
                    raise ValueError("Author checkpoint has changed")
                citations = self.inspect(task, handoffs, cited_evidence_ids=proposal["evidence_ids"])
                if not citations["accepted"]:
                    return self._failure("unauthorized_evidence", "Saved proposal cites unavailable evidence",
                                         report_path=self.save_report(citations))
                return {"status": "ok", "data": proposal, "error": None}
            scope = self._scope(handoffs)
            binding_path = f"author/evidence/{execution_id}.json"
            try:
                self.store.put_once(binding_path, scope.manifest())
            except FileExistsError:
                if self.store.read(binding_path) != scope.manifest():
                    raise EvidenceReferenceError("Author evidence scope changed")
            response = client.call(request, "ProposalCard", execution_id)
            if response.get("status") != "ok":
                return self._failure("author_call_incomplete", response.get("error"))
            proposal = scope.decode(response["data"])
            validate("ProposalCard", proposal)
            if proposal["task_id"] != task_id:
                raise ValueError("Proposal belongs to a different task")
            citations = self.inspect(task, handoffs, cited_evidence_ids=proposal["evidence_ids"])
            citation_path = self.save_report(citations)
            if not citations["accepted"]:
                return self._failure("unauthorized_evidence", "Proposal cites evidence outside authorized handoffs",
                                     report_path=citation_path)
            if not proposal["proposed_action"].strip() or not proposal["grounds"]:
                raise ValueError("Proposal must state an action and its grounds")
            # Trusted runner identity prevents a model choosing persistence paths.
            proposal["id"] = execution_id
            budget = {"schema_version": "1.0", "id": uuid4().hex, "task_id": task_id,
                      "execution_id": execution_id, "input_budget": self.input_budget, "output_budget": self.output_budget,
                      "input_upper_bound": size, "serialized_request_bytes": size,
                      "counting": "utf8_bytes_upper_bound", "model_calls": 1,
                      "request_sha256": sha256_hash(request), "proposal_sha256": sha256_hash(proposal)}
            if budget_path.exists():
                existing = json.loads(budget_path.read_text(encoding="utf-8"))
                if any(existing[key] != budget[key] for key in ("request_sha256", "proposal_sha256")):
                    raise ValueError("Author budget checkpoint differs from recovered output")
            else:
                self.store.put_once(budget_path.relative_to(self.root), budget)
            self.store.put_once(destination.relative_to(self.root), proposal)
            return {"status": "ok", "data": proposal, "error": None}
        except EvidenceReferenceError as exc:
            report = {"schema_version": "1.0", "kind": "author_reference_rejection",
                      "accepted": False, "code": "evidence_reference_contract", "message": str(exc)}
            return self._failure("evidence_reference_contract", str(exc), report_path=self.save_report(report))
        except Exception as exc:
            report = {"schema_version": "1.0", "kind": "author_preflight_rejection",
                      "accepted": False, "code": "author_boundary_failure", "message": str(exc),
                      "action": "Inspect the supplied task, handoffs and frozen checkpoints; nothing was repaired."}
            try:
                report_path = self.save_report(report)
            except Exception as persistence_error:
                return self._failure("author_boundary_failure", str(exc),
                                     report_save_error=str(persistence_error))
            return self._failure("author_boundary_failure", str(exc), report_path=report_path)

    @staticmethod
    def _failure(code, message, **details):
        return {"status": "incomplete", "data": None, "error": {"code": code, "message": message, **details}}
