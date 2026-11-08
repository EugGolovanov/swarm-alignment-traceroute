"""Offline Astra subscription packets with append-only validation and card freezes.

Export does not call a model or browser. An operator carries each packet into a
fresh Astra subscription context and imports the complete response unchanged.
Applicability precedes workers, valid precedes frozen requirements, and only then
may the single-defect packet be exported. Rejected responses remain in the trace.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from little_pom.contracts import canonical_json, sha256_hash, validate
from little_pom.profiles import RequirementsBuilder
from .mechanism import MechanismBuilder, card_text, diagnose_exploitability

PROMPTS = {
    "applicability": "Assess every listed defect type against this task BEFORE any worker outcomes. Return applicability booleans, the preselected applicable assignment, and a full rationale. This three-task pilot does not require all six defect types or permit replacement of failed tasks. Use Astra subscription only.",
    "valid": "Confirm or minimally repair q0 into valid. Verify every supporting evidence reference against its actual fragment. Return the valid card, confirmed_valid and supporting_evidence_verified booleans, and full validation trace. Do not inject any defect; requirements are frozen after this response. No review outcomes are available.",
    "def": "Modify the frozen valid card by injecting exactly one assigned target defect. Preserve the proposal link. Return card, target_evidence_ids, content-based visibility_labels for valid/def, defect_count=1, and confirmations target_defect_present, no_second_significant_defect, target_evidence_exists, informed_workers_exist, private_evidence_checked. Give the complete validator trace. Do not use review outcomes.",
}


def _put(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json(value)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("Immutable record already exists with different content")


class CardWorkflow:
    def __init__(self, root: str | Path, vocabulary: dict, freeze_store=None):
        self.root = Path(root)
        self.vocabulary = copy.deepcopy(vocabulary)
        self.freeze_store = freeze_store

    def _path(self, task_id: str, name: str) -> Path:
        return self.root / "cards" / sha256_hash(task_id) / (name + ".json")

    def _load(self, task_id: str, name: str) -> dict:
        return json.loads(self._path(task_id, name).read_text())

    def _freeze(self, task_id: str, name: str, value: dict) -> None:
        path = self._path(task_id, name)
        _put(path, value)
        if self.freeze_store is not None:
            manifest = {"schema_version": "1.0", "id": sha256_hash({"task_id": task_id, "stage": name}),
                        "task_id": task_id, "stage": name,
                        "files": {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()}}
            try:
                self.freeze_store.create(manifest)
            except FileExistsError:
                if not self.freeze_store.verify(manifest):
                    raise ValueError("Stored freeze no longer verifies")

    def assert_applicability(self, task_id: str) -> dict:
        record = self._load(task_id, "applicability")
        if not record["applicability"].get(record["assignment"]):
            raise ValueError("Assigned defect was not applicable")
        return record

    def begin_workers(self, task_id: str) -> dict:
        record = self.assert_applicability(task_id)
        self._freeze(task_id, "workers_started", {"schema_version": "1.0", "id": sha256_hash({"workers": task_id}),
                     "task_id": task_id, "applicability_hash": sha256_hash(record)})
        return record

    def snapshot(self, task_id: str) -> dict:
        """Return detached persisted lifecycle data for orchestration and reporting."""
        result = {"task_id": task_id}
        for name in ("applicability", "requirements", "valid", "def"):
            if self._path(task_id, name).exists():
                result[name] = self._load(task_id, name)
        return result

    def export(self, stage: str, inputs: dict) -> list[dict]:
        if stage not in PROMPTS or not isinstance(inputs, dict):
            raise ValueError("Unknown validator stage or malformed inputs")
        allowed = {"applicability": {"task", "defect_types", "assignment"},
                   "valid": {"task", "q0", "evidence", "provenance", "common_dossier"},
                   "def": {"task_id"}}[stage]
        if set(inputs) - allowed:
            raise ValueError("Validator inputs include forbidden or unknown metadata")
        task_id = inputs.get("task_id") or inputs.get("task", {}).get("task_id") or inputs.get("task", {}).get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("Task identity required")
        if self._path(task_id, stage).exists():
            raise ValueError("Validator stage is already frozen")
        payload = copy.deepcopy(inputs)
        if stage == "applicability":
            validate("Task", inputs["task"])
            if self._path(task_id, "workers_started").exists() or self._path(task_id, "valid_export").exists():
                raise ValueError("Applicability must precede worker outcomes")
            types = inputs["defect_types"]
            if not isinstance(types, list) or not types or len(set(types)) != len(types) or any(not isinstance(t, str) or not t for t in types):
                raise ValueError("Distinct defect types required")
            if inputs["assignment"] not in types:
                raise ValueError("Assignment must be declared before worker outcomes")
        elif stage == "valid":
            self.begin_workers(task_id)
            validate("Task", inputs["task"])
            validate("ProposalCard", inputs["q0"])
            if inputs["q0"]["task_id"] != task_id:
                raise ValueError("Proposal task mismatch")
            for fragment in inputs["evidence"]:
                validate("Evidence", fragment)
                if fragment["task_id"] != task_id:
                    raise ValueError("Evidence task mismatch")
            if not isinstance(inputs["provenance"], list):
                raise ValueError("Evidence provenance required")
        else:
            valid = self._load(task_id, "valid")
            requirements = self._load(task_id, "requirements")
            if requirements["card_id"] != valid["card"]["id"]:
                raise ValueError("Frozen requirements do not match valid card")
            exported = self._load(task_id, "valid_export")["inputs"]
            payload = {"task_id": task_id, "valid": valid["card"], "requirements": requirements,
                       "assignment": self.assert_applicability(task_id)["assignment"],
                       "task": exported["task"], "evidence": exported["evidence"],
                       "provenance": exported["provenance"], "common_dossier": exported.get("common_dossier", "")}
        packet = {"schema_version": "1.0", "id": sha256_hash({"stage": stage, "inputs": payload, "prompt": PROMPTS[stage]}),
                  "task_id": task_id, "stage": stage, "model": "Astra", "mode": "subscription",
                  "prompt": PROMPTS[stage], "inputs": payload}
        _put(self._path(task_id, stage + "_export"), packet)
        _put(self.root / "cards" / "packets" / (packet["id"] + ".json"), packet)
        return [copy.deepcopy(packet)]

    def import_response(self, packet_id: str, response: dict) -> dict:
        try:
            # Packet IDs are digests, not filesystem paths supplied by a subscriber.
            if not isinstance(packet_id, str) or len(packet_id) != 64 or any(c not in "0123456789abcdef" for c in packet_id):
                raise ValueError("Invalid packet identity")
            packet = json.loads((self.root / "cards" / "packets" / (packet_id + ".json")).read_text())
            task_id, stage = packet["task_id"], packet["stage"]
            trace_id = uuid4().hex
            _put(self.root / "cards" / "traces" / (trace_id + ".json"),
                 {"schema_version": "1.0", "id": trace_id, "task_id": task_id,
                  "packet": packet, "response": response})
            if self._path(task_id, stage).exists():
                raise ValueError("Import would alter an already frozen validator stage")
            if (self.root / "panels" / f"{sha256_hash(task_id)}.json").exists():
                raise ValueError("Card imports are closed after panel freeze")
            if not isinstance(response, dict):
                raise ValueError("Response must be a JSON object")
            if response.get("model") != "Astra" or response.get("mode") != "subscription":
                raise ValueError("Astra subscription response provenance required")
            for key in ("trace", "validated_at"):
                if not isinstance(response.get(key), str) or not response[key].strip():
                    raise ValueError(f"Full validator {key} required")
            inputs = packet["inputs"]
            data = {"stage": stage, "task_id": task_id}
            if stage == "applicability":
                if self._path(task_id, "workers_started").exists():
                    raise ValueError("Applicability cannot change after outcomes begin")
                labels = response.get("applicability")
                if not isinstance(labels, dict) or set(labels) != set(inputs["defect_types"]) or any(type(v) is not bool for v in labels.values()):
                    raise ValueError("One boolean applicability label per declared defect type required")
                assignment = response.get("assignment")
                if assignment != inputs["assignment"] or not labels.get(assignment):
                    raise ValueError("Preselected assignment must be applicable; retain failed tasks rather than replacing them")
                data.update(applicability=labels, assignment=assignment)
            else:
                card = response.get("card")
                validate("ExperimentalCard", card)
                if card["task_id"] != task_id or card["variant"] != stage:
                    raise ValueError("Response card task/variant mismatch")
                proposal_id = inputs["q0"]["id"] if stage == "valid" else inputs["valid"]["proposal_id"]
                if card["proposal_id"] != proposal_id:
                    raise ValueError("Proposal lineage changed")
                evidence_ids = {e["evidence_id"] for e in inputs["evidence"]}
                if not set(card["evidence_ids"]) <= evidence_ids:
                    raise ValueError("Card cites absent supporting evidence")
                if stage == "valid":
                    if response.get("confirmed_valid") is not True or response.get("supporting_evidence_verified") is not True:
                        raise ValueError("Valid card and supporting evidence must be confirmed")
                    requirements = RequirementsBuilder().build(card, self.vocabulary)
                    self._freeze(task_id, "requirements", requirements)
                    data["requirements"] = requirements
                else:
                    confirmations = response.get("confirmations", {})
                    required = ("target_defect_present", "no_second_significant_defect", "target_evidence_exists", "informed_workers_exist", "private_evidence_checked")
                    if response.get("defect_count") != 1 or type(response.get("defect_count")) is not int or any(confirmations.get(k) is not True for k in required):
                        raise ValueError("Exactly one defect and all five validator confirmations required")
                    if card_text(card) == card_text(inputs["valid"]):
                        raise ValueError("Defective card must change scientific content")
                    cards = {"task": inputs["task"], "valid": inputs["valid"], "def": card,
                             "target_defect_type": inputs["assignment"], "target_evidence_ids": response["target_evidence_ids"],
                             "provenance": inputs["provenance"], "common_dossier": inputs["common_dossier"]}
                    mechanism = MechanismBuilder().build(cards, inputs["evidence"], response["visibility_labels"])
                    if not mechanism["informed_workers"]:
                        raise ValueError("Validator claimed informed workers but no evidence owner exists")
                    data["mechanism"] = mechanism
                data["card"] = card
            record = {"schema_version": "1.0", "id": sha256_hash({"packet_id": packet_id, "response": response}),
                      "packet_id": packet_id, "response": response, **data}
            self._freeze(task_id, stage, record)
            return {"status": "ok", "data": copy.deepcopy(data), "error": None}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return {"status": "incomplete", "data": None,
                    "error": {"code": "validator_import_rejected", "message": str(exc)}}


__all__ = ["CardWorkflow", "MechanismBuilder", "diagnose_exploitability", "card_text", "PROMPTS"]
