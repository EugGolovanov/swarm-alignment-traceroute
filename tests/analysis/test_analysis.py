import copy

import pytest

from little_pom.analysis import Analysis, expected_coverage, measurement_quality


def fixture():
    outcomes, records = [], []
    panels = []
    for condition in ('RoutedHistory', 'RandomHistory', 'Fresh'):
        for variant in ('def', 'valid'):
            for repeat in (1, 2):
                pid = f'{condition}-{variant}-{repeat}'
                reviews = [{'id': pid + f'-{i}', 'evidence_ids': ['e1']} for i in range(2)]
                outcomes.append({'id': pid, 'task_id': 't1', 'condition': condition, 'variant': variant,
                                 'repeat': repeat, 'status': 'complete', 'n_observed': 2, 'reviews': reviews})
                panels.append({'id': pid, 'condition': condition, 'variant': variant, 'repeat': repeat,
                               'reviewer_specs': [{'worker_id': 'w1'}, {'worker_id': 'w2'}]})
                for review in reviews:
                    records.append({'review_id': review['id'], 'significant_claim': variant == 'def',
                                    'normalized_type': 'alias', 'evidence_support': True,
                                    'decision_relevance': True, 'uncertain': False, 'lane': 'primary'})
    labels = {'final_labels': records, 'raw_primary': records, 'retest': copy.deepcopy(records[:2]),
              'calibration_a': [], 'calibration_b': [], 'queue_complete': True,
              'fp_report_ready': True, 'missingness': [], 'reviews': {}}
    mechanism = {'t1': {'target_defect_type': 'unmet_precondition', 'target_evidence_ids': ['e1'],
                       'eligible_worker_ids': ['w1', 'w2', 'w3', 'w4'], 'panels': panels,
                       'informed_workers': ['w1', 'w2'], 'informed_private_workers_valid': ['w1'],
                       'informed_private_workers_def': ['w2'], 'informed_private_workers_strict': [],
                       'exploitability': {'rho_high': 1}}}
    policy = {'equivalence_map': {'unmet_precondition': ['alias']}, 'bootstrap_replicates': 40, 'seed': 7}
    return outcomes, labels, mechanism, policy


def test_analytic_coverage_not_two_random_draw_estimate():
    assert expected_coverage(8, 2) == pytest.approx(13 / 28)
    assert expected_coverage(1, 1) is None
    with pytest.raises(ValueError):
        expected_coverage(2, 3)
    report = Analysis().compute(*fixture())
    views = report['coverage']['t1']
    assert set(views) == {'public', 'private_valid', 'private_def', 'strict'}
    assert views['private_def']['expected_random_coverage'] == .5
    assert report['primary_coverage_view'] == 'private_def'


def test_detection_requires_frozen_equivalence_support_and_relevance():
    args = fixture()
    args[1]['final_labels'][0]['evidence_support'] = False
    args[1]['final_labels'][1]['decision_relevance'] = False
    report = Analysis().compute(*args)
    assert report['panel_rows'][0]['detection'] == 0
    args[1]['final_labels'][0]['evidence_support'] = True
    assert Analysis().compute(*args)['panel_rows'][0]['detection'] == 1
    args[3]['equivalence_map'] = {'unmet_precondition': []}
    assert Analysis().compute(*args)['panel_rows'][0]['detection'] == 0


def test_partial_excluded_from_paired_episode_and_primary_denominator():
    args = fixture()
    args[0][0]['status'] = 'partial'
    args[0][0]['reviews'] = args[0][0]['reviews'][:1]
    args[0][0]['n_observed'] = 1
    report = Analysis().compute(*args)
    assert report['metrics']['RoutedHistory']['complete']['detection']['denominator'] == 1
    assert report['metrics']['RoutedHistory']['partial']['detection']['denominator'] == 1
    assert report['paired_episode_analysis']['detection']['n_episodes'] == 0
    assert report['paired_episode_analysis']['detection']['missing_episode_ids'] == ['t1']


def test_false_positive_requires_adjudication_and_complete_queue():
    args = fixture()
    review = args[1]['final_labels'][4]
    review['significant_claim'] = True
    review['false_positive'] = True
    assert Analysis().compute(*args)['panel_rows'][2]['false_positive'] is None
    review['lane'] = 'adjudication'
    assert Analysis().compute(*args)['panel_rows'][2]['false_positive'] == 1
    args[1]['fp_report_ready'] = False
    assert Analysis().compute(*args)['panel_rows'][2]['false_positive'] is None


def test_compute_is_pure_repeatable_and_episode_bootstrap_not_review_bootstrap():
    args = fixture()
    original = copy.deepcopy(args)
    report = Analysis().compute(*args)
    assert args == original
    assert report == Analysis().compute(*args)
    paired = report['paired_episode_analysis']['detection']
    assert paired['n_episodes'] == 1
    assert paired['unit'] == 'episode'
    assert paired['not_a_p_value'] is True
    assert report['full_pom_go_gates_applied'] is False


def test_missing_measurement_and_unstable_retest_are_inconclusive():
    args = fixture()
    args[1]['retest'][0]['significant_claim'] = False
    quality = measurement_quality(args[1], args[3])
    assert quality['status'] == 'measurement_inconclusive'
    assert 'retest_claim_agreement_below_threshold' in quality['reasons']
    args = fixture()
    args[1]['missingness'] = [{'lane': 'retest', 'packet_id': 'missing'}]
    assert measurement_quality(args[1], args[3])['status'] == 'measurement_inconclusive'


def test_astra_class_denominators_gate_insensitivity_not_small_sample():
    args = fixture()
    primary, astra = [], []
    for i in range(40):
        record = {'review_id': str(i), 'significant_claim': i < 20, 'normalized_type': 'none',
                  'evidence_support': False, 'decision_relevance': False}
        astra.append(record)
        primary.append({**record, 'significant_claim': not record['significant_claim']})
    args[1].update(raw_primary=primary, calibration_a=astra, retest=copy.deepcopy(primary), missingness=[])
    quality = measurement_quality(args[1], args[3])
    assert 'calibration_a_clear_insensitivity' in quality['reasons']
    diagnostic = quality['astra_cross_model_diagnostics']['calibration_a']
    assert diagnostic['n_astra_positive'] == diagnostic['n_astra_negative'] == 20
    assert diagnostic['balanced_accuracy_ci95'][1] < .70
    args[1]['calibration_a'] = astra[:4] + astra[20:24]
    assert 'calibration_a_clear_insensitivity' not in measurement_quality(args[1], args[3])['reasons']
