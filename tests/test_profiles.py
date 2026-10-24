"""Profile compatibility and path review use only isolated temporary directories."""
import pathlib
import unittest
import test_tv


class ProfileIntegration(unittest.TestCase):
    setUp = test_tv.TVIntegration.setUp
    run_cli = test_tv.TVIntegration.run_cli
    prepare = test_tv.TVIntegration.prepare
    prompts = test_tv.TVIntegration.prompts

    def movie_setup(self, save='yes'):
        return [('Absolute Movies source:', ''), ('Server hostname', ''),
                ('SSH username:', ''), ('Absolute Movies destination:', ''),
                ('Save settings?', save)]

    def test_explicit_movie_profile_preserves_legacy_and_wins(self):
        before = self.config.read_bytes()
        named = pathlib.Path(str(self.config) + '.movies')
        status, output = self.run_cli('configure', 'movies', responses=self.movie_setup('no'))
        self.assertNotEqual(status, 0, output)
        self.assertFalse(named.exists())
        self.assertEqual(self.config.read_bytes(), before)
        status, output = self.run_cli('configure', 'movies', responses=self.movie_setup())
        self.assertEqual(status, 0, output)
        self.assertEqual(named.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(named.stat().st_mode & 0o777, 0o600)
        self.assertLess(output.index('Media: movies'), output.index('Save settings?'))
        self.assertIn('Remote library: user@server:' + str(self.dest), output)
        self.config.write_text('invalid inactive legacy file\n')
        status, output = self.run_cli('movies')
        self.assertEqual(status, 0, output)
        self.assertIn('Configuration: ' + str(named), output)
        self.assertEqual(list(self.dest.iterdir()), [])
        status, output = self.run_cli('configure', responses=self.movie_setup('no'))
        self.assertNotEqual(status, 0, output)
        self.assertEqual(named.read_bytes(), before)
        named.write_text('invalid named profile\n')
        self.config.write_bytes(before)
        status, output = self.run_cli('movies')
        self.assertNotEqual(status, 0, output)
        self.assertIn('Invalid configuration', output)

    def test_tv_alias_keeps_independent_values_and_reviews(self):
        self.prepare()
        movies = self.config.read_bytes()
        tv = self.tvconfig.read_bytes()
        responses = [('Absolute TV source:', ''), ('Server hostname', ''),
                     ('SSH username:', ''), ('Absolute TV destination:', ''),
                     ('Save TV settings?', 'no')]
        status, output = self.run_cli('configure', 'tv', responses=responses)
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.tvconfig.read_bytes(), tv)
        self.assertEqual(self.config.read_bytes(), movies)
        responses[-1] = ('Save TV settings?', 'yes')
        status, output = self.run_cli('configure', 'tv', responses=responses)
        self.assertEqual(status, 0, output)
        self.assertLess(output.index('Remote library:'), output.index('Save TV settings?'))
        self.assertIn('Media: tv', output)
        self.assertEqual(self.tvconfig.read_bytes(), tv)
        self.assertEqual(self.config.read_bytes(), movies)

    def test_overlap_is_rejected_in_both_directions(self):
        self.prepare()
        original = self.tvconfig.read_text()
        movie_dest = self.root / "Library's movies"
        for replacement in [original.replace(str(self.tv), str(self.source)),
                            original.replace(str(self.dest), str(movie_dest)),
                            original.replace(str(self.dest), str(movie_dest / 'nested'))]:
            self.tvconfig.write_text(replacement)
            for command in ['tv', 'movies']:
                status, output = self.run_cli(command)
                self.assertNotEqual(status, 0, output)
                self.assertIn('roots overlap', output)
            self.assertEqual(self.tvconfig.read_text(), replacement)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(list(movie_dest.iterdir()), [])

    def test_missing_tv_never_uses_movie_paths(self):
        before = self.config.read_bytes()
        status, output = self.run_cli('tv')
        self.assertNotEqual(status, 0, output)
        self.assertIn('TV configuration missing', output)
        self.assertEqual(self.config.read_bytes(), before)
        status, output = self.run_cli('configure', 'music')
        self.assertNotEqual(status, 0, output)
        self.assertFalse(pathlib.Path(str(self.config) + '.music').exists())
