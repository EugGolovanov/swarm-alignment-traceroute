"""Command-line entrypoint. No external execution is enabled implicitly."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .stages import StageError

STAGES = ('preflight', 'freeze', 'workers', 'cards-export', 'cards-import', 'panels',
          'reviews', 'eval-export', 'eval-import', 'analyze', 'mock-e2e')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='little-pom', description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--snapshot-root', type=Path, default=Path('data/terminal-bench-science'))
    parser.add_argument('--responses', type=Path, help='Explicit exported subscription response bundle')
    parser.add_argument('--live', action='store_true', help='Explicitly permit Adaption calls; requires successful preflight and key path')
    parser.add_argument('--secret-path', type=Path)
    parser.add_argument('--inspect-docker', action='store_true', help='Read Docker resource metadata; never build images')
    parser.add_argument('--phase', choices=('mock', 'd0', 'fix', 'final'), default='mock')
    parser.add_argument('--d0-task', help='Explicitly designated separate development task; never selected automatically')
    parser.add_argument('--readiness', type=Path, help='Explicit operator attestation and hashed readiness evidence')
    parser.add_argument('--card-phase', choices=('applicability', 'valid'), default='valid')
    parser.add_argument('--finalize-incomplete', action='store_true', help='Seal available measurement with missingness; never resample')
    parser.add_argument('stage', choices=STAGES)
    args = parser.parse_args(argv)
    try:
        from .pipeline import Pipeline
        result = Pipeline(args).execute(args.stage)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get('status', 'ok') == 'ok' else 2
    except (StageError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'incomplete', 'data': None,
                          'error': {'code': 'cli_boundary_failure', 'message': str(exc)}}, sort_keys=True), file=sys.stderr)
        return 2


__all__ = ['main', 'STAGES']
