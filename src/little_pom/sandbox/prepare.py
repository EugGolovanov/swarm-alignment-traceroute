"""Explicit scientific image preparation, outside experimental run directories.

Usage: python -m little_pom.sandbox.prepare --task ID --output DIR --build --runtime
For SCI add --snapshot-root data/sci-agent-exam. Its public harness base is built
first, followed by the exact task environment and the SCI isolation overlay.
Build-time downloads are explicit; runtime is non-root and offline. Provenance
records image IDs and build arguments. SCI evaluation is a separate parent-owned
step; --oracle remains a TBS-only opt-in Harbor invocation with private logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from little_pom.contracts import canonical_json, sha256_hash
from little_pom.tasks import ALL_TASK_PATHS, SCI_TASK_PATHS, TaskAdapter, verify_snapshot
from little_pom.sandbox import Sandbox, preflight
from little_pom.sandbox.resources import MEMORY_AMENDMENTS, runtime_requirements, sci_overlay_hash


def inspect_oracle(job_root, task_root):
    """A successful process is insufficient; require one exact successful oracle trial."""
    paths = sorted(Path(job_root).glob('*/result.json'))
    if len(paths) != 1:
        return {'verified': False, 'reason': 'Expected exactly one oracle trial'}
    path = paths[0]
    trial = json.loads(path.read_text())
    config = trial.get('config', {})
    rewards = (trial.get('verifier_result') or {}).get('rewards') or {}
    verified = (Path(config.get('task', {}).get('path', '')).resolve() == Path(task_root).resolve()
                and config.get('agent', {}).get('name') == 'oracle'
                and trial.get('exception_info') is None and bool(trial.get('finished_at'))
                and type(rewards.get('reward')) in (int, float) and rewards['reward'] == 1.0)
    return {'verified': verified, 'rewards': rewards, 'trial_result_path': str(path),
            'trial_result_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'scope': 'Upstream oracle; does not resolve separate overlay/export verifier risks'}


def sci_runtime_command(task_id):
    """Exercise public paths and confinement, not scientific answers or graders."""
    inputs = {
        'bioinformatics-error-diagnosis-01': ['analysis_config.json', 'broken_project/scripts/reproduce.py',
                                             'broken_project/tests/test_public_contract.py'],
        'chemistry-result-interpretation-02': ['reported_energetics.csv', 'malhan_thirumoorthy_2026.pdf'],
        'materials-science-literature-synthesis-02': ['2605.08262v1.pdf', '2512.04562v2.pdf',
                                                     '2511.01790v1.pdf', '2510.23181v2.pdf'],
        'mathematics-code-generation-02': ['cpcw_instances.json', 'starter/cpcw_audit.py',
                                          'axioms-15-00035-v2.pdf'],
        'physics-simulation-tool-orchestration-01': ['run_catalog.sqlite', 'diagnostics/Probes0.h5',
                                                    'diagnostics/Fields0.h5'],
    }
    code = [
        'import os,json,shutil,socket',
        'from pathlib import Path',
        'assert os.getuid()==65532 and os.getgid()==65532',
        "assert Path.cwd().resolve()==Path('/home/agent').resolve()",
        f"inputs={inputs[task_id]!r}",
        "assert all((Path('/home/agent/data')/name).is_file() for name in inputs)",
        "assert os.statvfs('/opt/little-sci-inputs').f_flag & os.ST_RDONLY",
        "assert all(name=='lo' or not (int(Path('/sys/class/net',name,'flags').read_text(),16)&1) for _,name in socket.if_nameindex())",
        "status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)",
        "assert int(status['CapEff'].strip(),16)==0 and status['NoNewPrivs'].strip()=='1'",
        "p=Path('/home/agent/results/.runtime-check')",
        "p.write_text('ok'); assert p.read_text()=='ok'; p.unlink()",
    ]
    if task_id != 'mathematics-code-generation-02':
        code.append('import numpy,scipy')
    if task_id == 'physics-simulation-tool-orchestration-01':
        code += ['import h5py,sqlite3', "assert shutil.which('sqlite3') and shutil.which('h5dump')",
                 "with h5py.File('/home/agent/data/diagnostics/Probes0.h5','r') as data: assert len(data)>0",
                 "with sqlite3.connect('file:/home/agent/data/run_catalog.sqlite?mode=ro',uri=True) as db: assert db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0]>0"]
    if task_id == 'bioinformatics-error-diagnosis-01':
        code += ["p=Path('/home/agent/data/broken_project/.runtime-check')",
                 "p.write_text('ok'); p.unlink()"]
    code.append("print(json.dumps({'uid':os.getuid(),'public_inputs':inputs,'results_writable':True,'isolated':True}))")
    return ['python3', '-c', '\n'.join(code)]


def prepare(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'preparation.json').exists():
        raise ValueError('Preparation output already exists; use a new directory for a new observation')
    manifest, _ = verify_snapshot(args.snapshot_root)
    task_result = TaskAdapter().load(args.task, args.snapshot_root)
    if task_result['status'] != 'ok':
        raise ValueError('Task projection failed')
    task = task_result['data']
    record = {'task_id': args.task, 'snapshot_hash': sha256_hash(manifest),
              'runtime_verified': False, 'oracle_verified': False, 'experiment_started': False,
              'declared_resource_requirements': task['resource_requirements'],
              'runtime_resource_requirements': runtime_requirements(args.task),
              'resource_amendment': MEMORY_AMENDMENTS.get(args.task)}
    try:
        sci = args.task in SCI_TASK_PATHS
        if sci and args.oracle:
            raise ValueError('SCI evaluation is separate; --oracle is supported only for TBS tasks')
        image = args.image
        if args.build:
            tag = 'little-pom-' + args.task + ':' + sha256_hash(manifest)[:12]
            context = args.snapshot_root.resolve() / 'upstream' / ALL_TASK_PATHS[args.task] / 'environment'
            # Never use a task root (with solution/verifier) as an agent build context.
            overlay = Path(__file__).with_name('Dockerfile.sci' if sci else 'Dockerfile')
            commands = []
            upstream_args = ['docker', 'build', '-t', tag + '-upstream']
            overlay_args = ['docker', 'build', '-t', tag,
                            '--build-arg', 'AGENT_IMAGE=' + tag + '-upstream',
                            '--build-arg', 'TASK_ID=' + args.task,
                            '--build-arg', 'SNAPSHOT_HASH=' + sha256_hash(manifest),
                            '--build-arg', 'DOCKERFILE_SHA256=' + task['agent_image']['dockerfile_sha256']]
            if sci:
                base_context = args.snapshot_root.resolve() / 'upstream' / 'harness' / 'base'
                base_tag = 'little-pom-sci-base:' + sha256_hash(manifest)[:12]
                commands.append(('base-build', ['docker', 'build', '-t', base_tag, str(base_context)]))
                source = (context / 'Dockerfile').read_text(encoding='utf-8')
                if source.count('FROM sciagent-bench:base') != 1:
                    raise ValueError('Unexpected SCI upstream base-image contract')
                generated = root / 'upstream.Dockerfile'
                generated.write_text(source.replace('FROM sciagent-bench:base', 'FROM ' + base_tag), encoding='utf-8')
                upstream_args += ['-f', str(generated)]
                overlay_args += ['--build-arg', 'OVERLAY_SHA256=' + sci_overlay_hash()]
                record['base_dockerfile_sha256'] = manifest['files_sha256']['harness/base/Dockerfile']
                record['overlay_sha256'] = sci_overlay_hash()
                record['effective_dockerfile_sha256'] = hashlib.sha256(generated.read_bytes()).hexdigest()
            commands += [('upstream-build', [*upstream_args, str(context)]),
                         ('overlay-build', [*overlay_args, '-f', str(overlay), str(overlay.parent)])]
            record['build_commands'] = [{'stage': name, 'argv': command} for name, command in commands]
            for name, command in commands:
                with (root / (name + '.log')).open('x') as log:
                    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=args.timeout)
            image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True, timeout=30).strip()
            if sci:
                record['base_image'] = subprocess.check_output(
                    ['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag], text=True, timeout=30).strip()
                record['upstream_image'] = subprocess.check_output(
                    ['docker', 'image', 'inspect', '--format', '{{.Id}}', tag + '-upstream'], text=True, timeout=30).strip()
        if not image:
            raise ValueError('Supply --image sha256:... or --build')
        record['image'] = image
        record['preflight'] = preflight(args.snapshot_root, inspect_docker=True, images={args.task: image})
        if args.runtime:
            sandbox = Sandbox(backend='docker', docker_enabled=True, images={args.task: image})
            with sandbox.session(task, {}) as handle:
                command = ['lake', 'env', 'lean', '--version'] if args.task == 'finite-free-stam' else [
                    'python3', '-c', 'import os,numpy; assert os.getuid()==65532; print(numpy.__version__)']
                if sci:
                    command = sci_runtime_command(args.task)
                record['runtime_result'] = sandbox.execute(handle, {'operation': 'exec', 'argv': command})
                result = record['runtime_result']
                record['runtime_verified'] = (result['status'] == 'ok' and result['data']['exit_status'] == 0
                                              and not result['data']['truncated'])
        if args.oracle:
            if not record['runtime_verified']:
                raise ValueError('Oracle execution requires successful --runtime first')
            task_root = args.snapshot_root.resolve() / 'upstream' / ALL_TASK_PATHS[args.task]
            command = [str(args.harbor.resolve()), 'run', '--path', str(task_root), '--agent', 'oracle',
                       '--n-concurrent', '1', '--n-attempts', '1', '--max-retries', '0', '--env', 'docker',
                       '--jobs-dir', str(root / 'private-oracle'), '--job-name', 'oracle']
            if args.task in MEMORY_AMENDMENTS:
                command += ['--override-memory-mb', str(runtime_requirements(args.task)['memory_mb'])]
            with (root / 'private-oracle.log').open('x') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
            record['oracle_exit_code'] = result.returncode
            record['oracle_inspection'] = inspect_oracle(root / 'private-oracle' / 'oracle', task_root)
            record['oracle_verified'] = result.returncode == 0 and record['oracle_inspection']['verified']
            if args.task == 'dna-storage-codec':
                record['dna_symlink_verifier_resolution'] = 'unresolved_separate_from_upstream_oracle_success'
        verify_snapshot(args.snapshot_root)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record['error'] = str(exc)
    (root / 'preparation.json').write_bytes(canonical_json(record))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, choices=tuple(ALL_TASK_PATHS))
    parser.add_argument('--snapshot-root', type=Path, default=Path('data/terminal-bench-science'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--build', action='store_true', help='Explicitly allow build-time downloads')
    parser.add_argument('--image', help='Existing immutable overlay image ID')
    parser.add_argument('--runtime', action='store_true')
    parser.add_argument('--oracle', action='store_true', help='Explicitly execute private upstream oracle with Harbor')
    parser.add_argument('--harbor', type=Path, default=Path('.venv-harbor/bin/harbor'))
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    result = prepare(args)
    print(json.dumps(result, indent=2))
    return 2 if (result.get('error') or (args.runtime and not result['runtime_verified'])
                 or (args.oracle and not result['oracle_verified'])) else 0


if __name__ == '__main__':
    raise SystemExit(main())
