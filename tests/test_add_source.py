"""Actual CLI setup/selection with isolated configs, media and SSH fixtures."""
import pathlib
import subprocess
import unittest
import test_cli
import test_tv


class AddSourceIntegration(unittest.TestCase):
    setUp = test_tv.TVIntegration.setUp
    run_cli = test_cli.CLIIntegration.run_cli
    prepare = test_tv.TVIntegration.prepare
    prompts = test_tv.TVIntegration.prompts

    def setup_answers(self, media='movies', name='new-rips', source=None, dest=None, save='yes'):
        return [('Media type', media), ('Profile name:', name),
                ('Absolute source directory:', str(source or self.source)),
                ('Server hostname', 'server'), ('SSH username:', 'user'),
                ('Absolute destination directory:', str(dest or self.dest)),
                ('Create source profile?', save)]

    def named(self, media='movies', name='new-rips'):
        return pathlib.Path(str(self.config) + '.sources') / (name + '.' + media)

    def test_movie_round_trip_literal_paths_and_legacy(self):
        source = self.root / "New rips' $(touch BAD) \\ folder"
        source.mkdir()
        filename = "Film's $title.mkv"
        (source / filename).write_bytes(b'new movie')
        before = self.config.read_bytes()
        status, output = self.run_cli('add-source', responses=self.setup_answers(source=source))
        self.assertEqual(status, 0, output)
        self.assertIn('movies --profile new-rips', output)
        self.assertFalse((self.root / 'ssh.log').exists())
        self.assertFalse((self.root / 'rsync.log').exists())
        self.assertEqual(list(self.dest.iterdir()), [])
        config = self.named()
        self.assertEqual(config.read_text(), f'{source}\nserver\nuser\n{self.dest}\n')
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        status, output = self.run_cli('movies', '--profile', 'new-rips')
        self.assertEqual(status, 0, output)
        self.assertIn('Would transfer', output)
        self.assertEqual(list(self.dest.iterdir()), [])
        status, output = self.run_cli('movies', '--profile', 'new-rips', '--apply')
        self.assertEqual(status, 0, output)
        self.assertEqual((self.dest / filename).read_bytes(), b'new movie')
        self.assertTrue((source / filename).exists())
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        self.assertIn(str(self.movie), output)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.root / 'BAD').exists())

    def test_tv_round_trip(self):
        season = self.prepare()
        legacy = self.config.read_bytes()
        default_tv = self.tvconfig.read_bytes()
        status, output = self.run_cli('add-source', responses=self.setup_answers('tv', source=self.tv))
        self.assertEqual(status, 0, output)
        self.assertFalse((self.root / 'ssh.log').exists())
        for options in [[], ['--apply']]:
            status, output = self.run_cli('tv', '--profile', 'new-rips', *options, responses=self.prompts())
            self.assertEqual(status, 0, output)
        target = self.dest / season.parent.name / season.name
        self.assertEqual((target / "S01E01's $file [x];.mkv").read_bytes(), b'one')
        self.assertEqual(self.config.read_bytes(), legacy)
        self.assertEqual(self.tvconfig.read_bytes(), default_tv)

    def test_cancel_eof_invalid_and_no_overwrite(self):
        for responses in [self.setup_answers(save='no'),
                          [('Media type', '\x04')],
                          [('Media type', 'music')],
                          [('Media type', 'movies'), ('Profile name:', '../escape')],
                          self.setup_answers(source='relative'),
                          self.setup_answers(dest='/'),
                          self.setup_answers(dest='/a/../b')]:
            status, output = self.run_cli('add-source', responses=responses)
            self.assertNotEqual(status, 0, output)
            self.assertFalse(self.named().exists())
        status, output = self.run_cli('add-source', responses=self.setup_answers())
        self.assertEqual(status, 0, output)
        before = self.named().read_bytes()
        status, output = self.run_cli('add-source', responses=self.setup_answers())
        self.assertNotEqual(status, 0, output)
        self.assertIn('already exists', output)
        self.assertEqual(self.named().read_bytes(), before)
        self.named().unlink()
        self.named().symlink_to(self.root / 'absent')
        status, output = self.run_cli('add-source', responses=self.setup_answers())
        self.assertNotEqual(status, 0, output)
        self.assertTrue(self.named().is_symlink())
        self.assertFalse((self.root / 'absent').exists())
        self.assertFalse((self.root / 'ssh.log').exists())

    def test_selector_errors_and_noninteractive_setup(self):
        for command in ['movies', 'tv']:
            for args in [[], ['../escape'], ['missing']]:
                status, output = self.run_cli(command, '--profile', *args)
                self.assertNotEqual(status, 0, output)
        result = subprocess.run([str(test_cli.CLI), 'add-source'], env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.named().parent.exists())

    def test_named_profiles_enforce_cross_media_separation(self):
        status, output = self.run_cli('add-source', responses=self.setup_answers())
        self.assertEqual(status, 0, output)
        # Named movies are checked even if the default is absent.
        self.config.unlink()
        status, output = self.run_cli('add-source', responses=self.setup_answers('tv'))
        self.assertNotEqual(status, 0, output)
        self.assertIn('roots overlap', output)
        self.assertFalse(self.named('tv').exists())
        self.named('tv').write_bytes(self.named().read_bytes())
        for command in ['movies', 'tv']:
            status, output = self.run_cli(command, '--profile', 'new-rips')
            self.assertNotEqual(status, 0, output)
            self.assertIn('roots overlap', output)
        self.assertFalse((self.root / 'ssh.log').exists())
