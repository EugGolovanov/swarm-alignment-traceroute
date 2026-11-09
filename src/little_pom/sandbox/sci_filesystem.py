"""Container-only SCI public-input layout; concatenated after filesystem.py.

The environment image supplies data, never a host task directory. Large scientific
inputs are hashed incrementally rather than forced through the tool evidence cap.
Only the bioinformatics repair project is copied into writable task storage.
"""
import shutil

SCI_INPUT_ROOT = '/opt/little-sci-inputs'


def sci_bootstrap():
    for hidden in ('/tests', '/solution', '/authoring', '/secrets', '/home/agent/tests',
                   '/home/agent/solution', '/home/agent/rubric.json', '/home/agent/golden'):
        if os.path.lexists(hidden):
            raise ValueError('SCI agent image contains a forbidden private input path')
    hashes = {}
    for directory, dirs, files in os.walk(SCI_INPUT_ROOT, followlinks=False):
        for name in dirs + files:
            candidate = os.path.join(directory, name)
            metadata = os.lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError('SCI image inputs contain a symlink')
            if name in files:
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError('SCI image inputs must be single-link regular files')
                relative = os.path.relpath(candidate, SCI_INPUT_ROOT)
                parts_for('data/' + relative, public_sci_tests=True)
                digest = hashlib.sha256()
                with open(candidate, 'rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                hashes['data/' + relative] = digest.hexdigest()
    os.makedirs('/work/data')
    for name in os.listdir(SCI_INPUT_ROOT):
        source = os.path.join(SCI_INPUT_ROOT, name)
        destination = os.path.join('/work/data', name)
        if name == 'broken_project':
            shutil.copytree(source, destination)
            # The immutable image files are world-readable; only the repair copy
            # needs owner write access, including files later patched in place.
            for directory, dirs, files in os.walk(destination):
                os.chmod(directory, 0o700)
                for filename in files:
                    os.chmod(os.path.join(directory, filename), 0o600)
        else:
            os.symlink(source, destination)
    os.makedirs('/work/results')
    return {'input_hashes': hashes}


def sci_source(path):
    """Resolve only owned input aliases; never follow user-created links."""
    parts = parts_for(path, allow_root=True, public_sci_tests=True)
    if parts and parts[0] == 'data':
        if len(parts) > 1 and parts[1] == 'broken_project':
            return '/work', path
        if len(parts) > 1:
            alias = os.path.join('/work/data', parts[1])
            expected = os.path.join(SCI_INPUT_ROOT, parts[1])
            if not os.path.islink(alias) or os.readlink(alias) != expected:
                raise ValueError('Immutable SCI input alias was modified')
        return SCI_INPUT_ROOT, '/'.join(parts[1:]) or '.'
    return '/work', path
