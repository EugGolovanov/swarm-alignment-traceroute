"""Single-dispatch, fresh-context card and blinded measurement subscription exchange.

The caller supplies an exact-model SwarmClient. Requests, import intent, and final
outcomes are immutable under root; provider intent/raw response remain in the
client ledger. A crash with uncertain remote or import completion never resamples.
"""
from __future__ import annotations

import json
from pathlib import Path

from little_pom.cards import ASTRA_MODEL, CardWorkflow
from little_pom.contracts import canonical_json, sha256_hash, validate
from little_pom.measurement import EvaluationImporter
from .client import SwarmClient
from .storage import Ledger

SOL_MODEL = "openai-codex/gpt-6.1-sol"
_METADATA = ("model", "mode", "date", "context_id", "prompt_sha256")


def _incomplete(code: str, message: str) -> dict:
    return {"status": "incomplete", "data": None,
            "error": {"code": code, "message": message}}


def _load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _packet_id(value: object) -> str:
    if (not isinstance(value, str) or len(value) not in (32, 64)
            or any(c not in "0123456789abcdef" for c in value)):
        raise ValueError("Exported packet must have an opaque hexadecimal identity")
    return value


class SubscriptionExchange:
    """Resume by exported identity, never by a newly allocated attempt ID.

    dispatch_card(workflow, packet, output_budget=8192) returns the CardWorkflow
    import result. dispatch_evaluation(measurement_root, request,
    output_budget=2048) returns the EvaluationImporter result. Both use medium
    reasoning, empty history, and one call through the supplied client. Use one
    exact-model client per selector; exchange root may be shared by those clients.
    """

    def __init__(self, root: str | Path, client: SwarmClient):
        self.records = Ledger(root)
        self.root = self.records.root
        self.client = client

    @staticmethod
    def execution_id(kind: str, packet_id: str) -> str:
        if kind not in ("card", "evaluation"):
            raise ValueError("Unknown exchange kind")
        return f"subscription-exchange:{kind}:{_packet_id(packet_id)}"

    def dispatch_card(self, workflow: CardWorkflow, packet: dict, *,
                      output_budget: int = 8192) -> dict:
        packet = json.loads(canonical_json(packet))
        pid = _packet_id(packet["id"])
        saved = _load(workflow.root / "cards" / "packets" / f"{pid}.json")
        if packet != saved:
            raise ValueError("Card packet differs from its immutable export")
        if packet["model"] != ASTRA_MODEL or packet["mode"] != "subscription":
            raise ValueError("Live card dispatch requires the exact Astra subscription selector")
        request = self._request(packet["model"], packet["prompt"], packet,
                                packet["task_id"], "validator", output_budget)

        def recover(data: dict, raw: str) -> dict | None:
            accepted = workflow.snapshot(packet["task_id"]).get(packet["stage"])
            if accepted is None:
                return None
            if accepted["packet_id"] != pid or accepted.get("raw_response_sha256") != sha256_hash(data):
                return _incomplete("import_conflict", "A different card response is already frozen")
            fields = {key: value for key, value in accepted.items()
                      if key not in ("schema_version", "id", "packet_id", "response", "raw_response_sha256")}
            return {"status": "ok", "data": fields, "error": None}

        return self._dispatch("card", pid, packet, request, "SubscriptionCardResponse",
                              workflow.root, recover,
                              lambda data, raw: workflow.import_response(pid, data))

    def dispatch_evaluation(self, measurement_root: str | Path, request: dict, *,
                            output_budget: int = 2048) -> dict:
        exported = json.loads(canonical_json(request))
        if set(exported) != {"packet", "prompt", *_METADATA}:
            raise ValueError("Evaluation requires exactly one exported subscription request")
        pid = _packet_id(exported["packet"]["id"])
        root = Path(measurement_root)
        if exported != _load(root / "subscription" / f"{pid}.json"):
            raise ValueError("Evaluation request differs from its immutable export")
        if exported["model"] not in (SOL_MODEL, ASTRA_MODEL) or exported["mode"] != "subscription":
            raise ValueError("Evaluation requires the exact Sol or Astra subscription selector")
        if sha256_hash(exported["prompt"]) != exported["prompt_sha256"]:
            raise ValueError("Exported evaluation prompt hash does not match")
        validate("TerraPacket", exported["packet"])
        # Only the blind packet and its frozen prompt reach the model. Exported
        # date/context/model provenance is retained locally, never private mapping.
        model_request = self._request(exported["model"], exported["prompt"],
                                      exported["packet"], pid, "evaluation", output_budget)
        metadata = {key: exported[key] for key in _METADATA}
        importer = EvaluationImporter(root)

        def recover(data: dict, raw: str) -> dict | None:
            for label in importer.labels():
                if label["packet_id"] != pid:
                    continue
                provenance = label["provenance"]
                if (provenance["raw_response"] != raw
                        or any(provenance[key] != metadata[key] for key in _METADATA)):
                    return _incomplete("import_conflict", "A different evaluation response is already frozen")
                return {"status": "ok", "data": label, "error": None}
            return None

        return self._dispatch("evaluation", pid, exported, model_request, "EvalLabel", root,
                              recover, lambda data, raw: importer.import_response(pid, raw, metadata))

    @staticmethod
    def _request(model: str, instructions: str, packet: dict, task_id: str,
                 stage: str, output_budget: int) -> dict:
        return {"model": model, "config": {"reasoning_effort": "medium"},
                "instructions": instructions, "packet": packet, "history": [],
                "output_budget": output_budget, "task_id": task_id, "stage": stage}

    def _dispatch(self, kind, pid, exported, request, schema_id, destination,
                  recover, import_response) -> dict:
        execution_id = self.execution_id(kind, pid)
        key = self.records.key(execution_id)
        frozen = {"execution_id": execution_id, "exported": exported,
                  "request": request, "schema_id": schema_id,
                  "destination": str(Path(destination).resolve()),
                  "client_ledger": str(self.client.ledger.root)}
        request_path = f"requests/{key}.json"
        result_path = f"results/{key}.json"
        import_path = f"import_intent/{key}.json"
        with self.records.lock(execution_id):
            if (self.root / request_path).exists():
                if self.records.store.read(request_path) != frozen:
                    return _incomplete("request_hash_conflict", "Export identity already has a different frozen request")
            else:
                self.records.store.put_once(request_path, frozen)
            if (self.root / result_path).exists():
                return self.records.store.read(result_path)
            result = self.client.call(request, schema_id, execution_id)
            if result["status"] == "ok":
                response = self.client.ledger.lookup(execution_id)["response"]
                raw = response["raw_response"].get("output_text") if response else None
                if not isinstance(raw, str) or not raw.strip():
                    result = _incomplete("missing_raw_response", "Original subscription output_text is required")
                else:
                    accepted = recover(result["data"], raw)
                    if accepted is not None:
                        result = accepted
                    elif (self.root / import_path).exists():
                        result = _incomplete("ambiguous_import", "Import was started without a recoverable accepted result; repeating it is forbidden")
                    else:
                        self.records.store.put_once(import_path, {
                            "execution_id": execution_id, "raw_response_sha256": sha256_hash(raw),
                            "parsed_response_sha256": sha256_hash(result["data"])})
                        result = import_response(result["data"], raw)
            self.records.store.put_once(result_path, result)
            return result
