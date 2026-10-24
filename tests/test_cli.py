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
printf '%s:%s\\n' "$LC_ALL" "$LANG" >> "$LOCALE_LOG"
printf '%s\\n' "$*" >> "$SSH_LOG"
# SSH joins command arguments for the remote shell; support --rsync-path too.
while [ "$#" -gt 0 ]; do
    case "$1" in
        -n) shift;;
        -o) shift 2;;
        *) shift; break;; # destination host
    esac
done
exec bash -c 'ln() { "${TEST_LN:-/bin/ln}" "$@"; }; '"$*"

""")
        (tools / 'rsync').write_text('''#!/bin/bash
printf '%s:%s\\n' "$LC_ALL" "$LANG" >> "$LOCALE_LOG"
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
                        LOCALE_LOG=str(self.root / 'locale.log'), SSH_LOG=str(self.root / 'ssh.log'), RSYNC_LOG=str(self.root / 'rsync.log'))

    def run_cli(self, *args, approval=True, responses=None, answers=None):
        master, slave = pty.openpty()
        proc = subprocess.Popen([os.environ.get("MEDIA_SORTER_TEST_BASH", "/bin/bash"), str(CLI), *args], stdin=slave, stdout=slave, stderr=slave, env=self.env)
        os.close(slave)
        output = b''
        approvals = 0
        replies = iter(answers) if answers is not None and not callable(answers) else None
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
                    if text.count('[Y/n] ') > approvals:
                        candidate = text.rsplit('Candidate:', 1)[-1]
                        response = answers(candidate) if callable(answers) else (
                            next(replies) if replies is not None else ('' if approval else 'n'))
                        # None sends terminal EOF, not an empty line (Enter).
                        os.write(master, b'\x04' if response is None else (response + '\n').encode())
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
        target = self.dest / "main's $film.mkv"
        self.assertEqual(target.read_bytes(), b'movie' * 100)
        self.assertEqual(len(list(target.parent.iterdir())), 1)
        self.assertTrue((self.movie / target.name).exists())
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('conflicted=1', output)
        self.assertEqual(output.count('[Y/n] '), 1)
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
        self.assertEqual(output.count('[Y/n] '), 1)
        self.assertFalse((self.dest / "main's $film.mkv").exists())
        self.assertEqual(list(self.dest.iterdir()), [])

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
        self.assertIn('100%', output)
        target = self.dest / "main's $film.mkv"
        self.assertEqual(target.read_bytes(), (self.movie / target.name).read_bytes())

    def test_all_top_level_candidates_remain_selectable(self):
        for name in ['Extra Ordinary (2019).mkv', 'bonus.mkv', 'sample.mkv']:
            with self.subTest(name=name):
                candidate = self.movie / name
                candidate.write_bytes(b'title')
                def select_title(menu):
                    # Match Bash %q output for the known title.
                    escaped = name.replace(' ', r'\ ').replace('(', r'\(').replace(')', r'\)')
                    return '' if menu.lstrip().startswith(escaped + ' (') else 'n'
                status, output = self.run_cli('movies', '--apply', answers=select_title)
                self.assertEqual(status, 0, output)
                self.assertEqual((self.dest / name).read_bytes(), b'title')
                self.assertFalse((self.dest / 'extras').exists())
                candidate.unlink()

    def test_multiple_movies_and_candidates(self):
        second = self.source / 'Second (2020)'
        second.mkdir()
        (second / 'small.mkv').write_bytes(b'x')
        (second / 'large.mkv').write_bytes(b'xx')
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=2', output)
        self.assertEqual((self.dest / 'large.mkv').read_bytes(), b'xx')

    def test_no_then_enter_selects_next_largest_only(self):
        (self.movie / 'middle.mkv').write_bytes(b'x' * 20)
        status, output = self.run_cli('movies', '--apply', answers=['N', ''])
        self.assertEqual(status, 0, output)
        self.assertEqual(re.findall(r'Candidate: .*?\((\d+) bytes;', output), ['500', '20'])
        self.assertIn('transferred=1', output)
        self.assertEqual([p.name for p in self.dest.iterdir()], ['middle.mkv'])

    def test_largest_first_and_exhaustion(self):
        (self.movie / 'middle.mkv').write_bytes(b'x' * 20)
        (self.movie / 'zero.mkv').touch()
        status, output = self.run_cli('movies', '--apply', approval=False)
        self.assertEqual(status, 0, output)
        self.assertEqual(re.findall(r'Candidate: .*?\((\d+) bytes;', output), ['500', '20', '5', '0'])
        self.assertIn('skipped=1', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_declining_folder_counts_once_and_continues(self):
        second = self.source / 'Healthy'
        second.mkdir()
        (second / 'accepted.mkv').write_bytes(b'movie')
        status, output = self.run_cli('movies', '--apply',
                                      answers=lambda candidate: '' if 'accepted.mkv' in candidate else 'n')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=1 skipped=1', output)
        self.assertIn('approved=1', output)
        self.assertFalse((self.dest / self.movie.name).exists())
        self.assertEqual((self.dest / 'accepted.mkv').read_bytes(), b'movie')

    def test_case_insensitive_responses_and_invalid_reprompt(self):
        for yes in ['y', 'Y', 'yes', 'YeS', 'YES']:
            status, output = self.run_cli('movies', answers=['invalid', '1', yes])
            self.assertEqual(status, 0, output)
            self.assertEqual(output.count('Candidate:'), 1)
            self.assertEqual(output.count('Please enter Y or N'), 2)
            self.assertIn('approved=1', output)
        for no in ['n', 'N', 'no', 'No', 'NO']:
            status, output = self.run_cli('movies', answers=[no, no])
            self.assertEqual(status, 0, output)
            self.assertIn('skipped=1', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_eof_stops_without_approval_or_transfer(self):
        second = self.source / 'Another movie'
        second.mkdir()
        (second / 'main.mkv').write_bytes(b'movie')
        (second / 'other.mkv').write_bytes(b'other')
        for answers in [[None], ['invalid', None], ['n', None]]:
            status, output = self.run_cli('movies', '--apply', answers=answers)
            self.assertNotEqual(status, 0, output)
            self.assertIn('Input ended', output)
            self.assertIn('approved=0', output)
            self.assertEqual(output.count('Movie folder:'), 1)
            self.assertEqual(list(self.dest.iterdir()), [])

    def test_mixed_layout_multiple_loose_movies_and_conflicts(self):
        loose_names = ["Loose's $(touch BAD) (2020).mkv", 'Another.Movie.MKV']
        for name in loose_names:
            (self.source / name).write_bytes(b'loose')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'hidden.mkv').write_bytes(b'hidden')
        (self.source / 'linked-folder').symlink_to(outside, target_is_directory=True)
        (self.source / 'linked.mkv').symlink_to(outside / 'hidden.mkv')
        (self.source / 'notes.txt').write_text('ignore')
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        self.assertIn('approved=3', output)
        self.assertEqual(list(self.dest.iterdir()), [])
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=3', output)
        self.assertEqual(output.count('[Y/n] '), 3)
        self.assertEqual(len(list(self.dest.iterdir())), 3)
        for name in loose_names:
            self.assertEqual((self.dest / name).read_bytes(), b'loose')
            self.assertEqual((self.source / name).read_bytes(), b'loose')
        self.assertFalse((self.root / 'BAD').exists())
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('conflicted=3', output)
        self.assertEqual(output.count('[Y/n] '), 3)

    def test_decline_loose_movie_and_folder_continue_independently(self):
        (self.source / 'declined.mkv').write_bytes(b'no')
        (self.source / 'accepted.mkv').write_bytes(b'yes')
        status, output = self.run_cli('movies', '--apply',
                                      answers=lambda candidate: '' if 'accepted.mkv' in candidate else 'n')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=1 skipped=2', output)
        self.assertEqual([p.name for p in self.dest.iterdir()], ['accepted.mkv'])
        self.assertEqual((self.dest / 'accepted.mkv').read_bytes(), b'yes')

    def test_same_basename_across_folders_and_loose_file_conflicts(self):
        shutil.rmtree(self.movie)
        originals = [self.source / 'same.mkv']
        for folder in ['First movie', 'Second movie']:
            directory = self.source / folder
            directory.mkdir()
            originals.append(directory / 'same.mkv')
            (directory / 'alternate.mkv').write_bytes(b'x')
        for i, source in enumerate(originals):
            source.write_bytes(('original-' + str(i)).encode())
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=1 skipped=0 conflicted=2', output)
        self.assertEqual(output.count('[Y/n] '), 3)
        self.assertEqual([p.name for p in self.dest.iterdir()], ['same.mkv'])
        winner = (self.dest / 'same.mkv').read_bytes()
        self.assertIn(winner, [p.read_bytes() for p in originals])
        for i, source in enumerate(originals):
            self.assertEqual(source.read_bytes(), ('original-' + str(i)).encode())
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=0 skipped=0 conflicted=3', output)
        self.assertEqual((self.dest / 'same.mkv').read_bytes(), winner)

    def test_flat_destination_display(self):
        shutil.rmtree(self.movie)
        (self.source / 'plain.mkv').write_bytes(b'movie')
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        destination_line = next(line for line in output.splitlines() if line.startswith('Destination:'))
        self.assertTrue(destination_line.endswith('/plain.mkv'), output)
        self.assertNotIn('/plain/plain.mkv', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_mixed_formats_rank_by_size_and_select_only_one(self):
        mp4 = self.movie / "Feature's $movie.Mp4"
        mp4.write_bytes(b'p' * 1000)
        (self.movie / 'extras' / 'nested.mp4').write_bytes(b'n' * 2000)
        status, output = self.run_cli('movies', approval=False)
        self.assertEqual(status, 0, output)
        self.assertEqual(re.findall(r'Candidate: .*?\((\d+) bytes;', output), ['1000', '500', '5'])
        self.assertNotIn('nested.mp4', output)
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertEqual(output.count('[Y/n] '), 1)
        self.assertEqual((self.dest / mp4.name).read_bytes(), mp4.read_bytes())
        (self.dest / mp4.name).unlink()
        status, output = self.run_cli('movies', '--apply', answers=['n', ''])
        self.assertEqual(status, 0, output)
        self.assertEqual([p.name for p in self.dest.iterdir()], ["main's $film.mkv"])
        self.assertTrue(mp4.exists())

    def test_loose_mp4_case_preview_transfer_and_exclusions(self):
        shutil.rmtree(self.movie)
        names = ['lower.mp4', 'UPPER.MP4', 'Mixed.mP4', 'existing.MKV']
        for name in names:
            (self.source / name).write_bytes(b'movie')
        for name in ['ignore.avi', 'ignore.mp4.txt']:
            (self.source / name).write_bytes(b'ignore')
        (self.source / 'linked.mp4').symlink_to(self.source / 'lower.mp4')
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        self.assertIn('approved=4', output)
        self.assertEqual(list(self.dest.iterdir()), [])
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=4', output)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(names))
        for name in names:
            self.assertEqual((self.dest / name).read_bytes(), (self.source / name).read_bytes())

    def test_mp4_collision_between_folder_and_loose_file(self):
        (self.movie / 'same.mp4').write_bytes(b'folder' * 200)
        (self.source / 'same.mp4').write_bytes(b'loose')
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('transferred=1 skipped=0 conflicted=1', output)
        self.assertEqual(output.count('[Y/n] '), 2)
        self.assertEqual([p.name for p in self.dest.iterdir()], ['same.mp4'])
        winner = (self.dest / 'same.mp4').read_bytes()
        self.assertIn(winner, [b'folder' * 200, b'loose'])
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertIn('conflicted=2', output)
        self.assertEqual((self.dest / 'same.mp4').read_bytes(), winner)
        self.assertEqual((self.movie / 'same.mp4').read_bytes(), b'folder' * 200)
        self.assertEqual((self.source / 'same.mp4').read_bytes(), b'loose')

    def test_batch_collects_all_approvals_before_transfer(self):
        (self.source / 'loose.mp4').write_bytes(b'loose')
        seen = []
        def approve(candidate):
            self.assertEqual(list(self.dest.iterdir()), [])
            self.assertFalse((self.root / 'rsync.log').exists())
            seen.append(candidate)
            return ''
        status, output = self.run_cli('movies', '--apply', answers=approve)
        self.assertEqual(status, 0, output)
        self.assertEqual(len(seen), 2)
        self.assertIn('transferred=2', output)
        self.assertLess(output.rindex('[Y/n] '), output.index('Transfer phase:'))
        self.assertIn('Transferring 1/2:', output)
        self.assertIn('Transferring 2/2:', output)

    def test_eof_cancels_previously_approved_batch(self):
        (self.source / 'loose.mp4').write_bytes(b'loose')
        status, output = self.run_cli('movies', '--apply', answers=['', None])
        self.assertNotEqual(status, 0, output)
        self.assertIn('approved=1', output)
        self.assertIn('batch cancelled', output)
        self.assertIn('transferred=0', output)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertFalse((self.root / 'rsync.log').exists())

    def test_missing_approved_source_is_reported_before_staging(self):
        def approve(candidate):
            (self.movie / "main's $film.mkv").unlink()
            return ''
        status, output = self.run_cli('movies', '--apply', answers=approve)
        self.assertNotEqual(status, 0, output)
        self.assertIn('no longer a readable regular file', output)
        self.assertIn('failed=1', output)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_collision_created_during_selection_is_not_overwritten(self):
        def approve(candidate):
            (self.dest / "main's $film.mkv").write_bytes(b'existing')
            return ''
        status, output = self.run_cli('movies', '--apply', answers=approve)
        self.assertEqual(status, 0, output)
        self.assertIn('conflicted=1', output)
        self.assertEqual((self.dest / "main's $film.mkv").read_bytes(), b'existing')
        self.assertFalse((self.root / 'rsync.log').exists())

    def test_portable_locale_and_progress_preserve_errors(self):
        self.env.update(LC_ALL='media_sorter_missing.UTF-8', LANG='media_sorter_missing.UTF-8')
        status, output = self.run_cli('movies', '--apply')
        self.assertEqual(status, 0, output)
        self.assertEqual(set((self.root / 'locale.log').read_text().splitlines()), {'C:C'})
        self.assertNotIn('cannot change locale', output.split('Selection phase:', 1)[1])
        for line in (self.root / 'ssh.log').read_text().splitlines():
            self.assertIn('export LC_ALL=C LANG=C;', line)
        options = (self.root / 'rsync.log').read_text()
        self.assertIn('--progress', options)
        self.assertIn('--rsync-path=env LC_ALL=C LANG=C rsync', options)
        self.dest.joinpath("main's $film.mkv").unlink()
        self.dest.rmdir()
        status, output = self.run_cli('movies', '--apply')
        self.assertNotEqual(status, 0, output)
        self.assertIn('Remote destination must exist and be writable', output)

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
