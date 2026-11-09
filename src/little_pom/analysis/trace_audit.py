"""Read-only saved SCI trace audit; exact byte identities are not scientific novelty.

Run with ``python -m little_pom.analysis.trace_audit --run RUN --output NEW_DIR``.
The output directory may exist, but audit files are exclusive-create. No runtime,
model, sandbox, or source-run storage constructors are used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from little_pom.contracts import canonical_json, sha256_bytes
from little_pom.contracts.hashing import evidence_id
from little_pom.evidence import EvidenceScope


LIMITATIONS = [
    "Unique means one accepted worker registered this exact source-SHA256 + fragment-SHA256 identity; it does not mean a unique scientific fact.",
    "Different excerpts, overlapping excerpts, equivalent calculations, and paraphrases may encode the same fact. Scientific novelty and causal usefulness are not estimated.",
    "Source verification proves persisted UTF-8 substring and hash integrity, not scientific correctness, provenance truth, or independent reproduction of generated results.",
    "Public-instruction classification uses recorded source paths and exact inclusion in task instruction/goal. Nonpublic fragments are not automatically new scientific knowledge.",
    "A fragment present in a packet is available, not necessarily read, understood, or used. A citation alone is not fragment availability or entailment.",
    "Accepted workers are stage states. Incomplete checkpoints and their registered evidence are retained separately and cannot count as accepted-worker novelty.",
    "Dispatch availability is reconstructed from saved request intents. Without a matching result, network delivery or model consumption remains unknown; request counts are not billed-call estimates.",
    "No frozen runs are resampled. This observational audit cannot establish a swarm advantage or an evidence-transfer treatment effect.",
]


class _Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}

    def raw(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"Saved artifact escapes run root: {path}")
        raw = path.read_bytes()
        self.files[str(path.relative_to(self.root))] = {
            "sha256": sha256_bytes(raw), "bytes": len(raw)}
        return raw

    def json(self, path):
        return json.loads(self.raw(path))


def _strings(value):
    """Include decoded embedded JSON text, without equating mentions with evidence."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value
        if value.lstrip().startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except (ValueError, TypeError):
                return
            yield from _strings(nested)


def _citations(value):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                result.update(x for x in item if isinstance(x, str))
            else:
                result.update(_citations(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_citations(item))
    return result


def _visibility(packet, evidence):
    texts = list(_strings(packet))
    return {
        "fragment_available_ids": sorted(eid for eid, obj in evidence.items()
                                         if obj.get("fragment") and any(obj["fragment"] in text for text in texts)),
        "id_mentioned_ids": sorted(eid for eid in evidence if any(eid in text for text in texts)),
    }


def _handoff(state):
    return {"result": state["output"]["result"], "limitations": state["output"]["limitations"],
            "evidence": [{"evidence_id": x["evidence_id"], "fragment": x["fragment"]}
                         for x in state["evidence"]]}


def _origin(record, public):
    source = record.get("source", "")
    if record.get("kind") == "generated":
        return "generated"
    if source in public:
        return "public_instruction"
    return "input_source" if record.get("kind") == "source" else "unknown"


def audit_saved_run(run, *, author_input_budget=32768):
    """Return deterministic audit data, reading only persisted local artifacts.

    ``author_input_budget`` is explicit because legacy launches did not persist
    a budget checkpoint when rejected before dispatch. Recorded checkpoints
    override it. Missing files and integrity failures are surfaced, never repaired.
    """
    reader = _Reader(run)
    root = reader.root
    stages = root / "stages" / "workers.json"
    if stages.exists():
        task_rows = reader.json(stages)["tasks"]
    else:
        task_rows = {}
        for path in sorted((root / "stages").glob("*--workers.json")):
            task_rows.update(reader.json(path).get("tasks", {}))
    if not task_rows:
        raise ValueError("No saved worker-stage tasks found")
    config = reader.json("launch-config.json") if (root / "launch-config.json").exists() else {}
    requests = defaultdict(list)
    for path in sorted((root / "intent").glob("*.json")):
        record = reader.json(path)
        if record.get("stage") in ("author", "review", "reviewer", "reviews"):
            requests[record["task_id"]].append((path, record))
    dispatch = {}
    for path in sorted((root / "reviews" / "dispatch").glob("*.json")):
        for slot in reader.json(path).get("slots", []):
            dispatch[slot["packet_id"]] = slot
    panels = {}
    if (root / "stages" / "panels.json").exists():
        panels = {p["id"]: p for p in reader.json("stages/panels.json").get("panels", [])}
    checkpoints = defaultdict(list)
    for path in sorted((root / "workers").glob("*/state.json")):
        state = reader.json(path)
        checkpoints[state["task_id"]].append(state)
    budgets = {}
    for path in sorted((root / "author").glob("*.budget.json")):
        record = reader.json(path)
        budgets[record["task_id"]] = record
    registry_objects = defaultdict(list)
    registry_provenance = defaultdict(list)
    for path in sorted((root / "evidence").glob("*/objects/*.json")):
        obj = reader.json(path)
        registry_objects[obj["task_id"]].append((path, obj))
    for path in sorted((root / "evidence").glob("*/provenance/*.json")):
        record = reader.json(path)
        registry_provenance[record["task_id"]].append(record)
    reports = []
    for task_id, saved in sorted(task_rows.items()):
        task = saved["task"]
        states = saved.get("states", [])
        accepted = {s["worker_id"] for s in states}
        objects = {e["evidence_id"]: e for e in saved.get("evidence", [])}
        provenance = {p["id"]: p for p in saved.get("provenance", [])}
        provenance.update({p["id"]: p for p in registry_provenance[task_id]})
        object_paths = {}
        object_conflicts = set()
        for path, obj in registry_objects[task_id]:
            eid = obj["evidence_id"]
            if eid in objects and any(objects[eid].get(key) != obj.get(key)
                                      for key in ("source_sha256", "fragment_sha256", "fragment", "task_id")):
                object_conflicts.add(eid)
            objects.setdefault(eid, obj)
            object_paths[eid] = path
        public_paths = {"__little_pom__/task.txt"}
        for state in checkpoints[task_id] + states:
            for history in state.get("history", []):
                # Environment metadata can name a nonstandard public task file.
                content = history.get("content")
                if isinstance(content, dict):
                    public = content.get("environment", {}).get("public_task_file")
                    if public:
                        public_paths.add(public)
        instruction_texts = [task.get("instruction", ""), task.get("goal", "")]
        owners = defaultdict(set)
        for state in states:
            for eid in state.get("evidence_ids", []):
                owners[eid].add(state["worker_id"])
        observations = defaultdict(list)
        for record in provenance.values():
            observations[record["evidence_id"]].append(record)
        fragment_owners = defaultdict(set)
        for eid, obj in objects.items():
            fragment_owners[obj.get("fragment_sha256")].update(owners[eid])
        handoffs = [_handoff(s) for s in states]
        handed = {x["evidence_id"] for h in handoffs for x in h["evidence"]}
        author_requests, reviewers = [], []
        for path, record in requests[task_id]:
            request = record["request"]
            packet = request.get("packet", {})
            result_path = root / "result" / path.name
            result = reader.json(result_path).get("service_result", {}) if result_path.exists() else {}
            citations = _citations(result.get("data"))
            packet_bytes = len(canonical_json(packet))
            binding_stage = "author" if record["stage"] == "author" else "reviews"
            binding_path = root / binding_stage / "evidence" / f"{record.get('execution_id')}.json"
            if binding_path.exists():
                binding = reader.json(binding_path)
                scope = EvidenceScope(binding["evidence"], provenance=binding["provenance"])
                if scope.manifest() != binding:
                    raise ValueError("Evidence transport binding has changed")
                citations = {scope.aliases.get(cid, cid) for cid in citations}
                packet = scope.restore(packet)
            visible = _visibility(packet, objects)
            entry = {"intent_path": str(path.relative_to(root)), "execution_id": record.get("execution_id"),
                     "request_bytes": len(canonical_json(request)), "packet_bytes": packet_bytes,
                     "citations": sorted(citations), "result_status": result.get("status"), **visible}
            if record["stage"] == "author":
                allowed = {e["evidence_id"] for h in packet.get("handoffs", []) for e in h.get("evidence", [])}
                entry.update({"authorized_ids": sorted(allowed), "unauthorized_citations": sorted(citations - allowed),
                              "unavailable_fragment_citations": sorted(citations - set(visible["fragment_available_ids"]))})
                author_requests.append(entry)
            else:
                slot = dispatch.get(packet.get("id"), {})
                panel = panels.get(slot.get("panel_id"), {})
                entry.update({"packet_id": packet.get("id"), "worker_id": slot.get("worker_id"),
                              "panel_id": slot.get("panel_id"), "condition": panel.get("condition"),
                              "variant": panel.get("variant"),
                              "unavailable_fragment_citations": sorted(citations - set(visible["fragment_available_ids"])),
                              "unknown_citations": sorted(citations - objects.keys()),
                              "citations_without_packet_id_mention": sorted(citations - set(visible["id_mentioned_ids"])),
                              "common_context": _visibility({k: v for k, v in packet.items() if k != "worker_context"}, objects),
                              "worker_context": _visibility(packet.get("worker_context", {}), objects)})
                reviewers.append(entry)
        cards = []
        for path in sorted((root / "cards").glob("*/*.json")):
            if path.name not in ("valid.json", "def.json"):
                continue
            record = reader.json(path)
            if record.get("task_id") != task_id:
                continue
            card = record.get("card", {})
            cited = _citations(card)
            cards.append({"path": str(path.relative_to(root)), "variant": card.get("variant", path.stem),
                          "citations": sorted(cited), "unknown_citations": sorted(cited - objects.keys()),
                          "proposal_citations_removed": sorted(_citations(saved.get("proposal")) - cited),
                          "handoff_ids_not_cited": sorted(handed - cited),
                          "not_in_accepted_handoffs": sorted(cited - handed)})
        proposal_ids = _citations(saved.get("proposal"))
        author_seen = {eid for r in author_requests for eid in r["fragment_available_ids"]}
        review_seen = {eid for r in reviewers for eid in r["fragment_available_ids"]}
        evidence_rows = []
        for eid, obj in sorted(objects.items()):
            fragment = obj.get("fragment", "")
            source_hash = obj.get("source_sha256", "")
            fragment_hash = obj.get("fragment_sha256", "")
            integrity = {"fragment_sha256_matches": sha256_bytes(fragment.encode()) == fragment_hash,
                         "identity_matches": False, "source_snapshot_present": False,
                         "source_sha256_matches": None, "exact_fragment_in_source": None}
            try:
                integrity["identity_matches"] = evidence_id(source_hash, fragment_hash) == eid
            except ValueError:
                pass
            object_path = object_paths.get(eid)
            if object_path:
                snapshot = object_path.parent.parent / "sources" / source_hash
                if snapshot.is_file():
                    raw = reader.raw(snapshot)
                    integrity["source_snapshot_present"] = True
                    integrity["source_sha256_matches"] = sha256_bytes(raw) == source_hash
                    try:
                        integrity["exact_fragment_in_source"] = bool(fragment) and fragment in raw.decode("utf-8")
                    except UnicodeDecodeError:
                        integrity["exact_fragment_in_source"] = False
            local = observations[eid]
            integrity["stage_registry_identity_agree"] = eid not in object_conflicts
            integrity["provenance_source_hashes_match"] = all(p.get("source_sha256") == source_hash for p in local)
            origins = sorted({_origin(p, public_paths) for p in local} or {_origin(obj, public_paths)})
            verified = all(integrity.values())
            evidence_rows.append({"evidence_id": eid, "source_sha256": source_hash, "fragment_sha256": fragment_hash,
                "fragment": fragment, "representative_source": obj.get("source"),
                "source_snapshot_path": str(object_path.parent.parent.relative_to(root) / "sources" / source_hash) if object_path else None,
                "observations": local, "origins": origins,
                "exact_fragment_in_public_instruction": bool(fragment) and any(fragment in text for text in instruction_texts),
                "same_exact_fragment_accepted_worker_ids": sorted(fragment_owners[fragment_hash]),
                "exact_fragment_unique_across_sources": len(fragment_owners[fragment_hash]) == 1,
                "public_instruction_only_origin": origins == ["public_instruction"],
                "accepted_worker_ids": sorted(owners[eid]), "accepted_worker_count": len(owners[eid]),
                "exact_identity_unique_to_accepted_worker": len(owners[eid]) == 1,
                "scientific_novelty": "not_assessed", "integrity": integrity, "verified_source_fragment": verified,
                "handed_off": eid in handed, "available_in_dispatched_author_packet": eid in author_seen,
                "proposal_cited": eid in proposal_ids,
                "card_variants_citing": sorted(c["variant"] for c in cards if eid in c["citations"]),
                "reviewer_packet_ids_with_fragment": sorted(r["packet_id"] for r in reviewers if eid in r["fragment_available_ids"]),
                "reviewer_execution_ids_citing": sorted(r["execution_id"] for r in reviewers if eid in r["citations"])})
        budget = budgets.get(task_id, {})
        cap = budget.get("input_budget", author_input_budget)
        pressure = {"counting": "canonical_json_utf8_bytes_not_model_tokens", "input_budget_bytes": cap,
                    "budget_basis": "saved_author_checkpoint" if budget else "explicit_audit_argument",
                    "handoff_list_bytes": len(canonical_json(handoffs)),
                    "per_worker": [{"worker_id": state["worker_id"], "handoff_bytes": len(canonical_json(handoff)),
                                    "evidence_bytes": len(canonical_json(handoff["evidence"])),
                                    "evidence_references": len(handoff["evidence"])}
                                   for state, handoff in zip(states, handoffs)],
                    "request_bytes": author_requests[0]["request_bytes"] if author_requests else budget.get("input_upper_bound"),
                    "request_size_basis": "saved_request" if author_requests else "saved_checkpoint" if budget else "not_dispatched"}
        # A lower bound alone establishes pressure without inventing a historical
        # request from today's prompt or constructor defaults.
        pressure["handoff_alone_exceeds_budget"] = pressure["handoff_list_bytes"] > cap
        pressure["request_exceeds_budget"] = pressure["request_bytes"] > cap if pressure["request_bytes"] is not None else None
        verified = [e for e in evidence_rows if e["verified_source_fragment"] and e["accepted_worker_count"]]
        representative = []
        for origin in ("public_instruction", "input_source", "generated"):
            for unique in (True, False):
                matches = [e for e in verified if origin in e["origins"] and e["exact_identity_unique_to_accepted_worker"] == unique]
                if matches:
                    e = min(matches, key=lambda x: (len(x["fragment"]), x["evidence_id"]))
                    representative.append({"evidence_id": e["evidence_id"], "origin": origin,
                        "exact_identity_unique": unique, "source": e["representative_source"],
                        "fragment": e["fragment"], "finding": "Persisted source contains this exact fragment; no semantic novelty claim.",
                        "handed_off": e["handed_off"], "proposal_cited": e["proposal_cited"]})
        reports.append({"task_id": task_id, "counts": {
            "accepted_workers": len(states), "saved_worker_checkpoints": len(checkpoints[task_id]),
            "incomplete_or_unaccepted_workers": len({s["worker_id"] for s in checkpoints[task_id]} - accepted),
            "registered_exact_identities": len(objects), "verified_exact_identities": sum(e["verified_source_fragment"] for e in evidence_rows),
            "accepted_verified_exact_identities": len(verified),
            "unique_accepted_verified_exact_identities": sum(e["accepted_worker_count"] == 1 for e in verified),
            "shared_accepted_verified_exact_identities": sum(e["accepted_worker_count"] > 1 for e in verified),
            "accepted_distinct_exact_fragment_hashes": len({e["fragment_sha256"] for e in verified}),
            "public_instruction_only_verified_identities": sum(e["public_instruction_only_origin"] for e in verified),
            "origins_verified_identity_counts_nonexclusive": dict(Counter(origin for e in verified for origin in e["origins"])),
            "handed_off_ids": len(handed), "author_calls": len(author_requests), "reviewer_calls": len(reviewers)},
            "worker_missingness": saved.get("worker_missingness", []), "author_error": saved.get("author_error"),
            "handoff_pressure": pressure, "author_requests": author_requests, "proposal_citations": sorted(proposal_ids),
            "registered_but_not_handed_off_ids": sorted(objects.keys() - handed),
            "handoff_not_cited_by_proposal_ids": sorted(handed - proposal_ids) if saved.get("proposal") else None,
            "handoff_not_available_to_any_dispatched_reviewer_ids": sorted(handed - review_seen),
            "cards": cards, "reviewers": reviewers, "evidence": evidence_rows,
            "representative_sourced_findings": representative})
    return {"schema_version": "1.0", "audit_kind": "saved_sci_exact_fragment_transfer", "source_run": str(root),
            "model_calls": 0, "scientific_novelty": "not_assessed", "limitations": LIMITATIONS,
            "tasks": reports, "source_manifest": dict(sorted(reader.files.items())),
            "saved_config_present": bool(config)}


def write_audit(run, output, *, author_input_budget=32768):
    """Exclusive-create artifacts outside the source run, preserving frozen data."""
    root, destination = Path(run).resolve(), Path(output).resolve()
    if destination == root or destination.is_relative_to(root):
        raise ValueError("Audit output must be outside the frozen source run")
    names = ("trace-audit.json", "trace-audit-summary.json")
    if any((destination / name).exists() for name in names):
        raise FileExistsError("Audit output already exists; use a new diagnostic directory")
    report = audit_saved_run(root, author_input_budget=author_input_budget)
    summary = {key: report[key] for key in ("schema_version", "audit_kind", "source_run", "model_calls", "scientific_novelty", "limitations")}
    summary["tasks"] = [{key: task[key] for key in ("task_id", "counts", "author_error", "handoff_pressure", "representative_sourced_findings")}
                        for task in report["tasks"]]
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in zip(names, (report, summary)):
        with (destination / name).open("xb") as stream:
            stream.write(canonical_json(value) + b"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--author-input-budget", type=int, default=32768,
                        help="Legacy cap in UTF-8 bytes where no saved checkpoint exists")
    args = parser.parse_args(argv)
    if args.author_input_budget <= 0:
        parser.error("--author-input-budget must be positive")
    report = write_audit(args.run, args.output, author_input_budget=args.author_input_budget)
    print(json.dumps({"tasks": len(report["tasks"]), "output": str(args.output), "model_calls": 0}))


if __name__ == "__main__":
    main()
