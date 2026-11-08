"""RFC 8785 canonical serialization and frozen domain-separated randomness."""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

import rfc8785


_SECRET_KEYS = re.compile(r"^(api[_-]?key|authorization|access[_-]?token|secret|password|credential[s]?)$", re.I)


def reject_secrets(value: Any) -> None:
    """Reject credential-bearing fields; never silently redact hashed input."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            if _SECRET_KEYS.fullmatch(key):
                raise ValueError("Credential-bearing field is forbidden")
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)
    elif isinstance(value, str) and re.search(r"(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,})", value):
        raise ValueError("Credential-like value is forbidden")


def canonical_json(value: Any) -> bytes:
    """Return JCS UTF-8 bytes; reject non-JSON values and unsafe integers."""
    def check(item: Any) -> None:
        if item is None or type(item) in (str, int, float, bool):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("Value is not JSON-compatible")
    check(value)
    return rfc8785.dumps(value)


def sha256_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("sha256_bytes requires bytes")
    return hashlib.sha256(value).hexdigest()


def sha256_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def evidence_id(source_sha256: str, fragment_sha256: str) -> str:
    for digest in (source_sha256, fragment_sha256):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Evidence hashes must be lowercase SHA-256 hex")
    return sha256_hash({"source_sha256": source_sha256, "fragment_sha256": fragment_sha256})


def domain_hash(seed: int | str, domain: str, *parts: Any) -> str:
    """HMAC-SHA256(JCS(seed), JCS(namespace, parts)); version is frozen."""
    if type(seed) not in (int, str) or not isinstance(domain, str) or not domain:
        raise ValueError("Seed must be an integer/string and domain nonempty")
    message = {"schema_version": "1.0", "namespace": domain, "parts": list(parts)}
    return hmac.new(canonical_json(seed), canonical_json(message), hashlib.sha256).hexdigest()


def derive_seed(seed: int | str, domain: str, *parts: Any) -> int:
    """Use the first four HMAC bytes as a JSON-safe unsigned big-endian seed."""
    return int(domain_hash(seed, domain, *parts)[:8], 16)


def seeded_order(values: list[str], seed: int | str, domain: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("Seeded ordering requires unique values")
    return sorted(values, key=lambda value: (domain_hash(seed, domain, value), value))


def request_hash(request: dict, schema_id: str, transport: dict) -> str:
    """Hash the complete runtime request, including its frozen transport policy."""
    reject_secrets(request)
    reject_secrets(transport)
    return sha256_hash({"schema_version": "1.0", "schema_id": schema_id,
                        "request": request, "transport": transport})
