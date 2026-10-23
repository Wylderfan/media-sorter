"""Local fixtures only: no SSH server, network, or real media library."""
import os
import pathlib
import pty
import select
import shutil
import subprocess
import tempfile
import unittest

CLI = pathlib.Path(__file__).resolve().parents[1] / 'bin/media-sorter'


class CLIIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.source = self.root / 'Movies'
        self.dest = self.root / "Library's movies"
        self.movie = self.source / "Film's $(touch BAD) (2024)"
        self.movie.mkdir(parents=True)
        self.dest.mkdir()
        (self.movie / "main's $film.mkv").write_bytes(b'movie' * 100)
        (self.movie / 'extra.mkv').write_bytes(b'extra')
        (self.movie / 'extras').mkdir()
        (self.movie / 'extras' / 'bonus.mkv').write_bytes(b'bonus')
        self.config = self.root / 'config'
        self.config.write_text(f'{self.source}\nserver\nuser\n{self.dest}\n')
        tools = self.root / 'tools'
        tools.mkdir()
        (tools / 'ssh').write_text('#!/bin/bash\n[ "${FAIL_SSH:-}" != yes ] || exit 255\n# Real rsync invokes a remote server through its remote-shell transport.\nwhile [ "$#" -gt 0 ]; do\n    if [ "$1" = rsync ]; then shift; exec "${REAL_RSYNC:?}" "$@"; fi\n    if [ "$#" -eq 1 ]; then exec bash -c "$1"; fi\n    shift\ndone\n')
        (tools / 'rsync').write_text('''#!/bin/bash
[ -z "${REAL_RSYNC:-}" ] || exec "$REAL_RSYNC" "$@"
if [ "$1" = --help ]; then echo --protect-args; exit 0; fi
[ "${FAIL_TRANSFER:-}" != yes ] || { dst=${@: -1}; echo partial > "${dst#*:}"; exit 23; }
args=("$@")
src=${args[${#args[@]}-2]}
dst=${args[${#args[@]}-1]}
cp -- "$src" "${dst#*:}"
''')
        for tool in tools.iterdir():
            tool.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', MEDIA_SORTER_CONFIG=str(self.config))

    def run_cli(self, *args, approval=True, responses=None):
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(CLI), *args], stdin=slave, stdout=slave, stderr=slave, env=self.env)
        os.close(slave)
        output = b''
        answered = set()
        pending = list(responses or [])
        try:
            while True:
                if select.select([master], [], [], 5)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                    text = output.decode(errors='replace')
                    if pending and pending[0][0] in text:
                        _, response = pending.pop(0)
                        os.write(master, (response + '\n').encode())
                    if 'Choose a file number' in text and 'choice' not in answered:
                        # find output order rather than assuming filesystem enumeration order
                        import re
                        number = re.search(r'(\d+): .*\(largest;', text).group(1)
                        os.write(master, (number + '\n').encode())
                        answered.add('choice')
                    if 'Type yes:' in text and 'approval' not in answered:
                        os.write(master, b'yes\n' if approval else b'\n')
                        answered.add('approval')
                else:
                    proc.kill()
                    self.fail('CLI timed out: ' + output.decode(errors='replace'))
            return proc.wait(timeout=5), output.decode(errors='replace')
        finally:
            os.close(master)

    def test_preview(self):
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        self.assertIn('Would transfer', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_transfer_and_collision(self):
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        target = self.dest / self.movie.name / "main's $film.mkv"
        self.assertEqual(target.read_bytes(), b'movie' * 100)
        self.assertEqual(len(list(target.parent.iterdir())), 1)
        self.assertTrue((self.movie / target.name).exists())
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('conflicted=1', output)
        self.assertFalse((self.root / 'BAD').exists())

    def test_skip(self):
        status, output = self.run_cli('movies', '--apply', approval=False)
        self.assertEqual(status, 0, output)
        self.assertIn('skipped=1', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_noninteractive_and_unsupported(self):
        for args in [('movies', '--apply'), ('tv',), ('both',), ('movies', '--unknown')]:
            result = subprocess.run([str(CLI), *args], stdin=subprocess.DEVNULL, capture_output=True, env=self.env)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_failed_transfer_never_publishes(self):
        self.env['FAIL_TRANSFER'] = 'yes'
        status, output = self.run_cli('movies', '--apply')
        self.assertNotEqual(status, 0, output)
        self.assertIn('failed=1', output)
        self.assertFalse((self.dest / self.movie.name / "main's $film.mkv").exists())
        self.assertEqual(list((self.dest / self.movie.name).iterdir()), [])

    def test_unavailable_host(self):
        self.env['FAIL_SSH'] = 'yes'
        status, output = self.run_cli('movies', '--apply')
        self.assertNotEqual(status, 0, output)
        self.assertIn('SSH failed', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_source_override_does_not_save(self):
        alternate = self.root / 'Other batch'
        alternate.mkdir()
        before = self.config.read_bytes()
        status, output = self.run_cli('movies', '--source', str(alternate), '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('approved=0', output)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_configure_and_cancel(self):
        before = self.config.read_bytes()
        prompts = [('Absolute Movies source:', str(self.source)),
                   ('Server hostname', 'server'), ('SSH username:', 'user'),
                   ('Absolute Movies destination:', str(self.dest)),
                   ('Save settings?', 'no')]
        status, output = self.run_cli('configure', responses=prompts)
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.config.read_bytes(), before)
        self.config.unlink()
        prompts[-1] = ('Save settings?', 'yes')
        status, output = self.run_cli('movies', responses=prompts)
        self.assertEqual(status, 0, output)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_unsupported_rsync(self):
        tool = self.root / 'tools' / 'rsync'
        tool.write_text('#!/bin/bash\necho old-rsync\n')
        status, output = self.run_cli('movies', '--apply')
        self.assertNotEqual(status, 0, output)
        self.assertIn('Local rsync must support', output)

    def test_real_rsync_protocol(self):
        real = os.environ.get('MEDIA_SORTER_TEST_RSYNC') or shutil.which('rsync')
        if not real:
            self.skipTest('Set MEDIA_SORTER_TEST_RSYNC to test a real rsync binary')
        self.env['REAL_RSYNC'] = real
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=1', output)
        target = self.dest / self.movie.name / "main's $film.mkv"
        self.assertEqual(target.read_bytes(), (self.movie / target.name).read_bytes())

    def test_missing_destination(self):
        self.dest.rmdir()
        status, output = self.run_cli('movies', '--apply')
        self.assertNotEqual(status, 0, output)
        self.assertIn('destination must exist', output)


if __name__ == '__main__':
    unittest.main()
