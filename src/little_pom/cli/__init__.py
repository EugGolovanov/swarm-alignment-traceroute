"""Command-line entrypoint. No external execution is enabled implicitly."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .stages import StageError

STAGES = ('preflight', 'freeze', 'workers', 'cards-export', 'cards-import', 'panels',
          'reviews', 'eval-export', 'eval-import', 'analyze', 'mock-e2e', 'sequential-run', 'subscription-export', 'model-smoke')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='little-pom', description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--snapshot-root', type=Path, default=Path('data/terminal-bench-science'))
    parser.add_argument('--responses', type=Path, help='Explicit exported subscription response bundle')
    parser.add_argument('--live', action='store_true', help='Permit subscription workers and Adaption author/reviewer calls after successful preflight')
    parser.add_argument('--secret-path', type=Path, help='Adaption key path, required only for author/reviewer calls')
    parser.add_argument('--inspect-docker', action='store_true', help='Read Docker resource metadata; never build images')
    parser.add_argument('--phase', choices=('mock', 'd0', 'fix', 'final', 'exploratory'), default='mock')
    parser.add_argument('--task', help='Task ID for an individual exploratory task stage')
    parser.add_argument('--d0-task', help='Explicitly designated separate development task; never selected automatically')
    parser.add_argument('--readiness', type=Path, help='Explicit operator attestation and hashed readiness evidence')
    parser.add_argument('--card-phase', choices=('applicability', 'valid'), default='valid')
    parser.add_argument('--finalize-incomplete', action='store_true', help='Seal available measurement with missingness; never resample')
    parser.add_argument('stage', choices=STAGES)
    args = parser.parse_args(argv)
    try:
        from .pipeline import Pipeline
        if args.stage == 'model-smoke':
            from .model_smoke import model_smoke
            result = model_smoke(args)
        elif args.phase == 'exploratory':
            from .sequential import SequentialPipeline
            result = SequentialPipeline(args).execute(args.stage)
        elif args.stage in ('sequential-run', 'subscription-export') or args.task:
            raise StageError('Sequential task execution requires explicit --phase exploratory')
        else:
            result = Pipeline(args).execute(args.stage)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get('status', 'ok') == 'ok' else 2
    except (StageError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'incomplete', 'data': None,
                          'error': {'code': 'cli_boundary_failure', 'message': str(exc)}}, sort_keys=True), file=sys.stderr)
        return 2


__all__ = ['main', 'STAGES']
