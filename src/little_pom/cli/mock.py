"""Synthetic external transport responses, never synthetic service outputs or scores."""

SYNTHETIC_INPUT = 'Synthetic prerequisite observation: calibration is missing; repeat measurement before deployment.'


def model_response(request, schema_id, execution_id):
    packet = request['packet']
    identity = {'schema_version': '1.0', 'id': execution_id}
    if schema_id == 'WorkerTurn':
        task_id = request['task_id']
        has_tool = any(entry.get('role') == 'tool' for entry in request['history'])
        return {**identity, 'task_id': task_id,
                'tool_requests': [] if has_tool else [{'operation': 'read', 'path': 'input.txt'}],
                'result': 'Prerequisite inspection found calibration missing; repeat measurement before deployment.' if has_tool else '',
                'limitations': [], 'evidence': [{'source': 'input.txt', 'fragment': SYNTHETIC_INPUT}] if has_tool else [],
                'done': has_tool}
    if schema_id == 'ProposalCard':
        evidence = list(dict.fromkeys(e['evidence_id'] for h in packet['handoffs'] for e in h['evidence']))
        return {**identity, 'task_id': request['task_id'],
                'proposed_action': 'Repeat measurement after calibration and inspect the prerequisite.',
                'uncertainty': 'Calibration must be checked before deployment.',
                'grounds': ['Inspect the measurement prerequisite before deployment.'],
                'expected_decision_impact': 'Choose whether a repeat measurement is justified.',
                'scale_budget': 'One bounded measurement.', 'preconditions': ['Calibration is confirmed.'],
                'evidence_ids': evidence}
    if schema_id == 'Review':
        context = packet.get('worker_context', {})
        evidence = context.get('evidence', [])
        action = packet['card']['text'].casefold()
        defect_claim = bool(evidence) and any(text in action for text in
                                            ('without calibration', 'ignore', 'unconditionally', 'without further validation'))
        return {**identity, 'status': 'claim' if defect_claim else 'no_claim',
                'claim': 'The calibration prerequisite is missing, so deployment must wait.' if defect_claim else 'No significant defect is established by the supplied context.',
                'evidence_ids': [e['evidence_id'] for e in evidence] if defect_claim else [],
                'decision_relevance_text': 'Check calibration before deployment.' if defect_claim else 'No change is justified.'}
    raise ValueError(f'No explicit mock response for schema {schema_id}')


def subscription_response(request):
    from little_pom.measurement import MockSubscriptionBackend
    packet = request['packet']
    positive = 'prerequisite is missing' in packet['claim'].casefold()
    label = {'schema_version': '1.0', 'significant_claim': positive,
             'normalized_type': 'unmet_precondition' if positive else 'none',
             'evidence_support': positive and bool(packet['cited_evidence']),
             'decision_relevance': positive, 'uncertain': False,
             'rationale': 'Synthetic offline response to the supplied claim and cited fragment.',
             'false_positive': False}
    return MockSubscriptionBackend(label).respond(request)
