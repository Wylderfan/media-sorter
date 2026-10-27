"""Executed via SSH with Python 3; Linux destination only. No shell interpolation."""
import ctypes
import errno
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile


def directory(path):
    path = pathlib.Path(path)
    if not path.is_absolute() or '..' in path.parts or '.' in str(path).split('/'):
        raise ValueError('Unsafe absolute directory')
    for part in reversed([path] + list(path.parents)):
        if not stat.S_ISDIR(part.lstat().st_mode):
            raise ValueError('Directory ancestor is not a real directory: ' + str(part))


def absent(path):
    if os.path.lexists(path):
        raise FileExistsError(errno.EEXIST, 'Destination conflict', path)


def rename_new(source, target):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), target)


def main():
    action, root, show, season, stage = json.loads(sys.argv[1])
    directory(root)
    if not os.access(root, os.W_OK):
        raise PermissionError('TV destination must be writable.')
    for name in (show, season):
        if not name or name in ('.', '..') or '/' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError('Unsafe component')
    parent = os.path.join(root, show)
    target = os.path.join(parent, season)
    if action in ('check', 'stage', 'publish'):
        # Check symbol availability without changing the destination in preview.
        getattr(ctypes.CDLL(None), 'renameat2')
        if os.path.lexists(parent):
            directory(parent)
        absent(target)
    if action == 'check':
        return
    if action == 'stage':
        print(tempfile.mkdtemp(prefix='.media-sorter-tv-', dir=root))
        return
    if not stage or os.path.dirname(stage) != root or not os.path.basename(stage).startswith('.media-sorter-tv-'):
        raise ValueError('Unsafe stage')
    directory(stage)
    if action == 'cleanup':
        shutil.rmtree(stage)
    elif action == 'publish':
        payload = os.path.join(stage, 'payload')
        directory(payload)
        # Reject unexpected types even if a source changed during transfer.
        for base, dirs, files in os.walk(payload):
            for name in dirs + files:
                mode = os.lstat(os.path.join(base, name)).st_mode
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ValueError('Unsafe staged entry')
        try:
            os.mkdir(parent)
        except FileExistsError:
            pass
        directory(parent)
        rename_new(payload, target)
    else:
        raise ValueError('Unknown operation')


try:
    main()
except FileExistsError as error:
    print(str(error), file=sys.stderr)
    sys.exit(3)
except Exception as error:
    print(str(error), file=sys.stderr)
    sys.exit(1)
