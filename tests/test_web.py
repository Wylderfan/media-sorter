"""Exercise the actual HTTP portal, shared prompts and CLI with disposable media."""
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import web
import test_cli
import test_tv


class WebIntegration(unittest.TestCase):
    prepare = test_tv.TVIntegration.prepare
    run_cli = test_cli.CLIIntegration.run_cli

    def setUp(self):
        test_tv.TVIntegration.setUp(self)
        self.server = web.Portal(0, self.config, self.env)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, data=None, headers=None, method=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        all_headers = {'Origin': self.server.origin, 'X-Portal-Token': self.server.token,
                       'Content-Type': 'application/json'}
        all_headers.update(headers or {})
        body = raw if raw is not None else None if data is None else json.dumps(data)
        connection.request(method or ('GET' if body is None else 'POST'), path, body, all_headers)
        response = connection.getresponse()
        payload = response.read().decode()
        status = response.status
        connection.close()
        return status, json.loads(payload) if payload.startswith(('{', 'null')) else payload

    def start(self, media='movies', apply=False):
        status, result = self.request('/api/start', {'media': media, 'apply': apply, 'confirm': apply})
        self.assertEqual(status, 200, result)
        return result

    def wait(self, predicate):
        until = time.monotonic() + 10
        while time.monotonic() < until:
            status, result = self.request('/api/state')
            self.assertEqual(status, 200, result)
            if predicate(result):
                return result
            time.sleep(.02)
        self.fail('Portal timed out: ' + repr(result))

    def answer(self, text, answer=''):
        state = self.wait(lambda state: state['prompt'] is not None)
        self.assertIn(text, state['prompt']['text'])
        status, result = self.request('/api/answer', {'job': state['id'], 'prompt': state['prompt']['id'], 'answer': answer})
        self.assertEqual(status, 200, result)
        return state

    def finished(self):
        return self.wait(lambda state: state['code'] is not None)

    def test_movies_parity_preview_apply_duplicate(self):
        status, cli = self.run_cli('movies')
        self.assertEqual(status, 0, cli)
        for apply in (False, True, True):
            self.start(apply=apply)
            self.answer('Send this file?' if apply else 'Preview this file?')
            result = self.finished()
            self.assertEqual(result['code'], 0, result)
            for text in ('Candidate:', 'Destination:', 'Selection complete: 1 approved file(s).'):
                self.assertIn(text, result['output'])
                self.assertIn(text, cli)
            if not apply:
                self.assertEqual(list(self.dest.iterdir()), [])
                self.assertIn('Would transfer', result['output'])
        self.assertIn('conflicted=1', result['output'])
        self.assertEqual((self.dest / "main's $film.mkv").read_bytes(), b'movie' * 100)
        self.assertTrue((self.movie / "main's $film.mkv").exists())
        self.assertFalse((self.root / 'BAD').exists())
        self.assertFalse(list(self.dest.glob('.media-sorter-*')))

    def test_tv_parity_and_identity_correction(self):
        source = self.prepare()
        status, cli = self.run_cli('tv', responses=[('Show name (optional year)', ''), ('Season number,', '')])
        self.assertEqual(status, 0, cli)
        for apply in (False, True, True):
            self.start('tv', apply)
            self.answer('Show name (optional year)', 'Corrected Show (2020)')
            self.answer('Season number,', '7')
            self.answer('Send this season?' if apply else 'Preview this season?')
            result = self.finished()
            self.assertEqual(result['code'], 0, result)
            for text in ('Contents: 2 files', 'Season size:', 'Selection complete: 1 approved season(s).'):
                self.assertIn(text, result['output'])
                self.assertIn(text, cli)
            if not apply:
                self.assertEqual(list(self.dest.iterdir()), [])
        self.assertIn('conflicted=1', result['output'])
        target = self.dest / 'Corrected Show (2020)' / 'Season 07'
        for path in source.rglob('*'):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (target / path.relative_to(source)).read_bytes())
        self.assertFalse(list(self.dest.glob('.media-sorter-tv-*')))

    def test_prompt_validation_skip_eof_retry(self):
        self.start(apply=True)
        stale = self.answer('Send this file?', 'invalid')
        current = self.wait(lambda s: s['prompt'] and s['prompt']['id'] != stale['prompt']['id'])
        for answer in ('bad\nY', '\x03', 'a' * 1025):
            status, _ = self.request('/api/answer', {'job': current['id'], 'prompt': current['prompt']['id'], 'answer': answer})
            self.assertEqual(status, 400)
        status, _ = self.request('/api/answer', {'job': stale['id'], 'prompt': stale['prompt']['id'], 'answer': ''})
        self.assertEqual(status, 400)
        self.answer('Send this file?', 'n')
        self.answer('Send this file?', None)
        self.assertNotEqual(self.finished()['code'], 0)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.start(apply=True)
        self.answer('Send this file?', 'n')
        self.answer('Send this file?', 'no')
        self.assertIn('skipped=1', self.finished()['output'])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_cancel_and_lease_expiry(self):
        for expiry in (False, True):
            self.start(apply=True)
            state = self.wait(lambda s: s['prompt'])
            if expiry:
                self.server.job.last_seen -= web.LEASE_SECONDS + 1
                # Do not heartbeat while waiting for the lease monitor.
                time.sleep(1.2)
            else:
                self.assertEqual(self.request('/api/cancel', {'job': state['id']})[0], 200)
            result = self.finished()
            self.assertTrue(result['cancelled'])
            self.assertNotEqual(result['code'], 0)
            self.assertEqual(list(self.dest.iterdir()), [])

    def test_security_and_allowlist(self):
        for headers in ({'Host': 'attacker.test'}, {'Origin': 'https://attacker.test'},
                        {'X-Portal-Token': ''}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('/api/state', headers=headers)[0], 403)
        self.assertEqual(self.request('/../../etc/passwd')[0], 404)
        for payload in ({'media': '../movies', 'apply': True, 'confirm': True},
                        {'media': 'movies', 'apply': True, 'confirm': False},
                        {'media': 'movies', 'apply': False, 'confirm': False, 'source': '/tmp'},
                        {'media': 'movies; touch BAD', 'apply': False, 'confirm': False}):
            self.assertEqual(self.request('/api/start', payload)[0], 400)
        self.assertEqual(self.request('/api/start', raw='a' * 9000)[0], 400)
        self.start()
        self.assertEqual(self.request('/api/start', {'media': 'movies', 'apply': False, 'confirm': False})[0], 400)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_failed_copy_cleanup_and_retry(self):
        self.env['FAIL_TRANSFER'] = 'yes'
        self.start(apply=True)
        self.answer('Send this file?')
        self.assertIn('failed=1', self.finished()['output'])
        self.assertEqual(list(self.dest.iterdir()), [])
        del self.env['FAIL_TRANSFER']
        self.start(apply=True)
        self.answer('Send this file?')
        self.assertIn('transferred=1', self.finished()['output'])

    def test_cancel_during_copy_removes_private_stage(self):
        tool = self.root / 'tools/rsync'
        tool.write_text(tool.read_text().replace('cp --', 'touch "$COPY_STARTED"\nsleep 30\ncp --'))
        self.env['COPY_STARTED'] = str(self.root / 'copy-started')
        self.start(apply=True)
        self.answer('Send this file?')
        state = self.wait(lambda s: (self.root / 'copy-started').exists())
        self.assertTrue(list(self.dest.glob('.media-sorter-*')))
        self.request('/api/cancel', {'job': state['id']})
        self.assertNotEqual(self.finished()['code'], 0)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_tv_cancel_during_copy_cleans_stage(self):
        self.prepare()
        tool = self.root / 'tools/rsync'
        tool.write_text(tool.read_text().replace('[ -z "${REAL_RSYNC:-}" ]',
            'if [ "$1" != --help ]; then touch "$COPY_STARTED"; sleep 30; fi\n[ -z "${REAL_RSYNC:-}" ]'))
        self.env['COPY_STARTED'] = str(self.root / 'copy-started')
        self.start('tv', True)
        self.answer('Show name')
        self.answer('Season number')
        self.answer('Send this season?')
        state = self.wait(lambda s: (self.root / 'copy-started').exists())
        self.assertTrue(list(self.dest.glob('.media-sorter-tv-*')))
        self.request('/api/cancel', {'job': state['id']})
        self.assertNotEqual(self.finished()['code'], 0)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_tv_malicious_identity_and_symlink(self):
        source = self.prepare()
        self.start('tv', True)
        self.answer('Show name', '../escape')
        self.answer('Show name', 'Safe Show')
        self.answer('Season number', '1')
        self.answer('Send this season?', 'n')
        self.assertEqual(self.finished()['code'], 0)
        self.assertEqual(list(self.dest.iterdir()), [])
        (source / 'bad').symlink_to(self.movie)
        self.start('tv', True)
        result = self.finished()
        self.assertNotEqual(result['code'], 0)
        self.assertIn('Symlink or special file', result['output'])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_output_bounded_and_text_only_ui(self):
        self.start()
        self.wait(lambda s: s['prompt'])
        self.server.job.append('x' * (web.LOG_LIMIT + 100))
        state = self.server.job.snapshot()
        self.assertEqual(len(state['output']), web.LOG_LIMIT)
        self.assertTrue(state['trimmed'])
        _, script = self.request('/app.js')
        self.assertNotIn('innerHTML', script)
        self.assertIn('textContent', script)
