"""Offline scientific grading checks: no OAuth, processes, or model dispatch."""
import base64
import copy
import json
from pathlib import Path
import struct

import pytest

from little_pom import scientific_judge as sci
from little_pom.runtime.subscription import validate_upstream_messages


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / 'data/sci-agent-exam/upstream'
GUIDANCE = 'Trusted original grading guidance.\n\n'


@pytest.fixture
def judge():
    path = UPSTREAM / 'tasks/bioinformatics/bioinformatics-error-diagnosis-01/tests/llm_judge.py'
    if not path.exists():
        pytest.skip('Preserved upstream snapshot not installed')
    return sci.load_judge(path)


def verdict(cid=1, verdict='PASS'):
    return {'id': cid, 'verdict': verdict, 'explanation': 'Concrete artifact evidence.'}


def packet():
    return {'max_tokens': 128000, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': GUIDANCE + sci.RUBRIC_MARKER + 'Original rubric and submitted artifacts, unchanged.'}]}]}


def catalog():
    return {'maxTokens': 128000, 'contextWindow': 272000, 'input': ['text', 'image'],
            'inputLimits': {'images': {'resize': {'maxWidth': 2000, 'maxHeight': 2000, 'maxBytes': 4718592}}}}


def test_upstream_fences_and_prose_preserved_without_relaxing_ids(judge):
    obj = {'criteria_results': [verdict(1), verdict(2, 'FAIL')]}
    assert sci.parse_response('Rationale.\n```json\n' + json.dumps(obj) + '\n```', [1, 2], judge) == obj


@pytest.mark.parametrize('obj', [
    {'criteria_results': [verdict(1), verdict(1)]},
    {'criteria_results': []},
    {'criteria_results': [verdict(2)]},
    {'criteria_results': [verdict(True)]},
    {'criteria_results': [verdict('1')]},
    {'criteria_results': [verdict(1.0)]},
    {'criteria_results': [verdict(1, 'pass')]},
    {'criteria_results': [verdict(1, 'MAYBE')]},
    {'criteria_results': [{'id': 1, 'verdict': 'PASS'}]},
    {'criteria_results': [verdict() | {'explanation': '   '}]},
    {'criteria_results': [verdict()], 'score': 1},
])
def test_malformed_scientific_verdicts_fail_closed(judge, obj):
    with pytest.raises(ValueError):
        sci.parse_response(json.dumps(obj), [1], judge)


def test_duplicate_json_keys_nonfinite_and_ambiguous_objects_fail_closed(judge):
    for raw in ('{"criteria_results":[],"criteria_results":[' + json.dumps(verdict()) + ']}',
                '{"criteria_results":[],"score":NaN}',
                '```json\n' + json.dumps({'criteria_results': [verdict()]}) + '\n```\n```json\n' +
                json.dumps({'criteria_results': [verdict(1, 'FAIL')]}) + '\n```'):
        with pytest.raises(ValueError):
            sci.parse_response(raw, [1], judge)


def test_forced_fail_denominator_and_full_precision_programmatic_aggregation(judge):
    saved = {'expected_ids': [1], 'forced_fail_criteria': {'2': [{'path': 'figure.png', 'reason': 'missing'}]},
             'rubrics': [{'id': 1, 'weight': 2}, {'id': 2, 'weight': 3}]}
    result = sci.aggregate(saved, {'criteria_results': [verdict()]}, 0.525256, 0.7, 0.3, judge)
    assert result['score_fraction'] == 0.4
    assert result['awarded_weight'] == 2 and result['total_weight'] == 5
    assert result['full_score'] == round(0.7 * 0.525256 + 0.3 * 0.4, 4)
    assert result['scores']['2']['verdict'] == 'FAIL'
    assert result['scores']['2']['forced_by_artifact_preflight'] == saved['forced_fail_criteria']['2']


def test_all_forced_fail_needs_no_model_verdict(judge):
    saved = {'expected_ids': [], 'forced_fail_criteria': {'1': [{'path': 'x', 'reason': 'missing'}]},
             'rubrics': [{'id': 1, 'weight': 5}]}
    assert sci.aggregate(saved, {'criteria_results': []}, 1, 0.65, 0.35, judge)['full_score'] == 0.65


def test_full_output_and_real_image_budget_no_truncation():
    saved = packet()
    # Metadata fixture only; no production dispatch, decoding, or scientific claim.
    image = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 2482, 1197)
    saved['messages'][0]['content'] += [{'type': 'text', 'text': 'Figure caption.'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                    'data': base64.b64encode(image).decode()}}]
    request = sci.make_request('one', saved, GUIDANCE)
    original = copy.deepcopy(request)
    result = sci.budget_preflight(request, catalog())
    assert request == original
    assert result['output_token_reservation'] == 128000
    assert result['images'][0]['sha256'] == sci.digest(image)
    assert result['images'][0]['width'] == 2482  # Exceeds the optional Pi history resize profile.
    assert result['catalog_image_resize_profile_not_applied'] == catalog()['inputLimits']['images']['resize']
    assert result['exact_tokenizer_available'] is False
    with pytest.raises(ValueError, match='full upstream'):
        sci.budget_preflight(request, catalog() | {'maxTokens': 32768})
    with pytest.raises(ValueError, match='context'):
        sci.budget_preflight(request, catalog() | {'contextWindow': 130000})


@pytest.mark.parametrize('mutate', [
    lambda req: req.update(instructions='Added instruction'),
    lambda req: req.update(history=[{'role': 'user', 'content': 'extra'}]),
    lambda req: req.update(stage='workers'),
    lambda req: req['upstream_messages'][0]['content'].append({'type': 'image', 'source': {'type': 'url', 'url': 'https://image'}}),
    lambda req: req['upstream_messages'][0]['content'].append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'garbage'}}),
])
def test_preserved_messages_cannot_gain_context_or_fake_images(mutate):
    request = sci.make_request('one', packet(), GUIDANCE)
    mutate(request)
    with pytest.raises(ValueError):
        validate_upstream_messages(request)


def test_immutable_output_cannot_overwrite_or_nest_under_source(tmp_path):
    sci.put(tmp_path / 'x.json', {'original': True})
    with pytest.raises(FileExistsError):
        sci.put(tmp_path / 'x.json', {'original': False})
    source = tmp_path / 'old-run'
    with pytest.raises(ValueError, match='outside historical'):
        sci.run(source, tmp_path / 'upstream', source / 'new', live=True, timeout=1)
    assert json.loads((tmp_path / 'x.json').read_text()) == {'original': True}


def test_saved_five_preflight_without_backend_dispatch():
    source = ROOT / 'runs/sci-agent-exam-executable'
    if not (source / 'report.json').exists():
        pytest.skip('Historical saved run not installed')

    class LocalCatalogOnly:
        def validate_request(self, request):
            validate_upstream_messages(request)

        def configuration(self):
            return {'catalog_model': catalog()}

    manifest, prepared = sci.prepare(source, UPSTREAM, LocalCatalogOnly())
    assert len(prepared) == 5
    assert manifest['max_generation_dispatches'] == 5
    assert manifest['aggregate_output_token_reservation'] == 640000
    assert manifest['model_override'] is True
    assert sum(len(item['budget']['images']) for item in prepared) == 3
    assert all(item['request']['upstream_messages'] == item['packet']['messages'] for item in prepared)

@pytest.mark.parametrize('change', [
    {'status': 'incomplete'}, {'error': {'code': 'unsupported_max_output_tokens'}},
    {'model': 'gpt-5.6-sol'}, {'usage': None}, {'usage': {'input_tokens': -1, 'output_tokens': 3}},
    {'incomplete_details': {'reason': 'max_output_tokens'}},
])
def test_incomplete_wrong_model_and_invalid_usage_cannot_pass(change):
    request = sci.make_request('one', packet(), GUIDANCE)
    raw = {'status': 'completed', 'model': sci.MODEL, 'usage': {'input_tokens': 10, 'output_tokens': 3},
           '_transport_metadata': {'generation_dispatches': 1, 'observation_complete': True,
               'sent_payload': {'instructions': GUIDANCE, 'max_output_tokens': 128000,
                   'input': [{'role': 'user', 'content': [{'type': 'input_text',
                       'text': request['upstream_messages'][0]['content'][0]['text'][len(GUIDANCE):]}]}]}}}
    sci.validate_envelope(raw, request)
    with pytest.raises(ValueError):
        sci.validate_envelope(raw | change, request)


def test_description_substitution_cannot_pass_provider_payload_audit():
    request = sci.make_request('one', packet(), GUIDANCE)
    raw = {'status': 'completed', 'model': sci.MODEL, 'usage': {'input_tokens': 10, 'output_tokens': 3},
           '_transport_metadata': {'generation_dispatches': 1, 'observation_complete': True,
               'sent_payload': {'instructions': GUIDANCE, 'max_output_tokens': 128000,
                                'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'Summary only'}]}]}}}
    with pytest.raises(ValueError, match='preserve'):
        sci.validate_envelope(raw, request)

@pytest.mark.parametrize('guidance', ['', 'Untrusted alternate guidance.', GUIDANCE[:-1], GUIDANCE + sci.RUBRIC_MARKER])
def test_guidance_split_rejects_empty_changed_or_rubric_promoting_prefix(guidance):
    with pytest.raises(ValueError):
        sci.make_request('one', packet(), guidance)


def test_ambiguous_guidance_split_rejected():
    saved = packet()
    saved['messages'][0]['content'][0]['text'] += '\n' + sci.RUBRIC_MARKER
    with pytest.raises(ValueError, match='unambiguous'):
        sci.make_request('one', saved, GUIDANCE)
