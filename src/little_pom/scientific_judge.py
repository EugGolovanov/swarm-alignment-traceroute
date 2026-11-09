"""Grade the five preserved SCI packets through connected Astra OAuth, never solvers.

Run with --live to dispatch; without it, write a local-only budget preflight.
Every invocation requires a new output directory. Interrupted intents are never
resumed/retried by this command. Historical packets and checker outputs are inputs.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import time
from types import ModuleType

from little_pom.contracts import canonical_json, validate
from little_pom.runtime.subscription import OmpSubscriptionBackend, validate_upstream_messages

MODEL = 'openai-codex/gpt-6-astra'
UPSTREAM_MODEL = 'gpt-5.6-sol'
ROLE_ADAPTATION = 'exact-trusted-upstream-guidance-prefix-to-codex-instructions/v2'
RUBRIC_MARKER = '## Rubric criteria\n'
OUTPUT_TOKENS = 128_000
TASKS = {
    'bioinformatics-error-diagnosis-01': ('bioinformatics', 0.65, 0.35),
    'chemistry-result-interpretation-02': ('chemistry', 0.45, 0.55),
    'materials-science-literature-synthesis-02': ('materials-science', 0.20, 0.80),
    'mathematics-code-generation-02': ('mathematics', 0.70, 0.30),
    'physics-simulation-tool-orchestration-01': ('physics-simulation', 0.55, 0.45),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(raw: str | bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f'Nonfinite JSON number: {value}')

    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def put(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical_json(value) + b'\n')
        stream.flush()
        import os
        os.fsync(stream.fileno())


def load_judge(path: Path):
    """Load verified upstream definitions without writing a historical __pycache__."""
    name = '_little_pom_saved_scientific_judge'
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module  # dataclasses resolves the defining module here.
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def parse_response(text: str, expected_ids: list[int], judge) -> dict:
    """Upstream fence/prose tolerance, strengthened against malformed JSON/IDs."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Empty scientific verdict response')
    candidates = [m.group(1).strip() for m in re.finditer(
        r'```(?:json)?\s*(.*?)\s*```', text, flags=re.DOTALL | re.IGNORECASE)]
    brace = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    candidates.append(text.strip())
    objects = []
    for candidate in dict.fromkeys(candidates):
        try:
            obj = strict_json(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get('criteria_results'), list):
            if obj not in objects:
                objects.append(obj)
    if len(objects) != 1:
        raise ValueError('Expected one unambiguous scientific verdict object')
    obj = objects[0]
    validate('ScientificRubricVerdicts', obj)
    ids = [item['id'] for item in obj['criteria_results']]
    if any(type(cid) is not int for cid in ids) or len(ids) != len(set(ids)) or set(ids) != set(expected_ids):
        raise ValueError('Rubric IDs must occur exactly once, with no omissions or extras')
    if any(not item['explanation'].strip() for item in obj['criteria_results']):
        raise ValueError('Every scientific verdict needs a nonempty explanation')
    verdicts, error = judge.parse_verdicts(canonical_json(obj).decode(), expected_ids)
    if error or verdicts != {item['id']: item['verdict'] for item in obj['criteria_results']}:
        raise ValueError(f'Upstream verdict parser rejected response: {error}')
    return obj


def aggregate(packet: dict, verdict_object: dict, programmatic_score: float,
              programmatic_weight: float, judge_weight: float, judge) -> dict:
    expected = packet['expected_ids']
    obj = parse_response(canonical_json(verdict_object).decode(), expected, judge)
    verdicts = {int(cid): 'FAIL' for cid in packet['forced_fail_criteria']}
    verdicts.update({item['id']: item['verdict'] for item in obj['criteria_results']})
    if set(verdicts) != {item['id'] for item in packet['rubrics']}:
        raise ValueError('Merged verdicts do not cover the complete rubric')
    result = judge.aggregate(verdicts, packet['rubrics'])
    explanations = {item['id']: item['explanation'] for item in obj['criteria_results']}
    for cid, score in result['scores'].items():
        if cid in packet['forced_fail_criteria']:
            score['forced_by_artifact_preflight'] = packet['forced_fail_criteria'][cid]
        else:
            score['explanation'] = explanations[int(cid)]
    result.update(programmatic_score=programmatic_score, programmatic_weight=programmatic_weight,
                  judge_weight=judge_weight, full_score=round(
                      programmatic_weight * programmatic_score + judge_weight * result['score_fraction'], 4))
    return result


def budget_preflight(request: dict, catalog: dict) -> dict:
    """Reserve the full upstream output budget, including reasoning, without trimming.

    Text uses the upstream conservative 3 UTF-8 bytes/token estimate. Images use
    4096 tokens per 512px tile plus 4096 overhead, deliberately over-reserving
    ordinary vision tiles. These are estimates, not an unavailable Astra tokenizer
    guarantee; provider context rejection stays a failure, never a truncation.
    """
    validate_upstream_messages(request)
    if request['output_budget'] != OUTPUT_TOKENS or OUTPUT_TOKENS > catalog['maxTokens']:
        raise ValueError('The full upstream 128000-token output budget is unavailable')
    text_bytes = 0
    images = []
    resize_profile = catalog.get('inputLimits', {}).get('images', {}).get('resize', {})
    for index, block in enumerate(request['upstream_messages'][0]['content']):
        if block['type'] == 'text':
            text_bytes += len(block['text'].encode('utf-8'))
            continue
        source = block['source']
        raw = base64.b64decode(source['data'], validate=True)
        # The saved SCI figure contract is PNG. Do not silently reencode or resize.
        if source['media_type'] != 'image/png' or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
            raise ValueError('Saved scientific figures must be real PNG payloads')
        width, height = struct.unpack('>II', raw[16:24])
        if not width or not height or 'image' not in catalog.get('input', []):
            raise ValueError('Invalid image dimensions or missing model vision support')
        reservation = 4096 * (1 + math.ceil(width / 512) * math.ceil(height / 512))
        images.append({'block_index': index, 'sha256': digest(raw), 'bytes': len(raw),
                       'media_type': source['media_type'], 'width': width, 'height': height,
                       'input_token_reservation': reservation})
    estimated_input = math.ceil(text_bytes / 3) + 16_000 + sum(i['input_token_reservation'] for i in images)
    total = estimated_input + OUTPUT_TOKENS
    if type(catalog.get('contextWindow')) is not int or total > catalog['contextWindow']:
        raise ValueError('Full preserved input plus complete output reservation exceeds model context')
    return {'text_utf8_bytes': text_bytes, 'images': images, 'estimated_input_tokens': estimated_input,
            'output_token_reservation': OUTPUT_TOKENS, 'total_token_reservation': total,
            'catalog_context_window': catalog['contextWindow'], 'catalog_max_output_tokens': catalog['maxTokens'],
            'text_estimator': 'ceil(UTF8_bytes/3), upstream conservative convention',
            'image_estimator': '4096*(1+ceil(width/512)*ceil(height/512)) per PNG',
            'scaffolding_token_reservation': 16_000, 'exact_tokenizer_available': False,
            'catalog_image_resize_profile_not_applied': resize_profile,
            'image_policy': 'Pi conversation-ingestion resize profile is not an API acceptance limit; original bytes dispatched',
            'context_policy': 'no client truncation, resizing, or image dropping; server context behavior is not guaranteed'}


def make_request(task_id: str, packet: dict, trusted_guidance: str) -> dict:
    request = {'model': MODEL, 'stage': 'scientific-judge', 'task_id': task_id,
               'config': {'reasoning_effort': 'medium'}, 'instructions': trusted_guidance, 'packet': {}, 'history': [],
               'output_budget': packet['max_tokens'], 'upstream_messages': packet['messages']}
    validate_upstream_messages(request)
    return request


def prepare(source: Path, upstream: Path, backend) -> tuple[dict, list[dict]]:
    hashes = {}

    def read_source(path):
        raw = path.read_bytes()
        hashes[str(path.resolve())] = digest(raw)
        return raw

    report = strict_json(read_source(source / 'report.json'))
    if report.get('source_inventory_unchanged') is not True or report.get('changed_sources') != []:
        raise ValueError('Historical report does not attest unchanged sources')
    tasks = report.get('tasks', [])
    if len(tasks) != 5 or {task['task_id'] for task in tasks} != set(TASKS):
        raise ValueError('Exactly the five preserved scientific submissions are required')
    snapshot = strict_json(read_source(upstream.parent / 'snapshot_manifest.json'))
    manifest_files = snapshot['files_sha256']
    for name in ('launch.py', 'solver.py', 'verifier.py'):
        read_source(source / name)
    prepared = []
    for task in tasks:
        task_id = task['task_id']
        domain, programmatic_weight, judge_weight = TASKS[task_id]
        archive = source / 'tasks' / task_id / 'solver' / 'results.tar'
        if digest(read_source(archive)) != task['solver']['export']['archive_sha256']:
            raise ValueError(f'Preserved submission archive changed: {task_id}')
        task_source = upstream / 'tasks' / domain / task_id / 'tests'
        for name in ('llm_judge.py', 'rubric.json', 'judge_config.json', 'test.sh'):
            path = task_source / name
            raw = read_source(path)
            if digest(raw) != manifest_files[path.relative_to(upstream).as_posix()]:
                raise ValueError(f'Upstream snapshot mismatch: {task_id}/{name}')
        judge = load_judge(task_source / 'llm_judge.py')
        artifacts = source / 'tasks' / task_id / 'verification' / 'artifacts'
        packet = strict_json(read_source(artifacts / 'judge_packet.json'))
        preflight = strict_json(read_source(artifacts / 'artifact_preflight.json'))
        programmatic = strict_json(read_source(artifacts / 'programmatic.json'))
        verification = task['verification']
        score = programmatic.get('score')
        if (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1
                or programmatic.get('error') or verification['programmatic']['status'] != 'measured'
                or score != verification['programmatic']['score']
                or verification.get('delivered_files_unchanged') is not True):
            raise ValueError(f'Invalid preserved programmatic score: {task_id}')
        full = verification['full_benchmark']
        if (full['programmatic_weight'], full['judge_weight']) != (programmatic_weight, judge_weight):
            raise ValueError('Saved aggregation weights differ from upstream task-type weights')
        if (packet.get('status') != 'prepared_not_dispatched' or packet.get('local_model_calls') != 0
                or packet.get('model') != UPSTREAM_MODEL or packet.get('max_tokens') != OUTPUT_TOKENS):
            raise ValueError('Preserved upstream model/budget/dispatch contract mismatch')
        rubrics = judge.load_rubric(task_source / 'rubric.json')
        if packet['rubrics'] != rubrics:
            raise ValueError('Saved rubric differs from verified upstream rubric')
        cfg = judge.load_config(task_source / 'judge_config.json')
        judge.validate_rubric_inputs(cfg, rubrics)
        forced = {str(cid): failures for cid, failures in judge.rubric_artifact_failures(
            rubrics, preflight['failures']).items()}
        expected = [item['id'] for item in rubrics if str(item['id']) not in forced]
        if (packet['forced_fail_criteria'] != forced or preflight['forced_fail_criteria'] != forced
                or packet['expected_ids'] != expected or preflight['judgeable_criteria_ids'] != expected):
            raise ValueError('Forced artifact FAILs or expected IDs differ from upstream semantics')
        # Only trusted snapshot-owned instructions may change role. Never derive
        # instructions from a submitted artifact or from a guessed prompt prefix.
        if judge.GRADING_PROMPT.count(RUBRIC_MARKER) != 1:
            raise ValueError('Ambiguous trusted upstream grading template boundary')
        guidance = judge.GRADING_PROMPT.split(RUBRIC_MARKER)[0].format(task_context=cfg.task_context)
        request = make_request(task_id, packet, guidance) if expected else None
        if request is not None:
            backend.validate_request(request)
            budget = budget_preflight(request, backend.configuration()['catalog_model'])
        else:
            budget = {'output_token_reservation': 0, 'images': [], 'reason': 'all criteria forced FAIL; no model call'}
        prepared.append({'task_id': task_id, 'packet': packet, 'request': request, 'budget': budget,
                         'programmatic_score': score, 'programmatic_weight': programmatic_weight,
                         'judge_weight': judge_weight, 'judge': judge,
                         'packet_sha256': hashes[str((artifacts / 'judge_packet.json').resolve())],
                         'messages_sha256': digest(canonical_json(packet['messages'])),
                         'request_sha256': digest(canonical_json(request)),
                         'solver_export_archive_sha256': task['solver']['export']['archive_sha256']})
    for path in (Path(__file__), Path(__file__).parent / 'contracts' / 'schemas.py'):
        read_source(path)
    manifest = {'schema_version': '1.0', 'created_at': datetime.now(timezone.utc).isoformat(),
                'source_root': str(source), 'source_sha256': hashes,
                'upstream_model': UPSTREAM_MODEL, 'actual_model': MODEL,
                'model_override': True, 'mode': 'subscription',
                'role_adaptation': ROLE_ADAPTATION,
                'role_adaptation_policy': 'Trusted task-context/grading-guidance prefix promoted once; rubric, submissions, captions and images remain user content; original bytes reconstruct exactly',
                'comparability': 'Astra override of upstream gpt-5.6-sol; not an official upstream-model score',
                'backend': backend.configuration(), 'retry_policy': 'none; one dispatch per judgeable task',
                'solver_reruns': 0, 'source_packet_mutations': 0,
                'max_generation_dispatches': sum(bool(item['packet']['expected_ids']) for item in prepared),
                'aggregate_output_token_reservation': sum(OUTPUT_TOKENS for item in prepared if item['packet']['expected_ids']),
                'tasks': [{key: item[key] for key in ('task_id', 'budget', 'packet_sha256', 'messages_sha256',
                          'request_sha256', 'programmatic_score', 'programmatic_weight', 'judge_weight',
                          'solver_export_archive_sha256')} | {'upstream_packet_warnings': item['packet']['warnings']}
                         for item in prepared]}
    return manifest, prepared


def validate_envelope(raw: dict, request: dict) -> None:
    validate_upstream_messages(request)
    metadata = raw.get('_transport_metadata', {})
    if (raw.get('status') != 'completed' or raw.get('error') or raw.get('incomplete_details')
            or raw.get('model') not in (MODEL, MODEL.split('/', 1)[1])
            or metadata.get('generation_dispatches') != 1 or metadata.get('observation_complete') is not True):
        raise ValueError('Scientific provider response incomplete, failed, or wrong model')
    usage = raw.get('usage')
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                         for key in ('input_tokens', 'output_tokens')):
        raise ValueError('Scientific provider response lacks valid raw token usage')
    expected_input = [{'role': 'user', 'content': [
        {'type': 'input_text', 'text': block['text'][len(request['instructions']):] if index == 0 else block['text']}
        if block['type'] == 'text' else
        {'type': 'input_image', 'detail': 'auto',
         'image_url': f"data:{block['source']['media_type']};base64,{block['source']['data']}"}
        for index, block in enumerate(request['upstream_messages'][0]['content'])]}]
    sent = metadata.get('sent_payload', {})
    if (sent.get('input') != expected_input or sent.get('instructions') != request['instructions']
            or sent.get('max_output_tokens') != OUTPUT_TOKENS or 'truncation' in sent):
        raise ValueError('Provider payload did not preserve the full upstream prompt, images, and budget')
    reconstructed = sent['instructions'] + sent['input'][0]['content'][0]['text']
    if reconstructed.encode('utf-8') != request['upstream_messages'][0]['content'][0]['text'].encode('utf-8'):
        raise ValueError('Role adaptation failed exact original scientific text reconstruction')


def run(source: Path, upstream: Path, output: Path, *, live: bool, timeout: float) -> dict:
    source, upstream, output = source.resolve(), upstream.resolve(), output.resolve()
    if output == source or source in output.parents or output == upstream or upstream in output.parents:
        raise ValueError('Output must be outside historical run and upstream snapshot')
    output.mkdir(parents=True, exist_ok=False)
    try:
        backend = OmpSubscriptionBackend(model=MODEL, timeout=timeout)
        manifest, prepared = prepare(source, upstream, backend)
    except Exception as exc:
        put(output / 'report.json', {'status': 'preflight_failed', 'model': MODEL,
                                    'error': str(exc), 'generation_dispatches': 0})
        raise
    put(output / 'manifest.json', manifest)
    for item in prepared:
        put(output / item['task_id'] / 'request.json', item['request'])
        put(output / item['task_id'] / 'source_packet.json', item['packet'])
    if not live:
        result = {'status': 'preflight_only', 'generation_dispatches': 0,
                  'model': MODEL, 'manifest': 'manifest.json', 'all_five_preflight_passed': True}
        put(output / 'report.json', result)
        return result
    results = []
    for item in prepared:
        task_id = item['task_id']
        root = output / task_id
        start = time.monotonic()
        result = {'task_id': task_id, 'model': MODEL, 'upstream_model': UPSTREAM_MODEL,
                  'model_override': True, 'status': 'failed', 'full_score': None,
                  'role_adaptation': ROLE_ADAPTATION,
                  'usage_raw': None, 'generation_dispatches': 0, 'raw_response_available': False,
                  'packet_sha256': item['packet_sha256'], 'request_sha256': item['request_sha256']}
        try:
            if item['packet']['expected_ids']:
                # Recheck frozen sources before recording an irreversible single dispatch.
                for name, expected_hash in manifest['source_sha256'].items():
                    if digest(Path(name).read_bytes()) != expected_hash:
                        raise ValueError(f'Frozen grading source changed: {name}')
                put(root / 'dispatch_intent.json', {'task_id': task_id, 'request_sha256': item['request_sha256'],
                    'created_at': datetime.now(timezone.utc).isoformat(), 'max_dispatches': 1})
                result['generation_dispatches'] = None  # Ambiguous until a durable bridge observation.
                raw = backend.send(item['request'], 'ScientificRubricVerdicts', f'scientific-judge:{task_id}')
                put(root / 'raw_response.json', raw)
                result['usage_raw'] = raw.get('usage')
                result['raw_response_available'] = True
                result['generation_dispatches'] = raw.get('_transport_metadata', {}).get('generation_dispatches')
                result['transport_metadata'] = {key: value for key, value in raw.get('_transport_metadata', {}).items()
                                                if key != 'sent_payload'}
                validate_envelope(raw, item['request'])
                verdict_object = parse_response(raw.get('output_text'), item['packet']['expected_ids'], item['judge'])
            else:
                verdict_object = {'criteria_results': []}
                result['usage_raw'] = None
                result['generation_dispatches'] = 0
            scored = aggregate(item['packet'], verdict_object, item['programmatic_score'],
                               item['programmatic_weight'], item['judge_weight'], item['judge'])
            put(root / 'verdicts.json', verdict_object)
            result.update(scored, status='measured')
        except Exception as exc:
            result['error'] = str(exc)
            result['failure_policy'] = 'unmeasured, not a fabricated zero; no retry or fallback'
        result['latency_seconds'] = time.monotonic() - start
        put(root / 'result.json', result)
        results.append(result)
    complete = all(item['status'] == 'measured' for item in results)
    report = {'status': 'measured' if complete else 'incomplete', 'model': MODEL,
              'upstream_model': UPSTREAM_MODEL, 'model_override': True, 'tasks': results,
              'role_adaptation': ROLE_ADAPTATION,
              'full_scientific_benchmark_score': round(sum(item['full_score'] for item in results) / 5, 4) if complete else None,
              'aggregation': 'Macro mean of five full task scores; each task uses unchanged upstream weighted rubric and programmatic aggregation',
              'benchmark_binary_threshold': None, 'solver_reruns': 0, 'historical_sources_modified': False,
              'upstream_packet_warnings': {item['task_id']: item['packet']['warnings'] for item in prepared}}
    put(output / 'report.json', report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('runs/sci-agent-exam-executable'))
    parser.add_argument('--upstream', type=Path, default=Path('data/sci-agent-exam/upstream'))
    parser.add_argument('--output', type=Path, required=True, help='New immutable output directory; must not exist')
    parser.add_argument('--live', action='store_true', help='Permit exactly one Astra subscription dispatch per saved judgeable packet')
    parser.add_argument('--timeout', type=float, default=1800, help='Per-call hard timeout, seconds; no automatic retry')
    args = parser.parse_args(argv)
    try:
        result = run(args.source, args.upstream, args.output, live=args.live, timeout=args.timeout)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}))
        return 1
    print(json.dumps({'status': result['status'], 'output': str(args.output),
                      'full_scientific_benchmark_score': result.get('full_scientific_benchmark_score')}))
    return 0 if result['status'] in ('measured', 'preflight_only') else 1


if __name__ == '__main__':
    raise SystemExit(main())
