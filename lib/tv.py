"""Manual season transfer orchestration; movie configuration and code stay separate."""
import argparse
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys
import tempfile

SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
REMOTE = pathlib.Path(__file__).with_name('tv_remote.py').read_text()
SEASON = re.compile(r'^(?:(.+?)\s+[-–]?\s*)?(?:Season\s*|S)([0-9]{1,2})$', re.I)


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        raise ValueError('Input ended; stopping without approval.') from None


def component(value):
    return bool(value.strip()) and value not in ('.', '..') and '/' not in value and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def absolute(value):
    if not value.startswith('/') or any(p in ('.', '..') for p in value.split('/')) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Paths must be absolute without traversal or control characters.')
    return value.rstrip('/') or '/'


def real_directory(path):
    for item in reversed([path] + list(path.parents)):
        if not stat.S_ISDIR(item.lstat().st_mode):
            raise ValueError('Symlink or non-directory ancestor: ' + str(item))


def inventory(path):
    real_directory(path)
    result = {}
    def visit(folder):
        with os.scandir(folder) as entries:
            for item in entries:
                info = item.stat(follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise ValueError('Symlink or special file in season: ' + item.path)
                result[str(pathlib.Path(item.path).relative_to(path))] = (
                    info.st_mode, info.st_size, info.st_mtime_ns, info.st_ino)
                if stat.S_ISDIR(info.st_mode):
                    visit(item.path)
    visit(path)
    return result


def discover(root):
    # Only the two documented levels. Never descend through symlinks.
    for entry in sorted(root.iterdir()):
        if not stat.S_ISDIR(entry.lstat().st_mode):
            print('Ignoring non-directory root entry:', repr(entry.name))
            continue
        children = sorted(entry.iterdir())
        # A labeled season, or a directory containing files, is one season.
        if SEASON.fullmatch(entry.name) or any(not stat.S_ISDIR(p.lstat().st_mode) for p in children):
            yield entry, ''
        else:
            for child in children:
                if stat.S_ISDIR(child.lstat().st_mode):
                    yield child, entry.name
                else:
                    print('Ignoring non-directory:', repr(str(child)))


def identity(path, context):
    match = SEASON.fullmatch(path.name)
    suggestion = context or (match.group(1) if match else '') or ''
    # Every suggestion is explicitly confirmed; ambiguous/release names are not parsed.
    while True:
        show = ask('Show name (optional year), confirm/correct [' + suggestion + ']: ').strip() or suggestion
        if component(show):
            break
        print('Enter a show name; no identity can be inferred.')
    number = match.group(2) if match else ''
    while True:
        value = ask('Season number, confirm/correct [' + number + ']: ').strip() or number
        if re.fullmatch(r'[0-9]{1,2}', value):
            return show, 'Season ' + value.zfill(2)
        print('Enter a season number from 0 to 99; no number is guessed.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['tv', 'configure-tv'])
    parser.add_argument('--source')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise ValueError('Interactive input is required; nothing transferred.')
    config = pathlib.Path(os.environ.get('MEDIA_SORTER_CONFIG', os.path.expanduser('~/.config/media-sorter/config')) + '.tv')
    if args.command == 'configure-tv':
        if args.source or args.apply:
            raise ValueError('configure-tv takes no options.')
        values = [ask(p) for p in ('Absolute TV source: ', 'Server hostname or SSH alias: ', 'SSH username: ', 'Absolute TV destination: ')]
    else:
        if not config.is_file():
            raise ValueError('TV configuration missing; run configure-tv.')
        values = config.read_text().splitlines()
    if len(values) != 4:
        raise ValueError('Invalid TV configuration; run configure-tv.')
    source, host, user, dest = values
    source, dest = absolute(args.source or source), absolute(dest)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', host) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*', user):
        raise ValueError('Invalid SSH host/user.')
    root = pathlib.Path(source)
    real_directory(root)
    if args.command == 'configure-tv':
        if ask('Save TV settings? [yes/no] ') != 'yes':
            raise ValueError('Setup cancelled.')
        config.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=config.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write('\n'.join(values) + '\n')
            os.replace(name, config)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        print('Saved', config)
        return 0
    peer = user + '@' + host
    def remote(action, show, season, stage=''):
        command = shlex.join(['python3', '-c', REMOTE, json.dumps([action, dest, show, season, stage])])
        return subprocess.run(SSH + ['-n', peer, command], stdout=subprocess.PIPE, text=True)
    local = subprocess.run(['rsync', '--help'], capture_output=True, text=True)
    if local.returncode or not any(x in local.stdout for x in ('--protect-args', '--secluded-args')):
        raise ValueError('Local rsync 3.x with --protect-args required.')
    check = subprocess.run(SSH + ['-n', peer, 'command -v python3 >/dev/null && rsync --help | grep -e --protect-args -e --secluded-args >/dev/null'])
    if check.returncode:
        raise ValueError('SSH failed or remote Python 3 / rsync 3.x unavailable.')
    counts = dict(transferred=0, skipped=0, conflicted=0, failed=0, approved=0)
    if not args.apply:
        print('PREVIEW: no remote library changes will be made.')
    try:
        candidates = list(discover(root))
        for path, context in candidates:
            stage = ''
            try:
                before = inventory(path)
                if not any(stat.S_ISREG(v[0]) for v in before.values()):
                    print('Skipping empty season:', repr(str(path)))
                    counts['skipped'] += 1
                    continue
                print('\nSeason directory:', repr(str(path)))
                show, season = identity(path, context)
                print('Destination:', repr(dest + '/' + show + '/' + season + '/'))
                print('Contents: %d files' % sum(stat.S_ISREG(v[0]) for v in before.values()))
                for name in sorted(before):
                    print(' ', repr(name) + ('/' if stat.S_ISDIR(before[name][0]) else ''))
                while True:
                    reply = ask(('Send' if args.apply else 'Preview') + ' this season? [Y/n] ').lower()
                    if reply in ('', 'y', 'yes', 'n', 'no'):
                        break
                    print('Please enter Y or N (Enter means yes).')
                if reply in ('n', 'no'):
                    counts['skipped'] += 1
                    continue
                counts['approved'] += 1
                checked = remote('check', show, season)
                if checked.returncode:
                    counts['conflicted' if checked.returncode == 3 else 'failed'] += 1
                    continue
                if not args.apply:
                    print('Would transfer season recursively.')
                    continue
                staged = remote('stage', show, season)
                if staged.returncode:
                    counts['conflicted' if staged.returncode == 3 else 'failed'] += 1
                    continue
                stage = staged.stdout.strip()
                if os.path.dirname(stage) != dest or not os.path.basename(stage).startswith('.media-sorter-tv-'):
                    raise ValueError('Invalid staging response.')
                if inventory(path) != before:
                    raise ValueError('Season changed after approval.')
                copied = subprocess.run(['rsync', '--protect-args', '-e', shlex.join(SSH), '-rt', '--no-links', '--no-specials', '--no-devices', '--', str(path) + '/', peer + ':' + stage + '/payload/'])
                if copied.returncode or inventory(path) != before:
                    raise ValueError('Transfer failed or source changed; season not published.')
                published = remote('publish', show, season, stage)
                counts['transferred' if published.returncode == 0 else 'conflicted' if published.returncode == 3 else 'failed'] += 1
            except (OSError, ValueError) as error:
                print('Error:', error, file=sys.stderr)
                counts['failed'] += 1
                if str(error).startswith('Input ended'):
                    break
            finally:
                if stage and remote('cleanup', show, season, stage).returncode:
                    print('Warning: staging cleanup failed:', repr(stage), file=sys.stderr)
    finally:
        print('Summary:', ' '.join('%s=%d' % item for item in counts.items()))
    return int(counts['failed'] != 0)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyboardInterrupt) as error:
        print('Error:', error, file=sys.stderr)
        sys.exit(1)
