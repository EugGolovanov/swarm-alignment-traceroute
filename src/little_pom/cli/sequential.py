"""Explicit task-major exploratory run, with one global measurement sample."""
from __future__ import annotations

from little_pom.contracts import sha256_hash
from .pipeline import Pipeline
from .stages import StageError

TASK_STAGES = frozenset({'applicability-import', 'workers', 'cards-export', 'cards-import', 'panels', 'reviews'})


class TaskStages:
    """Namespaced stage manifests; scientific services retain their original contracts."""
    def __init__(self, base, task_id, previous_task=None):
        self.base = base
        self.task_id = task_id
        self.previous_task = previous_task
        self.config_hash = base.config_hash

    def name(self, stage):
        return f'{self.task_id}--{stage}' if stage in TASK_STAGES else stage

    def load(self, stage):
        return self.base.load(self.name(stage))

    def data(self, stage):
        return self.base.data(self.name(stage))

    def run(self, stage, dependencies, action, *, inputs=None):
        dependencies = tuple(self.name(name) for name in dependencies)
        if stage == 'workers':
            if self.previous_task:
                dependencies += (f'{self.previous_task}--reviews',)
            if self.base.config['mode'] == 'live':
                # Freeze every applicability decision before the first worker outcome.
                dependencies = tuple(dict.fromkeys((*dependencies, *(
                    f'{task_id}--applicability-import' for task_id in self.base.config['task_ids']))))
        return self.base.run(self.name(stage), dependencies, action, inputs=inputs)


class SequentialPipeline(Pipeline):
    def __init__(self, args):
        super().__init__(args)
        self.operational_freeze = 'freeze-exploratory'
        self.base_stages = self.stages
        self.active_task = None
        if self.config['d0_task_id'] is not None:
            raise StageError('Exploratory sequence must not designate a separate D0')
        if (self.root / 'manifests' / 'freeze.json').exists():
            raise StageError('Exploratory sequence requires a separate run directory')

    def _select(self, task_id):
        self.active_task = task_id
        if task_id is None:
            self.stages = self.base_stages
            return
        order = self.config['task_ids']
        if task_id not in order:
            raise StageError('Unknown task in exploratory sequence')
        index = order.index(task_id)
        self.stages = TaskStages(self.base_stages, task_id, order[index - 1] if index else None)

    def execute(self, stage):
        if stage == 'mock-e2e':
            raise StageError('Use sequential-run for the exploratory lifecycle')
        if stage == 'sequential-run':
            return self.run_sequence()
        if stage == 'subscription-export':
            from .exchange import export_exchange
            self._select(None)
            if not (self.root / 'exploratory_freeze_manifest.json').exists():
                # Pre-outcome applicability needs no models, images, or D0 bypass.
                self.execute('preflight')
                workflow = self._cards()
                for task_id, task in self._tasks().items():
                    if 'applicability' not in workflow.snapshot(task_id):
                        workflow.export('applicability', {'task': task,
                            'defect_types': list(self.config['equivalence_map']),
                            'assignment': self.config['defect_assignments'][task_id]})
            else:
                self._check_freeze()
            return export_exchange(self.root)
        if stage in ('preflight', 'freeze'):
            self._select(None)
            if stage == 'freeze' and not self.mock:
                if not self.stages.data(self.preflight_stage)['live_ready']:
                    raise StageError('Live readiness must pass before exploratory freeze')
            return super().execute(stage)
        if stage in ('eval-export', 'eval-import', 'analyze'):
            self._select(None)
            self._check_freeze()
            self._aggregate()
            result = super().execute(stage)
            return result
        task_id = getattr(self.args, 'task', None)
        if not task_id:
            raise StageError('Task stages require --task in the exploratory lifecycle')
        self._select(task_id)
        return super().execute(stage)

    def _aggregate(self):
        """Publish ordinary global inputs only after every task's reviews are sealed."""
        dependencies = tuple(f'{task_id}--reviews' for task_id in self.config['task_ids'])
        for dep in dependencies:
            self.base_stages.load(dep)
        def combine(stage):
            records = [self.base_stages.data(f'{tid}--{stage}') for tid in self.config['task_ids']]
            if stage == 'workers':
                return {'status': 'ok', 'tasks': {key: value for r in records for key, value in r['tasks'].items()}}
            if stage == 'panels':
                return {'status': 'ok', 'panels': [p for r in records for p in r['panels']],
                        'mechanisms': {key: value for r in records for key, value in r['mechanisms'].items()}}
            return {'status': 'ok', 'outcomes': [v for r in records for v in r['outcomes']],
                    'service_errors': [v for r in records for v in r['service_errors']],
                    'review_metadata': {key: value for r in records for key, value in r['review_metadata'].items()}}
        for stage in ('workers', 'panels', 'reviews'):
            self.base_stages.run(stage, dependencies, lambda stage=stage: combine(stage),
                                 inputs={'execution_order': 'task_major_through_reviews'})

    def run_sequence(self):
        self.execute('preflight')
        self.execute('freeze')
        old_task, old_phase = getattr(self.args, 'task', None), self.args.card_phase
        try:
            if not self.mock:
                self._require_responses(self._applicability_packets(), 'applicability')
                self.args.card_phase = 'applicability'
                for task_id in self.config['task_ids']:
                    self.args.task = task_id
                    self.execute('cards-import')
            for task_id in self.config['task_ids']:
                self.args.task = task_id
                self._select(task_id)
                self.args.card_phase = 'valid'
                self.execute('workers')
                packets = self.execute('cards-export')['packets']
                if not self.mock:
                    self._require_responses(packets, 'valid')
                    # Import valid cards and export defect packets without reseeding workers.
                    workflow = self._cards()
                    bundle = self._bundle()
                    for packet in packets:
                        snapshot = workflow.snapshot(task_id)
                        if 'valid' not in snapshot:
                            from .pipeline import require
                            require(workflow.import_response(packet['id'], bundle[packet['id']]))
                        if 'def' not in snapshot:
                            defects = workflow.export('def', {'task_id': task_id})
                            self._require_responses(defects, 'def')
                self.execute('cards-import')
                self.execute('panels')
                self.execute('reviews')
            self._select(None)
            self.execute('eval-export')
            self.execute('eval-import')
            return self.execute('analyze')
        except StageError as exc:
            from .exchange import export_exchange
            exchange = export_exchange(self.root)
            return {'status': 'incomplete', 'active_task': self.active_task,
                    'error': {'code': 'sequence_paused', 'message': str(exc)}, 'exchange': exchange}
        finally:
            self.args.task, self.args.card_phase = old_task, old_phase
            self._select(None)

    def _require_responses(self, packets, stage):
        bundle = self._bundle() if self.args.responses else {}
        workflow = self._cards()
        for packet in packets:
            accepted = workflow.snapshot(packet['task_id']).get(stage)
            if (accepted and packet['id'] in bundle
                    and accepted.get('raw_response_sha256') != sha256_hash(bundle[packet['id']])):
                raise StageError('Frozen subscription response changed')
        pending = [p['id'] for p in packets if stage not in workflow.snapshot(p['task_id']) and p['id'] not in bundle]
        if pending:
            raise StageError(f'Subscription {stage} responses required: {pending}')
