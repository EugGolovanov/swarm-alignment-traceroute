"""Outcome-blind, paired panel construction with immutable on-disk freezes."""
from __future__ import annotations

import copy
from pathlib import Path

from little_pom.contracts import canonical_json, derive_seed, sha256_hash, validate


def _immutable(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json(payload)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError(f"Immutable freeze differs: {path.name}")


def select_routed(profiles: list[dict], requirements: dict, seed: int) -> tuple[list[str], bool]:
    """First max overlap, then max uncovered overlap, then total-overlap fallback."""
    validate("Requirements", requirements)
    for profile in profiles:
        validate("Profile", profile)
        if profile["task_id"] != requirements["task_id"]:
            raise ValueError("Profiles and requirements must belong to the same task")
    by_worker = {p["worker_id"]: set(p["tags"]) for p in profiles}
    if len(by_worker) != len(profiles) or len(by_worker) < 2:
        raise ValueError("Routing requires at least two distinct complete workers")
    required = set(requirements["tags"])
    task_id = requirements["task_id"]
    def choose(candidates: list[str], tags: set[str], slot: int) -> str:
        return min(candidates, key=lambda w: (-len(by_worker[w] & tags),
                   derive_seed(seed, "routing_tie", task_id, slot, w), w))
    first = choose(sorted(by_worker), required, 1)
    remaining = sorted(set(by_worker) - {first})
    uncovered = required - by_worker[first]
    complementary = max(len(by_worker[w] & uncovered) for w in remaining)
    second = choose(remaining, uncovered if complementary else required, 2)
    fallback = not complementary and not len(by_worker[second] & required)
    return [first, second], fallback


class PanelBuilder:
    """Build all twelve task panels before reviews; never accept mechanism metadata.

    Production callers supply root. Omitting it explicitly selects an in-memory
    freeze, useful for deterministic unit fixtures; return values are defensive copies.
    """

    def __init__(self, root: str | Path | None = None, freeze_store=None):
        self.root = Path(root) if root is not None else None
        self.freeze_store = freeze_store
        self._frozen: dict[str, dict] = {}

    def build(self, pool: list[dict] | list[str], profiles: list[dict], requirements: dict, seed: int) -> list[dict]:
        validate("Requirements", requirements)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("Seed must be an integer")
        identities = []
        for item in pool:
            if isinstance(item, str):
                identities.append(item)
            elif isinstance(item, dict):
                # Deliberately project only identity and completion, never evidence or labels.
                output = item.get("output", item)
                if output.get("complete") is True:
                    identities.append(item["worker_id"])
            else:
                raise ValueError("Pool entries must be worker identities or worker records")
        identities.sort()
        if len(set(identities)) != len(identities) or len(identities) < 2:
            raise ValueError("Panel pool requires two distinct complete workers")
        if len(profiles) != len(identities) or {p["worker_id"] for p in profiles} != set(identities):
            raise ValueError("Profiles must exactly cover the complete worker pool")
        routed, fallback = select_routed(profiles, requirements, seed)
        task_id = requirements["task_id"]
        panels = []
        for repeat in (1, 2):
            # Independent domain-separated random permutations; no replacement within pair.
            random_pair = sorted(identities, key=lambda w: (derive_seed(seed, "random_panel", task_id, repeat, w), w))[:2]
            for condition, workers in (("Fresh", [None, None]), ("RandomHistory", random_pair), ("RoutedHistory", routed)):
                for variant in ("valid", "def"):
                    panel_id = sha256_hash({"task_id": task_id, "variant": variant, "condition": condition, "repeat": repeat})
                    specs = [{"reviewer_key": sha256_hash({"panel": panel_id, "slot": slot}), "worker_id": worker}
                             for slot, worker in enumerate(workers)]
                    panel = {"schema_version": "1.0", "id": panel_id, "task_id": task_id,
                             "variant": variant, "condition": condition, "repeat": repeat,
                             "reviewer_specs": specs, "router_fallback": fallback if condition == "RoutedHistory" else False}
                    panels.append(validate("Panel", panel))
        frozen = {"inputs_hash": sha256_hash({"pool": identities, "profiles": sorted(profiles, key=lambda p:p["worker_id"]),
                   "requirements": requirements, "seed": seed}), "panels": panels}
        if task_id in self._frozen and self._frozen[task_id] != frozen:
            raise ValueError("Panel specifications are already frozen")
        if self.root is not None:
            _immutable(self.root / "panels" / f"{sha256_hash(task_id)}.json", frozen)
        if self.freeze_store is not None:
            if self.root is None:
                raise ValueError("A freeze store requires a persistent panel root")
            import hashlib
            path = self.root / "panels" / f"{sha256_hash(task_id)}.json"
            manifest = {"schema_version": "1.0", "id": sha256_hash({"panels": task_id}),
                        "task_id": task_id, "stage": "panels",
                        "files": {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()}}
            try:
                self.freeze_store.create(manifest)
            except FileExistsError:
                if not self.freeze_store.verify(manifest):
                    raise ValueError("Panel freeze verification failed")
        self._frozen[task_id] = copy.deepcopy(frozen)
        return copy.deepcopy(panels)


__all__ = ["PanelBuilder", "select_routed"]
