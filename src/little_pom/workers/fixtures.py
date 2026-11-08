"""Explicit synthetic fixtures composed through the production worker services.

These fixtures never call a hosted model or Docker. Shared evidence is a real
verified input; private evidence is eight distinct generated observations whose
provenance is explicitly marked generated, not scientific ground truth.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from little_pom.contracts import sha256_bytes
from little_pom.evidence import EvidenceRegistry
from little_pom.workers import FROZEN_SUBTASKS, WorkerRunner


def worker_responses(task_id, source, fragment, result):
    """Two real loop turns: request a file read, then cite its exact fragment."""
    def response(**fields):
        return {"schema_version": "1.0", "id": uuid4().hex, "task_id": task_id,
                "tool_requests": [], "result": "", "limitations": [], "evidence": [], "done": False, **fields}
    return [response(tool_requests=[{"operation": "read", "path": source}]),
            response(result=result, evidence=[{"source": source, "fragment": fragment}], done=True)]


def eight_state_fixture(root, task_id="fixture-task"):
    """Return eight actual frozen states plus canonical shared/private evidence.

    ``root`` is explicit and must belong to the fixture invocation. Consumers may
    independently fork returned states for valid/def/repeat reviews.
    """
    from little_pom.runtime import ArtifactStore, Ledger, MockBackend, SwarmClient
    from little_pom.sandbox import Sandbox

    root = Path(root)
    shared = "The shared baseline contains ten verified observations.\n"
    task = {"schema_version": "1.0", "id": "fixture-task-record", "task_id": task_id,
            "goal": "Assess a synthetic baseline and its decision implications.",
            "allowed_inputs": ["input/baseline.txt"], "constraints": ["Synthetic offline fixture only."],
            "snapshot_hash": sha256_bytes(shared.encode())}
    registry = EvidenceRegistry(root / "evidence", task_id)
    runner = WorkerRunner(root, registry=registry)
    store = ArtifactStore(root)
    states = []
    private_ids = {}
    shared_ids = set()
    for index, subtask in enumerate(FROZEN_SUBTASKS):
        private = f"Observation {index + 1}: {subtask['type']} analysis has a distinct constraint for decision use.\n"
        responses = worker_responses(task_id, "input/baseline.txt", shared.strip(),
                                     f"The {subtask['type']} result uses the shared baseline and its distinct generated observation.")
        write_turn = {"schema_version": "1.0", "id": uuid4().hex, "task_id": task_id,
                      "tool_requests": [{"operation": "write", "path": "out/observation.txt", "content": private}],
                      "result": "", "limitations": [], "evidence": [], "done": False}
        responses.insert(1, write_turn)
        responses[-1]["evidence"].append({"source": "out/observation.txt", "fragment": private.strip()})
        client = SwarmClient(ledger=Ledger(root / "ledger"), backend=MockBackend(responses=responses))
        sandbox = Sandbox(backend="fake", fake_inputs={"input/baseline.txt": shared})
        result = runner.run(task, subtask, client, sandbox, store)
        if result["status"] != "ok":
            raise RuntimeError(f"Synthetic worker failed: {result['error']}")
        state = result["data"]
        states.append(state)
        private_ids[state["worker_id"]] = [entry["evidence_id"] for entry in state["evidence"] if entry["kind"] == "generated"]
        shared_ids.update(entry["evidence_id"] for entry in state["evidence"] if entry["kind"] == "source")
    return {"task": task, "states": states, "evidence": registry.evidence(), "provenance": registry.provenance(),
            "shared_evidence_ids": sorted(shared_ids), "private_evidence_ids": private_ids}
