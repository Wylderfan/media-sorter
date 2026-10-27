"""Prompt defaults are suggestions, isolated to one invocation."""
import importlib.util
import pathlib
import unittest
from unittest import mock
import test_cli

spec = importlib.util.spec_from_file_location('tv_defaults_module', test_cli.CLI.parent.parent / 'lib/tv.py')
tv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tv)


class IdentityDefaults(unittest.TestCase):
    def identify(self, folder, context, previous, replies):
        with mock.patch.object(tv, 'ask', side_effect=replies) as ask, mock.patch('builtins.print'):
            result = tv.identity(pathlib.Path(folder), context, previous)
        return result, [call.args[0] for call in ask.call_args_list]

    def test_first_item_requires_both_values_and_invalid_input_does_not_advance(self):
        result, prompts = self.identify('Unlabeled', '', None,
                                        ['', '../unsafe', 'Show', '', 'invalid', '100', '4'])
        self.assertEqual(result, ('Show', 'Season 04'))
        self.assertTrue(all('[]' in p for p in prompts))

    def test_fallback_and_corrected_season(self):
        result, prompts = self.identify('Unlabeled', '', ('Show', 'Season 01'), ['', '7'])
        self.assertEqual(result, ('Show', 'Season 07'))
        self.assertIn('[Show]', prompts[0])
        self.assertIn('[2]', prompts[1])
        result, prompts = self.identify('Another', '', result, ['', ''])
        self.assertEqual(result, ('Show', 'Season 08'))
        self.assertIn('[8]', prompts[1])

    def test_show_switch_clears_increment_until_season_confirmed(self):
        result, prompts = self.identify('Unlabeled', '', ('Old', 'Season 03'), ['New', '6'])
        self.assertEqual(result, ('New', 'Season 06'))
        self.assertIn('[]', prompts[1])
        result, prompts = self.identify('Another', '', result, ['Old', '1'])
        self.assertIn('[]', prompts[1])  # Only the immediately previous identity is remembered.

    def test_directory_identity_and_season_take_precedence(self):
        for folder, context, expected in [
            ('New (2020) Season 09', '', ('New (2020)', 'Season 09')),
            ('Season 02', 'New', ('New', 'Season 02')),
            ('Season 04', '', ('Old', 'Season 04')),
            ('Season 00', 'Old', ('Old', 'Season 00'))]:
            with self.subTest(folder=folder, context=context):
                result, _ = self.identify(folder, context, ('Old', 'Season 07'), ['', ''])
                self.assertEqual(result, expected)
        result, prompts = self.identify('Unknown', 'New', ('Old', 'Season 07'), ['', '1'])
        self.assertIn('[New]', prompts[0])
        self.assertIn('[]', prompts[1])
        # Correcting the inferred show back to the previous show permits increment.
        result, prompts = self.identify('Unknown', 'New', ('Old', 'Season 07'), ['Old', ''])
        self.assertEqual(result, ('Old', 'Season 08'))

    def test_specials_and_upper_bound(self):
        result, prompts = self.identify('Unknown', '', ('Show', 'Season 00'), ['', ''])
        self.assertEqual(result, ('Show', 'Season 01'))
        result, prompts = self.identify('Unknown', '', ('Show', 'Season 99'), ['', '0'])
        self.assertIn('[]', prompts[1])
        self.assertEqual(result, ('Show', 'Season 00'))

    def test_eof_never_returns_partial_identity(self):
        previous = ('Show', 'Season 02')
        for replies in [[EOFError()], ['Corrected', EOFError()]]:
            with mock.patch('builtins.input', side_effect=replies), mock.patch('builtins.print'):
                with self.assertRaisesRegex(ValueError, 'Input ended'):
                    tv.identity(pathlib.Path('Unknown'), '', previous)
            self.assertEqual(previous, ('Show', 'Season 02'))
