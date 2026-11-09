"""Validate explicit operator-supplied preflight evidence, never infer live readiness."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from little_pom.contracts import sha256_hash


def evaluate_readiness(config, environment, attestation, evidence_root, task_ids):
    blockers = []
    if attestation is None:
        return {'ready': False, 'blockers': ['explicit_readiness_evidence_missing'], 'evidence_kind': 'none'}
    if not isinstance(attestation, dict):
        raise ValueError('Readiness attestation must be a JSON object')
    for key in ('models', 'checks', 'images'):
        if key in attestation and not isinstance(attestation[key], dict):
            raise ValueError(f'Readiness {key} must be a JSON object')
    if attestation.get('config_hash') != sha256_hash(config):
        blockers.append('readiness_configuration_mismatch')
    if attestation.get('snapshot_sha256') != config['snapshot']['sha256']:
        blockers.append('readiness_snapshot_mismatch')
    models = attestation.get('models', {})
    for role in ('worker', 'swarm', 'astra', 'terra'):
        expected = config['models'][role]
        actual = models.get(role, {})
        if not isinstance(actual, dict):
            raise ValueError('Readiness model entries must be objects')
        if actual.get('model') != expected['model'] or actual.get('mode') != expected['mode']:
            blockers.append(f'{role}_model_or_mode_mismatch')
        for field in ('transport', 'roles', 'parameters'):
            if field in expected and actual.get(field) != expected[field]:
                blockers.append(f'{role}_{field}_mismatch')
        if expected['mode'] in ('mock', 'unverified'):
            blockers.append(f'{role}_mode_unverified')
    required = ['model_access', 'quota_and_budget', 'structured_output', 'subscription_context_isolation']
    for task_id in task_ids:
        required.extend([f'{task_id}:runtime', f'{task_id}:oracle'])
        if task_id == 'dna-storage-codec':
            required.append(f'{task_id}:verifier_resolution')
    checks = attestation.get('checks', {})
    root = Path(evidence_root).resolve()
    for name in required:
        record = checks.get(name, {})
        if not isinstance(record, dict):
            raise ValueError('Readiness checks must be objects')
        if record.get('passed') is not True:
            blockers.append(f'{name}:not_passed')
            continue
        relative = record.get('path')
        digest = record.get('sha256')
        if not isinstance(relative, str) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            blockers.append(f'{name}:invalid_evidence_reference')
            continue
        candidate = root / relative
        path = candidate.resolve()
        if (not path.is_relative_to(root) or not path.is_file() or candidate.is_symlink()
                or any(parent.is_symlink() for parent in candidate.parents if parent != root and parent.is_relative_to(root))):
            blockers.append(f'{name}:evidence_path_invalid')
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            blockers.append(f'{name}:evidence_digest_mismatch')
    docker = environment.get('docker', {})
    if docker.get('available') is not True or not docker.get('inspected'):
        blockers.append('live_docker_resources_unverified')
    by_task = {task['task_id']: task for task in environment.get('tasks', [])}
    for task_id in task_ids:
        task = by_task.get(task_id, {})
        if task.get('ram_sufficient') is not True or task.get('cpu_sufficient') is not True:
            blockers.append(f'{task_id}:resources_insufficient_or_unverified')
        image = task.get('image', {})
        if image.get('state') != 'present' or image.get('provenance_matches') is not True:
            blockers.append(f'{task_id}:image_unverified')
        if 'image_architecture_mismatch' in task.get('blockers', []):
            blockers.append(f'{task_id}:image_architecture_mismatch')
    return {'ready': not blockers, 'blockers': blockers, 'evidence_kind': 'explicit_operator_attestation_with_hashed_artifacts',
            'independently_reexecuted': False, 'attestation_hash': sha256_hash(attestation)}
