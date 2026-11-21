"""Opt-in real Chromium test; pip install playwright, provide a local Chromium.

MEDIA_SORTER_BROWSER_TEST=1 MEDIA_SORTER_CHROMIUM=/path/to/chromium python3 ...
No browser, package or media downloads are performed by this test.
"""
import os
from pathlib import Path
import unittest
import test_web


@unittest.skipUnless(os.environ.get('MEDIA_SORTER_BROWSER_TEST') == '1', 'Opt-in local Chromium test')
class BrowserIntegration(unittest.TestCase):
    setUp = test_web.WebIntegration.setUp
    stop_server = test_web.WebIntegration.stop_server
    prepare = test_web.WebIntegration.prepare

    def test_movie_and_tv_browser_workflows(self):
        from playwright.sync_api import sync_playwright, expect
        malicious = '<img src=x onerror=alert(1)>.mkv'
        (self.movie / "main's $film.mkv").rename(self.movie / malicious)
        self.prepare()
        movie_dest = self.root / "Library's movies"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get('MEDIA_SORTER_CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            dialogs = []
            page.on('dialog', lambda dialog: (dialogs.append(dialog.type), dialog.accept()))
            page.goto(self.server.origin + '/#' + self.server.token)
            page.get_by_role('button', name='Upload Movies', exact=True).click()
            expect(page.locator('#prompt')).to_contain_text('Preview this file?')
            page.reload()
            expect(page.locator('#prompt')).to_contain_text('Preview this file?')
            expect(page.locator('#output')).to_contain_text(malicious)
            self.assertEqual(page.locator('img').count(), 0)
            page.get_by_role('button', name='Submit answer').click()
            expect(page.locator('#status')).to_contain_text('Batch finished')
            self.assertEqual(list(movie_dest.iterdir()), [])
            page.locator('#apply').check()
            page.get_by_role('button', name='Upload Movies', exact=True).click()
            expect(page.locator('#prompt')).to_contain_text('Send this file?')
            page.get_by_role('button', name='Submit answer').click()
            expect(page.locator('#status')).to_contain_text('Batch finished')
            self.assertEqual((movie_dest / malicious).read_bytes(), b'movie' * 100)
            self.assertEqual(dialogs, ['confirm'])
            for apply in (False, True):
                page.locator('#apply').set_checked(apply)
                page.get_by_role('button', name='Upload TV Shows', exact=True).click()
                expect(page.locator('#prompt')).to_contain_text('Show name')
                page.locator('#answer').fill('Browser Show (2026)')
                page.get_by_role('button', name='Submit answer').click()
                expect(page.locator('#prompt')).to_contain_text('Season number')
                page.locator('#answer').fill('2')
                page.get_by_role('button', name='Submit answer').click()
                expect(page.locator('#prompt')).to_contain_text('Send this season?' if apply else 'Preview this season?')
                expect(page.locator('#output')).to_contain_text('Contents: 2 files')
                page.get_by_role('button', name='Submit answer').click()
                expect(page.locator('#status')).to_contain_text('Batch finished')
                if not apply:
                    self.assertEqual(list(self.dest.iterdir()), [])
            self.assertTrue((self.dest / 'Browser Show (2026)' / 'Season 02' / 'sub folder' / 'S01E02.mp4').is_file())
            page.get_by_role('button', name='Upload Movies', exact=True).click()
            expect(page.locator('#prompt')).to_contain_text('Send this file?')
            page.get_by_role('button', name='Cancel batch').click()
            expect(page.locator('#status')).to_contain_text('Batch cancelled')
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            self.assertEqual(errors, [])
            browser.close()

