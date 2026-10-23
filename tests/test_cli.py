"""Local fixtures only: no SSH server, network, or real media library."""
import os
import pathlib
import pty
import re
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
        (tools / 'ssh').write_text("""#!/bin/bash
[ "${FAIL_SSH:-}" != yes ] || exit 255
printf '%s\\n' "$*" >> "$SSH_LOG"
while [ "$#" -gt 0 ]; do
    if [ "$1" = rsync ]; then shift; exec "${REAL_RSYNC:?}" "$@"; fi
    if [ "$#" -eq 1 ]; then
        exec bash -c 'ln() { "${TEST_LN:-/bin/ln}" "$@"; }; '"$1"
    fi
    shift
done
""")
        (tools / 'rsync').write_text('''#!/bin/bash
if [ "$1" != --help ]; then printf '%s\\n' "$*" >> "$RSYNC_LOG"; fi
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
        self.env = dict(os.environ, PATH=f'{tools}:{os.environ["PATH"]}', MEDIA_SORTER_CONFIG=str(self.config),
                        SSH_LOG=str(self.root / 'ssh.log'), RSYNC_LOG=str(self.root / 'rsync.log'))

    def run_cli(self, *args, approval=True, responses=None, choice=None):
        master, slave = pty.openpty()
        proc = subprocess.Popen([os.environ.get("MEDIA_SORTER_TEST_BASH", "/bin/bash"), str(CLI), *args], stdin=slave, stdout=slave, stderr=slave, env=self.env)
        os.close(slave)
        output = b''
        choices = approvals = 0
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
                    if text.count('Choose a file number') > choices:
                        # find output order rather than assuming filesystem enumeration order
                        menu = text.rsplit('Movie folder:', 1)[-1]
                        number = re.search(r'(\d+): .*\(largest;', menu).group(1)
                        if choice is not None:
                            number = choice(menu) if callable(choice) else choice
                        os.write(master, (number + '\n').encode())
                        choices += 1
                    if text.count('Type yes:') > approvals:
                        os.write(master, b'yes\n' if approval else b'\n')
                        approvals += 1
                else:
                    proc.kill()
                    self.fail('CLI timed out: ' + output.decode(errors='replace'))
            return proc.wait(timeout=5), output.decode(errors='replace')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
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

    def test_all_top_level_candidates_remain_selectable(self):
        for name in ['Extra Ordinary (2019).mkv', 'bonus.mkv', 'sample.mkv']:
            with self.subTest(name=name):
                candidate = self.movie / name
                candidate.write_bytes(b'title')
                def select_title(menu):
                    # Match Bash %q output for the known title.
                    escaped = name.replace(' ', r'\ ').replace('(', r'\(').replace(')', r'\)')
                    return re.search(r'(\d+): ' + re.escape(escaped), menu).group(1)
                status, output = self.run_cli('movies', '--apply', choice=select_title)
                self.assertEqual(status, 0, output)
                self.assertEqual((self.dest / self.movie.name / name).read_bytes(), b'title')
                self.assertFalse((self.dest / self.movie.name / 'extras').exists())
                candidate.unlink()

    def test_multiple_movies_and_candidates(self):
        second = self.source / 'Second (2020)'
        second.mkdir()
        (second / 'small.mkv').write_bytes(b'x')
        (second / 'large.mkv').write_bytes(b'xx')
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=2', output)
        self.assertEqual((self.dest / second.name / 'large.mkv').read_bytes(), b'xx')

    def test_choice_edge_cases(self):
        for choice in ['', '0', '999999', '1000000', '-1', 'abc', '1+1']:
            with self.subTest(choice=choice):
                status, output = self.run_cli('movies', '--apply', choice=choice)
                self.assertEqual(status, 0, output)
                self.assertIn('skipped=1', output)
                self.assertEqual(list(self.dest.iterdir()), [])
        status, output = self.run_cli('movies', choice=lambda menu: '00' + re.search(r'(\d+): .*\(largest;', menu).group(1))
        self.assertEqual(status, 0, output)
        self.assertIn('approved=1', output)

    def test_scan_failures_discard_partial_results(self):
        real_find = shutil.which('find')
        tool = self.root / 'tools' / 'find'
        tool.write_text("""#!/bin/bash
"$REAL_FIND" "$@"
if [ "$1" = "$FAIL_SCAN" ]; then echo 'find: Permission denied' >&2; exit 1; fi
""")
        tool.chmod(0o755)
        self.env['REAL_FIND'] = real_find
        second = self.source / 'Healthy'
        second.mkdir()
        (second / 'feature.mkv').write_bytes(b'ok')
        for folder in [self.source, self.movie]:
            with self.subTest(folder=folder):
                self.env['FAIL_SCAN'] = str(folder)
                status, output = self.run_cli('movies')
                self.assertNotEqual(status, 0, output)
                self.assertIn('failed=1', output)
                self.assertIn('cannot scan', output)
                self.assertNotIn('Movie folder: Film', output)
                self.assertIn('approved=' + ('0' if folder == self.source else '1'), output)
                self.assertEqual(list(self.dest.iterdir()), [])

    def test_unreadable_movie_directory(self):
        self.movie.chmod(0)
        try:
            if os.access(self.movie, os.R_OK):
                self.skipTest('Current user bypasses directory permissions')
            status, output = self.run_cli('movies', '--apply')
            self.assertNotEqual(status, 0, output)
            self.assertIn('cannot scan movie folder', output)
            self.assertIn('failed=1', output)
            self.assertIn('approved=0', output)
            self.assertEqual(list(self.dest.iterdir()), [])
        finally:
            self.movie.chmod(0o755)

    def test_transport_options(self):
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        for line in (self.root / 'ssh.log').read_text().splitlines():
            self.assertIn('-o BatchMode=yes -o ConnectTimeout=10', line)
        self.assertIn('-e ssh -o BatchMode=yes -o ConnectTimeout=10',
                      (self.root / 'rsync.log').read_text())

    def test_missing_destination(self):
        self.dest.rmdir()
        status, output = self.run_cli('movies', '--apply')
        self.assertNotEqual(status, 0, output)
        self.assertIn('destination must exist', output)


if __name__ == '__main__':
    unittest.main()
