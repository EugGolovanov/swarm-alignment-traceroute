"""Complete-panel metrics and episode-level paired pilot diagnostics."""
from __future__ import annotations

from collections import Counter
from math import comb
from random import Random
from statistics import mean

from little_pom.contracts import derive_seed, sha256_hash


def rate(values):
    observed = [v for v in values if v is not None]
    return {'numerator': sum(observed), 'denominator': len(observed),
            'rate': mean(observed) if observed else None, 'missing': len(values) - len(observed)}


def expected_coverage(pool_size: int, informed_count: int) -> float | None:
    if not 0 <= informed_count <= pool_size:
        raise ValueError('Informed count must be contained in the eligible pool')
    if pool_size < 2:
        return None
    return 1 - comb(pool_size - informed_count, 2) / comb(pool_size, 2)


def coverage_views(reference):
    if reference.get('status') == 'unavailable':
        return {view: {'expected_random_coverage': None, 'routed_coverage': None,
                       'missing': True, 'reason': reference['reason']}
                for view in ('public', 'private_valid', 'private_def', 'strict')}
    pool = set(reference['eligible_worker_ids'])
    result = {}
    for name, key in [('public', 'informed_workers'), ('private_valid', 'informed_private_workers_valid'),
                      ('private_def', 'informed_private_workers_def'), ('strict', 'informed_private_workers_strict')]:
        informed = set(reference[key]) & pool
        realized = []
        for panel in reference['panels']:
            workers = {s['worker_id'] for s in panel['reviewer_specs'] if s.get('worker_id')}
            realized.append({'panel_id': panel['id'], 'condition': panel['condition'],
                             'variant': panel['variant'], 'repeat': panel['repeat'],
                             'coverage': int(bool(workers & informed))})
        routed = [r['coverage'] for r in realized if r['condition'] == 'RoutedHistory']
        expected = expected_coverage(len(pool), len(informed))
        result[name] = {'pool_size': len(pool), 'informed_count': len(informed),
                        'expected_random_coverage': expected,
                        'routed_coverage': mean(routed) if routed else None,
                        'routed_gain_over_expected': mean(routed) - expected if routed and expected is not None else None,
                        'realized_panels': realized}
    return result


def _class_interval(successes, total):
    """Exact binomial bounds at 97.5%; two classes give >=95% joint coverage."""
    if not total:
        return [None, None]
    def cdf(k, probability):
        return sum(comb(total, j) * probability ** j * (1 - probability) ** (total - j)
                   for j in range(k + 1))
    def solve(k, target):
        lo, hi = 0.0, 1.0
        for _ in range(64):
            middle = (lo + hi) / 2
            if cdf(k, middle) > target:
                lo = middle
            else:
                hi = middle
        return (lo + hi) / 2
    return [0.0 if successes == 0 else solve(successes - 1, .9875),
            1.0 if successes == total else solve(successes, .0125)]


def _paired_bootstrap(rows, seed, replicates):
    if not rows:
        return {'n_episodes': 0, 'mean_difference': None, 'ci95': [None, None]}
    values = [r['difference'] for r in rows]
    random = Random(seed)
    draws = sorted(mean(random.choices(values, k=len(values))) for _ in range(replicates))
    return {'n_episodes': len(values), 'mean_difference': mean(values),
            'ci95': [draws[int(.025 * (replicates - 1))], draws[int(.975 * (replicates - 1))]],
            'unit': 'episode', 'replicates': replicates, 'not_a_p_value': True, 'episodes': rows}


def measurement_quality(labels, policy):
    axes = ['significant_claim', 'normalized_type', 'evidence_support', 'decision_relevance']
    primary = {r['review_id']: r for r in labels.get('raw_primary', [])}
    retest = labels.get('retest', labels.get('raw_retest', []))
    agreement = {axis: rate([int(r[axis] == primary[r['review_id']][axis]) for r in retest
                            if r['review_id'] in primary]) for axis in axes}
    calibration = {}
    for lane in ('calibration_a', 'calibration_b'):
        records = labels.get(lane, labels.get('raw_' + lane, []))
        paired = [(primary[r['review_id']], r) for r in records if r['review_id'] in primary]
        positives = [(p, r) for p, r in paired if r['significant_claim']]
        negatives = [(p, r) for p, r in paired if not r['significant_claim']]
        tp = sum(p['significant_claim'] for p, _ in positives)
        tn = sum(not p['significant_claim'] for p, _ in negatives)
        ba = (tp / len(positives) + tn / len(negatives)) / 2 if positives and negatives else None
        positive_ci, negative_ci = _class_interval(tp, len(positives)), _class_interval(tn, len(negatives))
        ci = [(positive_ci[i] + negative_ci[i]) / 2 for i in (0, 1)] if positives and negatives else [None, None]
        calibration[lane] = {'balanced_accuracy': ba, 'balanced_accuracy_ci95': ci,
            'ci_method': 'bonferroni_simultaneous_exact_binomial_class_bounds',
            'n_astra_positive': len(positives), 'n_astra_negative': len(negatives),
            'confusion_matrix': {'tp': tp, 'fn': len(positives) - tp, 'tn': tn, 'fp': len(negatives) - tn},
            'agreement': {axis: rate([int(p[axis] == r[axis]) for p, r in paired]) for axis in axes},
            'insensitivity_gate_eligible': len(positives) >= 8 and len(negatives) >= 8}
    reasons = []
    claim = agreement['significant_claim']['rate']
    if claim is None:
        reasons.append('missing_retest_claim_denominator')
    elif claim < policy.get('retest_min_agreement', .70):
        reasons.append('retest_claim_agreement_below_threshold')
    for lane, diagnostic in calibration.items():
        if (diagnostic['insensitivity_gate_eligible'] and diagnostic['balanced_accuracy'] < .60
                and diagnostic['balanced_accuracy_ci95'][1] < .70):
            reasons.append(f'{lane}_clear_insensitivity')
    if not labels.get('queue_complete', False):
        reasons.append('mandatory_adjudication_incomplete')
    if labels.get('missingness'):
        reasons.append('frozen_measurement_responses_missing')
    return {'status': 'measurement_inconclusive' if reasons else 'measurement_usable', 'reasons': reasons,
            'retest_agreement': agreement, 'astra_cross_model_diagnostics': calibration,
            'denominators': labels.get('denominators', {}),
            'human_validated': False, 'correlated_model_errors_possible': True}


class Analysis:
    def compute(self, panel_outcomes, labels, mechanism, policy):
        if not isinstance(panel_outcomes, list) or not all(isinstance(x, dict) for x in panel_outcomes):
            raise ValueError('panel_outcomes must be a list of objects')
        if not all(isinstance(x, dict) for x in (labels, mechanism, policy)):
            raise ValueError('labels, mechanism and policy must be objects')
        equivalence = policy.get('equivalence_map')
        if not isinstance(equivalence, dict):
            raise ValueError('Frozen equivalence_map is required')
        final = {r['review_id']: r for r in labels.get('final_labels', [])}
        primary = {r['review_id']: r for r in labels.get('raw_primary', [])}
        review_records = dict(labels.get('reviews', {}))
        rows = []
        for outcome in panel_outcomes:
            task_id = outcome['task_id']
            reference = mechanism[task_id]
            target = reference['target_defect_type']
            acceptable = set(equivalence.get(target, [target])) | {target}
            reviews = outcome.get('reviews', [])
            review_ids = [r['id'] if isinstance(r, dict) else r for r in reviews]
            for r in reviews:
                if isinstance(r, dict):
                    review_records.setdefault(r['id'], r)
            observed_labels = [final[r] for r in review_ids if r in final]
            fully_measured = len(observed_labels) == len(review_ids) and bool(review_ids)
            variant = outcome['variant']
            detection = None
            fp = None
            if fully_measured and variant == 'def':
                detection = int(any(r['significant_claim'] and r['normalized_type'] in acceptable
                                    and r['evidence_support'] and r['decision_relevance'] and not r['uncertain']
                                    for r in observed_labels))
            if fully_measured and variant == 'valid' and labels.get('fp_report_ready', False):
                positives = [r for r in observed_labels if r['significant_claim']
                             or primary.get(r['review_id'], {}).get('significant_claim', False)]
                if all('false_positive' in r and r.get('lane') == 'adjudication' for r in positives):
                    fp = int(any(r.get('false_positive', False) for r in observed_labels))
            targets = set(reference['target_evidence_ids'])
            use = [int(bool(targets & set(review_records[r].get('evidence_ids', []))))
                   for r in review_ids if r in review_records]
            rows.append({'panel_id': outcome.get('panel_id', outcome['id']), 'task_id': task_id,
                         'condition': outcome['condition'], 'variant': variant, 'repeat': outcome['repeat'],
                         'status': outcome['status'], 'detection': detection, 'false_positive': fp,
                         'evidence_use': rate(use), 'n_reviewers_observed': outcome.get('n_observed', len(review_ids)),
                         'measurement_complete': fully_measured})
        metrics = {}
        for condition in ('Fresh', 'RandomHistory', 'RoutedHistory'):
            metrics[condition] = {}
            for status in ('complete', 'partial', 'incomplete'):
                subset = [r for r in rows if r['condition'] == condition and r['status'] == status]
                metrics[condition][status] = {
                    'panels': len(subset), 'detection': rate([r['detection'] for r in subset if r['variant'] == 'def']),
                    'false_positive': rate([r['false_positive'] for r in subset if r['variant'] == 'valid']),
                    'evidence_use': {'numerator': sum(r['evidence_use']['numerator'] for r in subset),
                                     'denominator': sum(r['evidence_use']['denominator'] for r in subset)}}
        paired = {}
        seed = policy.get('seed', 0)
        replicates = policy.get('bootstrap_replicates', 2000)
        if not isinstance(replicates, int) or replicates < 1:
            raise ValueError('bootstrap_replicates must be positive')
        for metric, variant in [('detection', 'def'), ('false_positive', 'valid')]:
            episodes, missing = [], []
            for task_id in sorted(mechanism):
                cells = {c: [r for r in rows if r['task_id'] == task_id and r['condition'] == c
                              and r['variant'] == variant] for c in ('RoutedHistory', 'RandomHistory')}
                complete = all(len(cell) == 2 and {r['repeat'] for r in cell} == {1, 2}
                               and all(r['status'] == 'complete' and r[metric] is not None for r in cell)
                               for cell in cells.values())
                if complete:
                    routed, random = (mean(r[metric] for r in cells[c]) for c in ('RoutedHistory', 'RandomHistory'))
                    episodes.append({'task_id': task_id, 'routed': routed, 'random': random, 'difference': routed - random})
                else:
                    missing.append(task_id)
            paired[metric] = {**_paired_bootstrap(episodes, derive_seed(seed, 'analysis_bootstrap', metric), replicates),
                              'missing_episode_ids': missing, 'total_episodes': len(mechanism)}
        quality = measurement_quality(labels, policy)
        return {'schema_version': '1.0', 'id': sha256_hash([panel_outcomes, labels, mechanism, policy]),
                'pilot_status': quality['status'] if quality['status'] == 'measurement_inconclusive' else 'feasibility_pilot_reported',
                'full_pom_go_gates_applied': False, 'scientific_confirmation': False,
                'primary_coverage_view': 'private_def', 'coverage': {k: coverage_views(v) for k, v in mechanism.items()},
                'exploitability': {k: v.get('exploitability', {}) for k, v in mechanism.items()},
                'metrics': metrics, 'panel_rows': rows, 'paired_episode_analysis': paired, 'measurement_quality': quality,
                'missingness': {'panel_status_counts': dict(Counter(r['status'] for r in rows)),
                               'panels_expected': len(mechanism) * 12, 'panels_observed': len(rows),
                               'panels_not_created': len(mechanism) * 12 - len(rows),
                               'reviewers_observed_distribution': dict(Counter(str(r['n_reviewers_observed']) for r in rows)),
                               'unmeasured_panels': sum(not r['measurement_complete'] for r in rows),
                               'measurement': labels.get('missingness', {})}}
