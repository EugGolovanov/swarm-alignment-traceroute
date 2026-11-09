"""Explicit pilot configuration and a two-phase, append-only freeze lifecycle."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .hashing import canonical_json, reject_secrets, sha256_hash
from .validation import ContractError, validate

PILOT_TASK_IDS = ("dna-storage-codec", "nanoindentation-property-extraction", "finite-free-stam")


def default_config_path() -> Path:
    """Repository-local convenience; applications should pass an explicit path."""
    return Path(__file__).resolve().parents[3] / "config" / "little" / "pilot.json"


def validate_config(config: dict) -> dict:
    validate("Config", config)
    from little_pom.tasks import SCI_TASK_IDS

    expected = SCI_TASK_IDS if config["pilot_kind"] == "sci_agent_exam_exploratory" else PILOT_TASK_IDS
    if set(config["task_ids"]) != set(expected) or len(config["task_ids"]) != len(expected):
        raise ContractError("The pilot must contain exactly its selected corpus tasks")
    if set(config["defect_assignments"]) != set(expected):
        raise ContractError("Each selected task requires exactly one defect assignment")
    if config["pilot_kind"] == "sci_agent_exam_exploratory" and config["d0_task_id"] is not None:
        raise ContractError("The public SCI sample is exploratory, without a separate D0")
    if config["d0_task_id"] in expected:
        raise ContractError("D0 must be designated separately from pilot tasks")
    if config["models"]["astra"]["model"] not in ("gpt6-astra", "openai-codex/gpt-6-astra"):
        raise ContractError("Astra uses the explicitly requested subscription model")
    for role, transport, roles in (
        ("worker", "omp_subscription", {"worker"}),
        ("swarm", "omp_subscription" if config["models"]["swarm"]["model"] == "openai-codex/gpt-6.1-terra" else "adaption", {"author", "reviewer"}),
        ("astra", "subscription", {"validator", "calibration_a", "calibration_b", "adjudicator"}),
        ("terra", "subscription", {"blind_evaluator"}),
    ):
        model = config["models"][role]
        if model["transport"] != transport or set(model["roles"]) != roles:
            raise ContractError("Model roles and transports must preserve the pilot assignment")
    for prompt in config["prompts"].values():
        if "text" in prompt:
            from .hashing import sha256_bytes
            if sha256_bytes(prompt["text"].encode()) != prompt["sha256"]:
                raise ContractError("Frozen prompt text/hash mismatch")
    return config


def load_config(path: str | Path) -> dict:
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


class ConfigFreeze:
    """D0 designation -> observed D0 -> fix window -> immutable final config.

    Stage artifacts are separate immutable files. Their addition never rewrites
    either config manifest. Mock freezes deliberately do not claim live readiness.
    """
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _put(self, relative: str, payload: dict) -> dict:
        reject_secrets(payload)
        data = canonical_json(payload)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ContractError("Frozen artifact cannot be changed")
            return payload
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            # An interrupted write remains fail-closed; it is never overwritten.
            raise
        return payload

    def _get(self, relative: str) -> dict:
        try:
            return json.loads((self.root / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError("Required freeze artifact is unavailable") from exc

    def _manifest(self, phase: str, config: dict, artifacts: dict | None, **extra) -> dict:
        validate_config(config)
        inventory = dict(artifacts or {})
        if any(not isinstance(path, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
               for path, digest in inventory.items()):
            raise ContractError("Artifact inventory requires path-to-SHA256 entries")
        payload = {"schema_version": "1.0", "phase": phase, "config": config,
                   "config_hash": sha256_hash(config), "artifacts": inventory, **extra}
        payload["id"] = sha256_hash(payload)
        return payload

    def freeze_mock(self, config: dict, artifacts: dict | None = None) -> dict:
        if config.get("mode") != "mock":
            raise ContractError("Independent mock freeze requires explicit mock mode")
        return self._put("mock_freeze_manifest.json", self._manifest("mock", config, artifacts, live_ready=False))

    def freeze_exploratory(self, config: dict, artifacts: dict | None = None) -> dict:
        """Explicit operator-requested no-D0 pilot; never impersonates final/D0."""
        if config.get('d0_task_id') is not None:
            raise ContractError('Exploratory pilot must not claim a designated D0')
        if any((self.root / name).exists() for name in (
                'd0_freeze_manifest.json', 'final_freeze_manifest.json', 'mock_freeze_manifest.json')):
            raise ContractError('Exploratory pilot requires a separate run directory')
        return self._put('exploratory_freeze_manifest.json', self._manifest(
            'exploratory', config, artifacts, d0_completed=False,
            independent_confirmation=False, task_order=list(config['task_ids']),
            execution_order='task_major_through_reviews',
            measurement_sampling='global_after_all_reviews',
            protocol_amendment=('operator_requested_five_sci_examples_without_separate_d0'
                if config['pilot_kind'] == 'sci_agent_exam_exploratory'
                else 'operator_requested_three_tasks_without_separate_d0')))

    def freeze_d0(self, config: dict, task_id: str, artifacts: dict | None = None) -> dict:
        if not isinstance(task_id, str) or not task_id or task_id in PILOT_TASK_IDS:
            raise ContractError("An explicit separate D0 task is required")
        if config.get("d0_task_id") != task_id:
            raise ContractError("D0 designation must be recorded explicitly in config")
        if (self.root / "final_freeze_manifest.json").exists():
            raise ContractError("D0 cannot be changed after final freeze")
        return self._put("d0_freeze_manifest.json", self._manifest("d0", config, artifacts, d0_task_id=task_id))

    def record_d0_result(self, result: dict) -> dict:
        d0 = self._get("d0_freeze_manifest.json")
        self.verify("d0", d0["config"])
        validate("ServiceResult", result)
        if result["status"] != "ok":
            raise ContractError("Incomplete D0 cannot open the fix window")
        if result["data"].get("task_id") != d0["d0_task_id"]:
            raise ContractError("D0 result must identify the explicitly designated task")
        return self._put("d0_result.json", {"schema_version": "1.0", "id": sha256_hash(result),
            "d0_manifest_id": d0["id"], "consumed_task_id": d0["d0_task_id"], "result": result})

    def open_fix_window(self) -> dict:
        result = self._get("d0_result.json")
        d0 = self._get("d0_freeze_manifest.json")
        self.verify("d0", d0["config"])
        if result["id"] != sha256_hash(result["result"]) or result["d0_manifest_id"] != d0["id"]:
            raise ContractError("D0 result integrity check failed")
        if (self.root / "final_freeze_manifest.json").exists():
            raise ContractError("The final freeze closes the fix window")
        return self._put("fix_window.json", {"schema_version": "1.0", "id": sha256_hash(result),
            "d0_result_id": result["id"], "status": "open"})

    def freeze_final(self, config: dict, artifacts: dict | None = None) -> dict:
        d0 = self._get("d0_freeze_manifest.json")
        self.verify("d0", d0["config"])
        result = self._get("d0_result.json")
        window = self._get("fix_window.json")
        if (result["id"] != sha256_hash(result["result"]) or result["d0_manifest_id"] != d0["id"]
                or window["id"] != sha256_hash(result) or window["d0_result_id"] != result["id"]):
            raise ContractError("D0/fix-window chain integrity check failed")
        if config.get("d0_task_id") != d0["d0_task_id"]:
            raise ContractError("Final config must preserve the consumed D0 designation")
        return self._put("final_freeze_manifest.json", self._manifest("final", config, artifacts,
            d0_manifest_id=d0["id"], consumed_task_ids=[d0["d0_task_id"]], independent_confirmation=False))

    def verify(self, phase: str, config: dict, artifacts: dict | None = None) -> bool:
        if phase not in ("d0", "final", "mock", "exploratory"):
            raise ContractError("Unknown freeze phase")
        manifest = self._get(f"{phase}_freeze_manifest.json")
        identity = dict(manifest)
        saved_id = identity.pop("id")
        if saved_id != sha256_hash(identity) or manifest["config_hash"] != sha256_hash(manifest["config"]):
            raise ContractError("Freeze manifest was modified")
        if manifest["config_hash"] != sha256_hash(validate_config(config)):
            raise ContractError("Frozen config has changed")
        if artifacts is not None and manifest["artifacts"] != artifacts:
            raise ContractError("Frozen artifact inventory has changed")
        return True

    def add_stage(self, stage: str, payload: dict) -> dict:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", stage):
            raise ContractError("Invalid stage name")
        final = self._get("final_freeze_manifest.json")
        self.verify("final", final["config"])
        manifest = {"schema_version": "1.0", "id": sha256_hash({"stage": stage, "payload": payload}),
                    "stage": stage, "final_manifest_id": final["id"], "payload": payload}
        return self._put(f"stages/{stage}.json", manifest)
