"""TV fixtures use real rsync over local fake SSH; never a real server."""
import os
import json
import subprocess
import tempfile
from unittest import mock
import pathlib
import shutil
import unittest
import test_cli


class TVIntegration(unittest.TestCase):
    def setUp(self):
        # macOS /var is a symlink; fixtures must use the physical temp root,
        # just like the production policy requires for source/destination paths.
        with mock.patch('tempfile.tempdir', str(pathlib.Path(tempfile.gettempdir()).resolve())):
            test_cli.CLIIntegration.setUp(self)

    run_cli = test_cli.CLIIntegration.run_cli

    def prepare(self, layout='nested', name="Show's $(touch BAD) (2020)", season='Season 01'):
        self.dest = self.root / "Library's TV"
        self.dest.mkdir(exist_ok=True)
        self.tv = self.root / 'TV'
        self.tv.mkdir(exist_ok=True)
        path = self.tv / name / season if layout == 'nested' else self.tv / (name + ' ' + season if name else season)
        path.mkdir(parents=True)
        (path / "S01E01's $file [x];.mkv").write_bytes(b'one')
        (path / 'sub folder').mkdir()
        (path / 'sub folder' / 'S01E02.mp4').write_bytes(b'two')
        self.tvconfig = pathlib.Path(str(self.config) + '.tv')
        self.tvconfig.write_text(f'{self.tv}\nserver\nuser\n{self.dest}\n')
        real = os.environ.get('MEDIA_SORTER_TEST_RSYNC') or shutil.which('rsync')
        if not real:
            self.skipTest('Real rsync required for TV tests')
        self.env['REAL_RSYNC'] = real
        # Simulate Linux renameat2 on macOS using its equivalent exclusive rename.
        wrapper = self.root / 'tools' / 'python3'
        wrapper.write_text('''#!''' + shutil.which('python3') + '''
import ctypes, errno, os, runpy, sys
if sys.platform == 'darwin' and sys.argv[1] == '-c':
    original = ctypes.CDLL
    class LinuxLib:
        def __init__(self, *args, **kwargs):
            self.lib = original(*args, **kwargs)
            def renameat2(a, src, b, dst, flags):
                return self.lib.renamex_np(src, dst, 4)  # RENAME_EXCL
            self.renameat2 = renameat2
    ctypes.CDLL = LinuxLib
if sys.argv[1] == '-c':
    code = sys.argv[2]
    sys.argv = ['-c'] + sys.argv[3:]
    exec(code)
else:
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
''')
        wrapper.chmod(0o755)
        return path

    def prompts(self, show='', number=''):
        return [('Show name (optional year)', show), ('Season number,', number)]

    def test_layouts_recursion_and_preview(self):
        for layout in ['nested', 'flat']:
            with self.subTest(layout=layout):
                path = self.prepare(layout)
                before = self.config.read_bytes()
                for apply in [False, True]:
                    status, output = self.run_cli('tv', *(['--apply'] if apply else []), responses=self.prompts())
                    self.assertEqual(status, 0, output)
                    self.assertIn('Contents: 2 files', output)
                    self.assertIn('Destination:', output)
                    self.assertLess(output.index('Contents:'), output.index('[Y/n]'))
                    if not apply:
                        self.assertEqual(list(self.dest.iterdir()), [])
                target = self.dest / "Show's $(touch BAD) (2020)" / 'Season 01'
                for file in path.rglob('*'):
                    if file.is_file():
                        self.assertEqual(file.read_bytes(), (target / file.relative_to(path)).read_bytes())
                self.assertFalse((self.root / 'BAD').exists())
                self.assertEqual(before, self.config.read_bytes())
                shutil.rmtree(self.tv)
                shutil.rmtree(target.parent)

    def test_no_guess_and_corrections(self):
        self.prepare('flat', '', 'Unlabeled')
        status, output = self.run_cli('tv', '--apply', responses=[
            ('Show name (optional year)', ''), ('Enter a show name', 'Corrected'),
            ('Season number,', ''), ('Enter a season number', '7')])
        self.assertEqual(status, 0, output)
        self.assertTrue((self.dest / 'Corrected' / 'Season 07').is_dir())
        self.assertNotIn('(2020)', output)

    def test_reject_eof_collision(self):
        self.prepare()
        for answers in [['n'], [None]]:
            status, output = self.run_cli('tv', '--apply', responses=self.prompts(), answers=answers)
            self.assertEqual(status, 0 if answers == ['n'] else 1, output)
            self.assertEqual(list(self.dest.iterdir()), [])
        target = self.dest / 'Correct' / 'Season 09'
        target.mkdir(parents=True)
        (target / 'keep').write_bytes(b'original')
        status, output = self.run_cli('tv', '--apply', responses=self.prompts('Correct', '9'))
        self.assertEqual(status, 0, output)
        self.assertIn('conflicted=1', output)
        self.assertEqual([p.name for p in target.iterdir()], ['keep'])

    def test_symlinks_and_special_files(self):
        path = self.prepare()
        for kind in ['file', 'directory', 'fifo']:
            bad = path / 'bad'
            if kind == 'fifo':
                os.mkfifo(bad)
            else:
                bad.symlink_to(self.movie if kind == 'directory' else self.movie / 'extra.mkv')
            status, output = self.run_cli('tv', '--apply')
            self.assertEqual(status, 1, output)
            self.assertIn('Symlink or special file', output)
            self.assertEqual(list(self.dest.iterdir()), [])
            bad.unlink()

    def test_failure_and_concurrent_publication(self):
        self.prepare()
        tool = self.root / 'tools' / 'rsync'
        original = tool.read_text()
        for failure in [True, False]:
            hook = '''
if [ "$1" != --help ]; then
    "$REAL_RSYNC" "$@" || exit $?
'''
            if failure:
                hook += '    exit 23\n'
            else:
                hook += '''    mkdir -p "$RACE_TARGET"
    echo winner > "$RACE_TARGET/keep"
    exit 0
'''
            hook += 'fi\n'
            tool.write_text('#!/bin/bash\n' + hook + original)
            target = self.dest / 'Correct' / 'Season 01'
            self.env['RACE_TARGET'] = str(target)
            status, output = self.run_cli('tv', '--apply', responses=self.prompts('Correct'))
            self.assertEqual(status, 1 if failure else 0, output)
            self.assertFalse(list(self.dest.glob('.media-sorter-*')))
            if failure:
                self.assertFalse(target.exists())
            else:
                self.assertIn('conflicted=1', output)
                self.assertEqual([p.name for p in target.iterdir()], ['keep'])

    def test_atomic_publication_collision_after_check(self):
        self.prepare()
        stage = self.dest / '.media-sorter-tv-test'
        (stage / 'payload').mkdir(parents=True)
        (stage / 'payload' / 'episode.mkv').write_bytes(b'ours')
        code = (test_cli.CLI.parent.parent / 'lib/tv_remote.py').read_text()
        code = code.replace('        rename_new(payload, target)',
                            '        os.mkdir(target)\n        rename_new(payload, target)')
        result = subprocess.run(['python3', '-c', code, json.dumps([
            'publish', str(self.dest), 'Show', 'Season 01', str(stage)])],
            env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(list((self.dest / 'Show' / 'Season 01').iterdir()), [])
        self.assertTrue((stage / 'payload' / 'episode.mkv').exists())

    def test_batch_eof_and_readable_progress_locale(self):
        path = self.prepare(name="Show's name")
        self.prepare(name='Second', season='Season 02')
        self.env.update(LC_ALL='media_sorter_missing.UTF-8', LANG='media_sorter_missing.UTF-8')
        status, output = self.run_cli('tv', '--apply', responses=self.prompts() * 2, answers=['', None])
        self.assertEqual(status, 1, output)
        self.assertIn('Selection cancelled', output)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertFalse((self.root / 'rsync.log').exists())
        def approve(candidate):
            self.assertEqual(list(self.dest.iterdir()), [])
            return ''
        status, output = self.run_cli('tv', '--apply', responses=self.prompts() * 2, answers=approve)
        self.assertEqual(status, 0, output)
        self.assertIn('Season directory: ' + str(path), output)
        self.assertIn('Season size: <0.01 GB', output)
        self.assertIn('Transferring 1/2:', output)
        self.assertIn('Transferring 2/2:', output)
        self.assertLess(output.rindex('[Y/n]'), output.index('Transferring 1/2:'))
        self.assertEqual(set((self.root / 'locale.log').read_text().splitlines()), {'C:C'})
        options = (self.root / 'rsync.log').read_text()
        self.assertIn('--progress', options)
        self.assertIn('--rsync-path=env LC_ALL=C LANG=C rsync', options)

    def test_multiple_seasons_and_shows(self):
        self.prepare(name='Alpha')
        self.prepare(name='Alpha', season='Season 02')
        self.prepare(name='Beta')
        status, output = self.run_cli('tv', '--apply', responses=self.prompts() * 3)
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=3', output)
        self.assertEqual(len(list(self.dest.glob('*/Season *'))), 3)

    def test_unsafe_paths_and_destination_symlink(self):
        self.prepare(name='Show')
        (self.dest / 'Show').symlink_to(self.movie, target_is_directory=True)
        status, output = self.run_cli('tv', '--apply', responses=self.prompts())
        self.assertEqual(status, 1, output)
        self.assertFalse((self.movie / 'Season 01').exists())
        text = self.tvconfig.read_text()
        self.tvconfig.write_text(text.replace(str(self.dest), str(self.dest) + '/../escape'))
        status, output = self.run_cli('tv', '--apply')
        self.assertEqual(status, 1, output)
        self.assertIn('traversal', output)

    def test_configure_tv_and_old_config(self):
        self.prepare()
        original = self.config.read_bytes()
        self.tvconfig.unlink()
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        status, output = self.run_cli('configure-tv', responses=[
            ('Absolute TV source:', str(self.tv)), ('Server hostname', 'server'),
            ('SSH username:', 'user'), ('Absolute TV destination:', str(self.dest)),
            ('Save TV settings?', 'yes')])
        self.assertEqual(status, 0, output)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(self.tvconfig.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
