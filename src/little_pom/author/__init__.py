"""Proposal author restricted to pre-defect task information and handoffs."""
from __future__ import annotations

import json
from uuid import uuid4

from little_pom.contracts import canonical_json, sha256_hash, validate
from little_pom.workers import MODEL_CONFIG, _copy, task_context
from little_pom.runtime.storage import ArtifactStore

AUTHOR_PROMPT = (
    "Create the initial proposal q0 from this task and these authorized worker handoffs. "
    "Return one JSON object with proposed_action (string), uncertainty (string), grounds (array of strings), "
    "expected_decision_impact (string), scale_budget (string), preconditions (array of strings), "
    "and evidence_ids (array of strings). The host supplies schema_version, id, and task_id. "
    "Do not create, infer, or mention a future experimental defect. "
    "Cite only supplied evidence IDs. State uncertainty and decision relevance."
)


class AuthorRunner:
    """One Qwen call with no worker history or experimental annotations.

    Handoffs are the explicit {result, limitations, evidence} projection returned
    by workers.author_handoff. Additional fields are rejected rather than silently
    exposing hidden labels. Proposal evidence IDs must be in those handoffs.
    """

    def __init__(self, root, model_config=None, input_budget=32768, output_budget=2048, execution_namespace=None):
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in (input_budget, output_budget)):
            raise ValueError("Author budgets must be positive integers")
        self.store = ArtifactStore(root)
        self.root = self.store.root
        self.model_config = _copy(dict(MODEL_CONFIG) if model_config is None else model_config)
        self.input_budget = input_budget
        self.output_budget = output_budget
        self.execution_namespace = str(self.root.resolve()) if execution_namespace is None else execution_namespace

    def run(self, task, handoffs, client) -> dict:
        try:
            validate("Task", task)
            task_id = task["task_id"]
            execution_id = sha256_hash([self.execution_namespace, task_id, "author"])
            if not isinstance(handoffs, list) or not handoffs:
                raise ValueError("Author requires at least one complete worker handoff")
            allowed_ids = set()
            clean = []
            for handoff in handoffs:
                if not isinstance(handoff, dict) or set(handoff) != {"result", "limitations", "evidence"}:
                    raise ValueError("Handoff contains unapproved fields")
                if not isinstance(handoff["result"], str) or not isinstance(handoff["limitations"], list) or not all(isinstance(value, str) for value in handoff["limitations"]):
                    raise ValueError("Handoff result and limitations must be text")
                if not isinstance(handoff["evidence"], list):
                    raise ValueError("Handoff evidence must be a list")
                for item in handoff["evidence"]:
                    if not isinstance(item, dict) or set(item) != {"evidence_id", "fragment"} or not all(isinstance(value, str) and value for value in item.values()):
                        raise ValueError("Handoff evidence must contain only a verified ID and fragment")
                    allowed_ids.add(item["evidence_id"])
                clean.append(_copy(handoff))
            request = {"model": "qwen3.8-27b", "config": _copy(self.model_config),
                       "instructions": AUTHOR_PROMPT,
                       "packet": {"task": task_context(task), "handoffs": clean}, "history": [],
                       "output_budget": self.output_budget, "task_id": task_id, "stage": "author"}
            size = len(canonical_json(request))
            if size > self.input_budget:
                return self._failure("input_budget_exceeded", "Full author context exceeds frozen budget; nothing was truncated")
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
                return {"status": "ok", "data": proposal, "error": None}
            response = client.call(request, "ProposalCard", execution_id)
            if response.get("status") != "ok":
                return self._failure("author_call_incomplete", response.get("error"))
            proposal = _copy(response["data"])
            validate("ProposalCard", proposal)
            if proposal["task_id"] != task_id:
                raise ValueError("Proposal belongs to a different task")
            if not set(proposal["evidence_ids"]).issubset(allowed_ids):
                raise ValueError("Proposal cites evidence outside authorized handoffs")
            if not proposal["proposed_action"].strip() or not proposal["grounds"]:
                raise ValueError("Proposal must state an action and its grounds")
            # Trusted runner identity prevents a model choosing persistence paths.
            proposal["id"] = execution_id
            budget = {"schema_version": "1.0", "id": uuid4().hex, "task_id": task_id,
                      "execution_id": execution_id, "input_budget": self.input_budget, "output_budget": self.output_budget,
                      "input_upper_bound": size, "counting": "utf8_bytes_upper_bound", "model_calls": 1,
                      "request_sha256": sha256_hash(request), "proposal_sha256": sha256_hash(proposal)}
            if budget_path.exists():
                existing = json.loads(budget_path.read_text(encoding="utf-8"))
                if any(existing[key] != budget[key] for key in ("request_sha256", "proposal_sha256")):
                    raise ValueError("Author budget checkpoint differs from recovered output")
            else:
                self.store.put_once(budget_path.relative_to(self.root), budget)
            self.store.put_once(destination.relative_to(self.root), proposal)
            return {"status": "ok", "data": proposal, "error": None}
        except Exception as exc:
            return self._failure("author_boundary_failure", str(exc))

    @staticmethod
    def _failure(code, message):
        return {"status": "incomplete", "data": None, "error": {"code": code, "message": message}}
