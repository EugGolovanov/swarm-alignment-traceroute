"""Consumer-visible evidence transport failures, not scientific grading tests."""
import copy

import pytest

from little_pom.evidence import EvidenceReferenceError, EvidenceRegistry, EvidenceScope
from little_pom.reviews import BlindingError, ReviewerPacketBuilder, ReviewRunner
from little_pom.runtime import ArtifactStore


def registered(tmp_path):
    sources = tmp_path / 'inputs'
    sources.mkdir()
    (sources / 'a.txt').write_text('shared observation\nsource A context')
    (sources / 'b.txt').write_text('shared observation\nsource B context')
    (sources / 'copy.txt').write_text('shared observation\nsource A context')
    registry = EvidenceRegistry(tmp_path / 'registry', 'task', sources)
    first = registry.register('a.txt', 'shared observation', 'worker-a')
    second = registry.register('b.txt', 'shared observation', 'worker-b')
    duplicate = registry.register('copy.txt', 'shared observation', 'worker-c')
    return registry, first, second, duplicate


def test_same_fragment_keeps_source_identity_and_duplicate_source_observations(tmp_path):
    registry, first, second, duplicate = registered(tmp_path)
    assert first['evidence_id'] != second['evidence_id']
    assert first['evidence_id'] == duplicate['evidence_id']
    scope = EvidenceScope(registry.evidence(), provenance=registry.provenance())
    assert len(scope.aliases) == 2
    assert len(scope.manifest()['provenance']) == 3
    assert scope.resolve([scope.reverse[first['evidence_id']]]) == [first['evidence_id']]
    assert EvidenceScope(list(reversed(registry.evidence()))).aliases == scope.aliases


@pytest.mark.parametrize('references', [['P1'], ['E1'], ['[[ev:01]]'], ['[[ev:999]]'], ['a' * 62], ['a' * 64], ['[[ev:1]]', '[[ev:1]]'], '[[ev:1]]', [None]])
def test_malformed_nonexistent_and_hash_references_are_contract_failures(tmp_path, references):
    registry, first, _, _ = registered(tmp_path)
    scope = EvidenceScope([first], provenance=registry.provenance('worker-a'))
    with pytest.raises(EvidenceReferenceError):
        scope.resolve(references)


def test_public_packet_rejects_inaccessible_fragment(tmp_path):
    _, first, _, _ = registered(tmp_path)
    with pytest.raises(BlindingError, match='not visible'):
        ReviewerPacketBuilder().build({'text': 'Task'}, {'text': ''}, {'text': 'Action'}, public_evidence=[first])
    packet = ReviewerPacketBuilder().build({'text': 'Task'}, {'text': first['fragment']}, {'text': 'Action'}, public_evidence=[first])
    assert packet['evidence'] == [{'evidence_id': first['evidence_id'], 'text': first['fragment']}]


@pytest.mark.parametrize('status', ['claim', 'no_claim'])
def test_unknown_reviewer_reference_is_missingness_not_a_scientific_miss(tmp_path, status):
    _, first, second, _ = registered(tmp_path)
    packet = ReviewerPacketBuilder(packet_id='packet').build(
        {'text': 'Task'}, {'text': first['fragment']}, {'text': 'Action'}, public_evidence=[first])
    observed_requests = []

    class Client:
        def call(self, request, schema, execution_id):
            observed_requests.append(copy.deepcopy(request))
            return {'status': 'ok', 'data': {'schema_version': '1.0', 'id': 'packet',
                    'status': status, 'claim': 'No firm conclusion.', 'evidence_ids': ['[[ev:2]]'],
                    'decision_relevance_text': 'Uncertain.'}, 'error': None}

    result = ReviewRunner()._execute({'id': 'execution', 'task_id': 'task'}, packet, Client(), ArtifactStore(tmp_path / 'run'))
    assert result['status'] == 'incomplete'
    assert result['data'] is None
    assert result['error']['code'] == 'evidence_reference_contract'
    assert observed_requests[0]['packet']['evidence'][0]['evidence_id'] == '[[ev:1]]'
    assert second['evidence_id'] not in str(observed_requests)


def test_reviewer_short_reference_restores_canonical_identity_and_prose(tmp_path):
    _, first, _, _ = registered(tmp_path)
    packet = ReviewerPacketBuilder(packet_id='packet').build(
        {'text': 'Task'}, {'text': first['fragment']}, {'text': 'Action'}, public_evidence=[first])

    class Client:
        def call(self, request, schema, execution_id):
            return {'status': 'ok', 'data': {'schema_version': '1.0', 'id': 'packet',
                    'status': 'claim', 'claim': 'E1 and E2 energy levels differ; see [[ev:1]].', 'evidence_ids': ['[[ev:1]]'],
                    'decision_relevance_text': 'Inspect [[ev:1]] before the E1 electric dipole transition.'}, 'error': None}

    store = ArtifactStore(tmp_path / 'run')
    result = ReviewRunner()._execute({'id': 'execution', 'task_id': 'task'}, packet, Client(), store)
    assert result['status'] == 'ok'
    assert result['data']['evidence_ids'] == [first['evidence_id']]
    assert result['data']['claim'] == f"E1 and E2 energy levels differ; see {first['evidence_id']}."
    assert result['data']['decision_relevance_text'] == f"Inspect {first['evidence_id']} before the E1 electric dipole transition."
    assert store.read('reviews/evidence/execution.json')['aliases'] == {'[[ev:1]]': first['evidence_id']}


def test_reserved_references_do_not_rewrite_scientific_tokens_or_fragments():
    fragment = "Energy E1 is 3 eV; E2 is 5 eV. The literal marker [[ev:1]] appears in this source."
    item = {"evidence_id": "a" * 64, "fragment": fragment}
    scope = EvidenceScope([item])
    assert scope.restore({"explanation": "Energy E1 is 3 eV; E2 is 5 eV."}) == {
        "explanation": "Energy E1 is 3 eV; E2 is 5 eV."}
    projected = scope.project(item)
    assert projected == {"evidence_id": "[[ev:1]]", "fragment": fragment}
    assert scope.restore(projected) == item
