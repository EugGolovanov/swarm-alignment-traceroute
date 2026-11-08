"""Stage orchestration over the real contracts, runtime and scientific services."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from little_pom.contracts import canonical_json, sha256_hash
from little_pom.contracts.config import ConfigFreeze, default_config_path, load_config
from little_pom.runtime import ArtifactStore, FreezeStore, Ledger, MockBackend, SwarmClient

from .stages import StageError, StageStore


def require(result):
    if result['status'] != 'ok':
        raise StageError(f"Service incomplete: {result.get('error')}")
    return result['data']


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class Pipeline:
    def __init__(self, args):
        self.args = args
        self.root = args.run_dir.resolve()
        config_path = args.config or default_config_path()
        self.config = load_config(config_path)
        self.stages = StageStore(self.root, self.config)
        self.store = ArtifactStore(self.root)
        self.freezes = FreezeStore(self.root)
        self.config_freeze = ConfigFreeze(self.root)
        self.snapshot_root = args.snapshot_root.resolve()
        self.seed = self.config['seed']
        self.mock = not args.live
        if args.live and self.config['mode'] == 'mock':
            raise StageError('Live execution requires an explicitly live configuration')
        if not args.live and self.config['mode'] != 'mock':
            raise StageError('Non-mock configuration requires explicit --live')
        if args.stage == 'mock-e2e' and args.live:
            raise StageError('mock-e2e cannot enable live calls')
        readiness_key = sha256_hash(read_json(args.readiness))[:12] if args.readiness else 'none'
        self.preflight_stage = ('preflight' if self.mock else
                                f'preflight-{self.stages.config_hash[:12]}-{readiness_key}')
        self.operational_freeze = 'freeze' if self.mock else 'freeze-final'
        self.model_config = dict(self.config['models']['swarm'].get('parameters', {}))
        if set(self.model_config) - {'temperature'}:
            raise StageError('Unsupported Adaption inference options in frozen configuration')

    def _client(self):
        if self.mock:
            from .mock import model_response
            backend = MockBackend(responses=model_response)
        else:
            from little_pom.runtime import AdaptionBackend
            if self.args.secret_path is None:
                raise StageError('Live calls require an explicit --secret-path')
            report = self.stages.data(self.preflight_stage)
            if not report.get('live_ready', False):
                raise StageError('Live scientific preflight has not passed; no model call is allowed')
            backend = AdaptionBackend(secret_path=self.args.secret_path,
                                      timeout=self.config['budgets']['timeout_seconds'])
        return SwarmClient(ledger=Ledger(self.root), backend=backend)

    def _cards(self):
        from little_pom.cards import CardWorkflow
        return CardWorkflow(self.root, self.config['vocabulary'], freeze_store=self.freezes)

    def _inventory(self):
        project = Path(__file__).resolve().parents[3]
        paths = [project / 'pyproject.toml', project / 'uv.lock', self.snapshot_root / 'snapshot_manifest.json']
        paths.extend(sorted((project / 'src' / 'little_pom').rglob('*.py')))
        paths.extend(sorted((project / 'src' / 'little_pom' / 'contracts' / 'json').glob('*.json')))
        paths.append(project / 'src' / 'little_pom' / 'sandbox' / 'Dockerfile')
        if (self.root / 'manifests' / f'{self.preflight_stage}.json').exists():
            paths.extend(Path(path) for path in self.stages.data(self.preflight_stage).get('readiness_evidence', {}))
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def _check_freeze(self):
        record = self.stages.data(self.operational_freeze)
        self.config_freeze.verify(record['phase'], self.config, self._inventory())

    def _bundle(self):
        if not self.args.responses:
            raise StageError('An explicit --responses JSON bundle is required for subscription import')
        value = read_json(self.args.responses)
        if not isinstance(value, dict):
            raise StageError('Response bundle must map opaque packet IDs to complete responses')
        return value

    def execute(self, stage):
        if stage == 'mock-e2e':
            for name in ('preflight', 'freeze', 'workers', 'cards-export', 'cards-import', 'panels',
                         'reviews', 'eval-export', 'eval-import', 'analyze'):
                result = self.execute(name)
                if result.get('status', 'ok') != 'ok':
                    return result
            return result
        if stage == 'workers' and self.args.phase == 'd0':
            raise StageError('Separate designated D0 execution requires its task data and adapter; neither is present in the supported three-task snapshot')
        if stage in ('cards-export', 'cards-import') and self.args.card_phase == 'applicability':
            self._check_freeze()
            if stage == 'cards-export':
                return {'status': 'ok', 'packets': self.stages.data(self.operational_freeze)['applicability_packets']}
            bundle = self._bundle()
            for packet in self.stages.data(self.operational_freeze)['applicability_packets']:
                accepted = self._cards().snapshot(packet['task_id']).get('applicability')
                if accepted and packet['id'] in bundle and accepted['response'] != bundle[packet['id']]:
                    raise StageError('Frozen applicability response changed')
            return self.stages.run('applicability-import', (self.operational_freeze,), self.applicability_import)
        if stage not in ('preflight', 'freeze'):
            self._check_freeze()
        action = getattr(self, stage.replace('-', '_'))
        dependencies = {
            'preflight': (), 'freeze': (self.preflight_stage,), 'workers': (self.operational_freeze,),
            'cards-export': ('workers',), 'cards-import': ('cards-export',),
            'panels': ('cards-import',), 'reviews': ('panels',),
            'eval-export': ('reviews',), 'eval-import': ('eval-export',), 'analyze': ('eval-import',),
        }
        storage_stage = stage
        if stage == 'preflight':
            storage_stage = self.preflight_stage
        if stage == 'freeze' and self.args.phase != 'mock':
            storage_stage = 'freeze-' + self.args.phase
            if self.args.phase in ('d0', 'fix'):
                dependencies['freeze'] = ()
        if stage == 'workers' and not self.mock:
            dependencies['workers'] += ('applicability-import',)
        inputs = {'snapshot_root': str(self.snapshot_root), 'mock': self.mock}
        if stage == 'preflight':
            inputs['inspect_docker'] = self.args.inspect_docker
            inputs['readiness_hash'] = sha256_hash(read_json(self.args.readiness)) if self.args.readiness else None
        if stage == 'freeze':
            inputs.update(phase=self.args.phase, d0_task=self.args.d0_task)
        return self.stages.run(storage_stage, dependencies[stage], action, inputs=inputs)

    def preflight(self):
        from little_pom.sandbox import preflight
        from little_pom.tasks import TaskAdapter
        from .readiness import evaluate_readiness
        attestation = read_json(self.args.readiness) if self.args.readiness else None
        if attestation is not None and not isinstance(attestation, dict):
            raise StageError('Readiness attestation must be a JSON object')
        images = attestation.get('images', {}) if attestation else {}
        if not isinstance(images, dict):
            raise StageError('Readiness images must map task IDs to immutable image IDs')
        report = preflight(self.snapshot_root, inspect_docker=self.args.inspect_docker, images=images)
        tasks = {}
        for task_id in self.config['task_ids']:
            # The safe task projection, not verifier or solution bytes, enters the run.
            tasks[task_id] = require(TaskAdapter().load(task_id, self.snapshot_root))
        digest = hashlib.sha256((self.snapshot_root / 'snapshot_manifest.json').read_bytes()).hexdigest()
        if digest != self.config['snapshot']['sha256']:
            raise StageError('Configured source snapshot digest differs from the actual manifest')
        readiness = evaluate_readiness(self.config, report.get('data') or {}, attestation,
                                      self.args.readiness.parent if self.args.readiness else self.root, self.config['task_ids'])
        readiness_evidence = {}
        if attestation and readiness['ready']:
            readiness_evidence[str(self.args.readiness.resolve())] = hashlib.sha256(self.args.readiness.read_bytes()).hexdigest()
            readiness_evidence.update({str((self.args.readiness.parent / record['path']).resolve()): record['sha256']
                                       for record in attestation['checks'].values() if record.get('passed') is True})
        return {'status': 'ok', 'tasks': tasks, 'environment': report, 'readiness': readiness, 'images': images,
                'mode': 'mock' if self.mock else 'live', 'live_ready': readiness['ready'] and not self.mock,
                'scientific_readiness': readiness['ready'] and not self.mock,
                'model_access_verified': readiness['ready'], 'blockers': readiness['blockers'],
                'readiness_evidence': readiness_evidence,
                'd0_completed': (self.root / 'd0_result.json').is_file()}

    def freeze(self):
        phase = self.args.phase
        inventory = self._inventory()
        if phase == 'mock':
            manifest = self.config_freeze.freeze_mock(self.config, inventory)
        elif phase == 'd0':
            manifest = self.config_freeze.freeze_d0(self.config, self.args.d0_task, inventory)
            return {'status': 'ok', 'phase': 'd0', 'manifest': manifest,
                    'execution_blocker': 'Separate designated task data and adapter are not in the three-task snapshot'}
        elif phase == 'fix':
            from .d0 import validate_d0_result
            d0 = read_json(self.root / 'd0_freeze_manifest.json')
            result = validate_d0_result(self._bundle(), d0['d0_task_id'], self.args.responses.parent)
            self.config_freeze.record_d0_result(result)
            manifest = self.config_freeze.open_fix_window()
            return {'status': 'ok', 'phase': 'fix', 'manifest': manifest}
        else:
            manifest = self.config_freeze.freeze_final(self.config, inventory)
        cards = self._cards()
        packets = []
        for task_id, task in self.stages.data(self.preflight_stage)['tasks'].items():
            assignment = self.config.get('defect_assignments', {}).get(task_id)
            if assignment is None:
                raise StageError('Every task needs an explicit frozen defect assignment before workers')
            accepted = cards.snapshot(task_id).get('applicability')
            if accepted:
                packets.append(read_json(self.root / 'cards' / 'packets' / f"{accepted['packet_id']}.json"))
            else:
                packets.extend(cards.export('applicability', {'task': task,
                               'defect_types': list(self.config['equivalence_map']), 'assignment': assignment}))
        if self.mock:
            from little_pom.cards.fixtures import subscription_response
            for packet in packets:
                if 'applicability' not in cards.snapshot(packet['task_id']):
                    require(cards.import_response(packet['id'], subscription_response(packet)))
        return {'status': 'ok', 'phase': phase, 'manifest': manifest, 'applicability_packets': packets}

    def applicability_import(self):
        workflow = self._cards()
        bundle = self._bundle()
        records = []
        for packet in self.stages.data(self.operational_freeze)['applicability_packets']:
            response = bundle[packet['id']]
            accepted = workflow.snapshot(packet['task_id']).get('applicability')
            if accepted:
                if accepted['response'] != response:
                    raise StageError('Frozen applicability response changed')
                records.append(accepted)
            else:
                records.append(require(workflow.import_response(packet['id'], response)))
        return {'status': 'ok', 'records': records}

    def workers(self):
        from little_pom.author import AuthorRunner
        from little_pom.evidence import EvidenceRegistry
        from little_pom.profiles import ProfileBuilder
        from little_pom.sandbox import Sandbox
        from little_pom.workers import FROZEN_SUBTASKS, WorkerRunner, author_handoff
        from .mock import SYNTHETIC_INPUT
        cards, client = self._cards(), self._client()
        all_tasks = {}
        for task_id, task in self.stages.data(self.preflight_stage)['tasks'].items():
            cards.begin_workers(task_id)
            registry = EvidenceRegistry(self.root / 'evidence' / sha256_hash(task_id), task_id)
            sandbox = (Sandbox(backend='fake', fake_inputs={'input.txt': SYNTHETIC_INPUT}) if self.mock else
                       Sandbox(backend='docker', docker_enabled=True, images=self.stages.data(self.preflight_stage)['images']))
            runner = WorkerRunner(self.root, registry=registry, model_config=self.model_config,
                                  max_turns=self.config['budgets']['worker_max_turns'],
                                  output_budget=self.config['budgets']['worker_output_tokens'])
            results = [runner.run(task, subtask, client, sandbox, self.store) for subtask in FROZEN_SUBTASKS]
            states = [r['data'] for r in results if r['status'] == 'ok']
            author_result = ({'status': 'incomplete', 'data': None, 'error': {'code': 'insufficient_complete_workers'}}
                             if len(states) < 2 else AuthorRunner(self.root, model_config=self.model_config,
                               output_budget=self.config['budgets']['author_output_tokens']).run(
                                   task, [author_handoff(s) for s in states], client))
            proposal = author_result['data']
            all_tasks[task_id] = {'task': task, 'states': states, 'proposal': proposal,
                                 'profiles': [ProfileBuilder().build(s['output'], self.config['vocabulary']) for s in states],
                                 'evidence': registry.evidence(), 'provenance': registry.provenance(),
                                 'worker_missingness': [r['error'] for r in results if r['status'] != 'ok'],
                                 'author_error': author_result['error']}
        return {'status': 'ok', 'tasks': all_tasks}

    def cards_export(self):
        workflow = self._cards()
        packets = []
        for record in self.stages.data('workers')['tasks'].values():
            if record['proposal'] is None:
                continue
            packets.extend(workflow.export('valid', {'task': record['task'], 'q0': record['proposal'],
                'evidence': record['evidence'], 'provenance': record['provenance'], 'common_dossier': ''}))
        return {'status': 'ok', 'packets': packets}

    def cards_import(self):
        workflow = self._cards()
        bundle = {} if self.mock else self._bundle()
        from little_pom.cards.fixtures import subscription_response
        imported = {}
        for packet in self.stages.data('cards-export')['packets']:
            snapshot = workflow.snapshot(packet['task_id'])
            if 'valid' in snapshot:
                valid = snapshot['valid']
            else:
                response = subscription_response(packet) if self.mock else bundle[packet['id']]
                valid = require(workflow.import_response(packet['id'], response))
            if 'def' in snapshot:
                defect = snapshot['def']
            else:
                defect_packet = workflow.export('def', {'task_id': packet['task_id']})[0]
                if not self.mock and defect_packet['id'] not in bundle:
                    raise StageError(f"Defect packet exported to cards/packets/{defect_packet['id']}.json; import its explicit response next")
                response = subscription_response(defect_packet) if self.mock else bundle[defect_packet['id']]
                defect = require(workflow.import_response(defect_packet['id'], response))
            imported[packet['task_id']] = {'valid': valid['card'], 'def': defect['card'],
                                          'requirements': valid['requirements'], 'mechanism': defect['mechanism']}
        return {'status': 'ok', 'tasks': imported}

    def panels(self):
        from little_pom.cards import diagnose_exploitability
        from little_pom.routing import PanelBuilder
        panels, mechanisms = [], {}
        workers = self.stages.data('workers')['tasks']
        for task_id, cards in self.stages.data('cards-import')['tasks'].items():
            record = workers[task_id]
            task_panels = PanelBuilder(self.root, self.freezes).build(record['states'], record['profiles'], cards['requirements'], self.seed)
            panels.extend(task_panels)
            mechanisms[task_id] = {**cards['mechanism'], 'eligible_worker_ids': [s['worker_id'] for s in record['states']],
                'panels': task_panels, 'exploitability': diagnose_exploitability(cards['mechanism'], record['profiles'], cards['requirements'])}
        for task_id, record in workers.items():
            if task_id not in mechanisms:
                mechanisms[task_id] = {'status': 'unavailable', 'reason': 'worker_or_author_stage_incomplete',
                    'target_defect_type': self.config['defect_assignments'][task_id], 'target_evidence_ids': [],
                    'eligible_worker_ids': [s['worker_id'] for s in record['states']], 'panels': [],
                    'informed_workers': [], 'informed_private_workers_valid': [], 'informed_private_workers_def': [],
                    'informed_private_workers_strict': [], 'exploitability': {'status': 'unavailable'}}
        return {'status': 'ok', 'panels': panels, 'mechanisms': mechanisms}

    def reviews(self):
        from little_pom.cards import card_text
        from little_pom.reviews import ReviewerPacketBuilder, ReviewRunner, RunList
        panels = self.stages.data('panels')['panels']
        workers = self.stages.data('workers')['tasks']
        cards = self.stages.data('cards-import')['tasks']
        packets, metadata = {}, {}
        states = {s['worker_id']: s for record in workers.values() for s in record['states']}
        by_panel = {p['id']: p for p in panels}
        for slot in RunList.build(panels, self.seed):
            panel = by_panel[slot['panel_id']]
            task = workers[slot['task_id']]['task']
            text = card_text(cards[slot['task_id']][panel['variant']])
            packet = ReviewerPacketBuilder(packet_id=slot['packet_id']).build(
                {'text': task['goal']}, {'text': ''}, {'text': text}, states.get(slot['worker_id']))
            packets.setdefault(panel['id'], {})[slot['reviewer_key']] = packet
            metadata[slot['id']] = {'task_id': slot['task_id'], 'task': task['goal'], 'card': text,
                'condition': panel['condition'], 'variant': panel['variant'], 'panel_id': panel['id'],
                'reviewer_id': slot['reviewer_key']}
        outcomes = ReviewRunner(seed=self.seed, concurrency=self.config['budgets']['concurrency'], model_config=self.model_config,
                               output_budget=self.config['budgets']['review_output_tokens']).run_all(panels, packets, self._client(), self.store)
        return {'status': 'ok', 'outcomes': [r['data'] for r in outcomes if r['data'] is not None],
                'service_errors': [r['error'] for r in outcomes if r['error']], 'review_metadata': metadata}

    def eval_export(self):
        from little_pom.measurement import EvaluationExporter
        record = self.stages.data('reviews')
        reviews = [review for outcome in record['outcomes'] for review in outcome['reviews']]
        evidence = {e['evidence_id']: {'text': e['fragment']} for task in self.stages.data('workers')['tasks'].values() for e in task['evidence']}
        metadata = {r['id']: record['review_metadata'][r['id']] for r in reviews}
        manifest = EvaluationExporter(self.root / 'measurement').export(reviews, {'reviews': metadata, 'items': evidence},
                                                                        self.seed, self.config['measurement_policy'])
        return {'status': 'ok', 'manifest': manifest}

    def eval_import(self):
        from little_pom.measurement import AdjudicationQueue, EvaluationImporter, final_scoring_projection
        from .mock import subscription_response
        root = self.root / 'measurement'
        importer = EvaluationImporter(root)
        bundle = {} if self.mock else self._bundle()
        def import_requests():
            accepted = {r['packet_id'] for r in importer.labels()}
            missing = []
            for path in sorted((root / 'subscription').glob('*.json')):
                request = read_json(path)
                packet_id = request['packet']['id']
                if packet_id in accepted:
                    continue
                if not self.mock and packet_id not in bundle:
                    missing.append(packet_id)
                    continue
                response = subscription_response(request) if self.mock else bundle[packet_id]
                require(importer.import_response(packet_id, response['raw_response'], response['metadata']))
            return missing
        missing = import_requests()
        if missing and not self.args.finalize_incomplete:
            raise StageError(f'Subscription responses still required: {missing}; use --finalize-incomplete to seal missingness')
        expected = self.stages.data('eval-export')['manifest']['packet_ids']
        observed = {r['packet_id'] for r in importer.labels()}
        queue_ready = all(pid in observed for lane in ('primary', 'calibration_a', 'calibration_b') for pid in expected[lane])
        if queue_ready:
            AdjudicationQueue(root).build_from_imports()
            missing = import_requests()
            if missing and not self.args.finalize_incomplete:
                raise StageError(f'Adjudication responses exported and still required: {missing}; use --finalize-incomplete to seal missingness')
        result = final_scoring_projection(root)
        return {'status': 'ok', 'measurement_status': result['status'], 'labels': result['data'], 'error': result['error']}

    def analyze(self):
        from little_pom.analysis import Analysis
        policy = {**self.config['analysis_policy'], 'equivalence_map': self.config['equivalence_map'], 'seed': self.seed}
        report = Analysis().compute(self.stages.data('reviews')['outcomes'], self.stages.data('eval-import')['labels'],
                                    self.stages.data('panels')['mechanisms'], policy)
        report.update(mode='mock' if self.mock else 'live',
                      scientific_readiness=self.stages.data(self.preflight_stage)['scientific_readiness'],
                      d0_task_id=self.config['d0_task_id'], independent_confirmation=False)
        report['missingness']['tasks'] = {task_id: {'worker_errors': record['worker_missingness'],
            'author_error': record['author_error']} for task_id, record in self.stages.data('workers')['tasks'].items()}
        path = self.root / 'report.json'
        if path.exists():
            if path.read_bytes() != canonical_json(report):
                raise StageError('Existing analysis report differs from frozen inputs')
        else:
            self.store.put_once('report.json', report)
        return {'status': 'ok', 'report': report, 'report_path': 'report.json', 'audit_trace': 'manifests/'}
