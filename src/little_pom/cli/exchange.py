"""Immutable JSON exchange templates, never fabricated subscription responses."""
from __future__ import annotations

import json
from pathlib import Path

from little_pom.contracts import canonical_json, get_schema, sha256_hash
from little_pom.runtime import ArtifactStore


def _put(root, relative, value):
    path = root / relative
    content = canonical_json(value)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('Exchange artifact changed; never edit exported templates in place')
    else:
        ArtifactStore(root).put_once(relative, content)
    return str(path)


def card_response_template(packet):
    """Null judgments deliberately fail import until replaced by real responses."""
    stage, inputs = packet['stage'], packet['inputs']
    result = {'model': 'Astra', 'mode': 'subscription', 'validated_at': None, 'trace': None}
    if stage == 'applicability':
        result.update(applicability={kind: None for kind in inputs['defect_types']}, assignment=inputs['assignment'])
    elif stage == 'valid':
        result.update(card=None, confirmed_valid=None, supporting_evidence_verified=None)
    else:
        result.update(card=None, defect_count=None, target_evidence_ids=None, visibility_labels=None,
                      confirmations={key: None for key in (
                          'target_defect_present', 'no_second_significant_defect', 'target_evidence_exists',
                          'informed_workers_exist', 'private_evidence_checked')})
    return result


def export_exchange(run_root):
    root = Path(run_root)
    pending = []
    from little_pom.cards import CardWorkflow
    workflow = CardWorkflow(root, {})
    for path in sorted((root / 'cards' / 'packets').glob('*.json')):
        packet = json.loads(path.read_text())
        if packet['stage'] in workflow.snapshot(packet['task_id']):
            continue
        pid = packet['id']
        request = _put(root, f'exchange/validator/{pid}.request.json', packet)
        template = _put(root, f'exchange/validator/{pid}.response-template.json', {pid: card_response_template(packet)})
        contract = _put(root, 'exchange/validator/ExperimentalCard.schema.json', get_schema('ExperimentalCard'))
        pending.append({'packet_id': pid, 'request_path': request, 'response_template_path': template,
                        'card_contract_path': contract, 'stage': packet['stage']})
    from little_pom.measurement import EvaluationImporter
    accepted = {label['packet_id'] for label in EvaluationImporter(root / 'measurement').labels()}
    for path in sorted((root / 'measurement' / 'subscription').glob('*.json')):
        request = json.loads(path.read_text())
        pid = request['packet']['id']
        if pid in accepted:
            continue
        # Copy only the public request; no private mapping or condition metadata.
        public = _put(root, f'exchange/evaluation/{pid}.request.json', request)
        template = _put(root, f'exchange/evaluation/{pid}.response-template.json', {pid: {
            'raw_response': None, 'metadata': {
                key: request.get(key) for key in ('model', 'mode', 'date', 'context_id', 'prompt_sha256')}}})
        pending.append({'packet_id': pid, 'request_path': public, 'response_template_path': template})
    bundle = {}
    for item in pending:
        bundle.update(json.loads(Path(item['response_template_path']).read_text()))
    bundle_path = (_put(root, f'exchange/operator/response-bundle-template.{sha256_hash(bundle)}.json', bundle)
                   if bundle else None)
    return {'status': 'ok', 'pending': pending, 'response_bundle_template_path': bundle_path,
            'instructions': 'Send only each request.json to its specified subscription model in a fresh context. '
                            'Replace nulls with the actual complete response and actual metadata. '
                            'Merge packet-ID mappings into a response bundle OUTSIDE the run directory; '
                            'resume using --responses PATH. Templates are not model answers. '
                            'Never send measurement/private or the whole run directory.'}
