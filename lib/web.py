"""Loopback-only, single-job portal for the existing trusted CLI (Python 3.8+)."""
import argparse
import codecs
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
LOG_LIMIT = 512 * 1024
LEASE_SECONDS = 60


class Job:
    def __init__(self, media, apply, config, env=None):
        self.id = secrets.token_hex(16)
        self.lock = threading.RLock()
        self.output = ''
        self.trimmed = False
        self.prompt = None
        self.sequence = 0
        self.last_seen = time.monotonic()
        self.cancelled = False
        self.code = None
        parent, child = socket.socketpair()
        self.channel = parent
        environment = dict(os.environ if env is None else env)
        environment.update(MEDIA_SORTER_CONFIG=str(config), MEDIA_SORTER_PROMPT_FD=str(child.fileno()),
                           PYTHONUNBUFFERED='1', LC_ALL='C', LANG='C')
        try:
            self.proc = subprocess.Popen(['/bin/bash', str(ROOT / 'bin/media-sorter'), media] +
                                         (['--apply'] if apply else []), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         env=environment, pass_fds=(child.fileno(),), start_new_session=True)
        except BaseException:
            parent.close()
            raise
        finally:
            child.close()
        threading.Thread(target=self.read_output, daemon=True).start()
        threading.Thread(target=self.read_prompts, daemon=True).start()

    def append(self, text):
        with self.lock:
            self.output += text
            if len(self.output) > LOG_LIMIT:
                self.trimmed = True
                self.output = self.output[-LOG_LIMIT:]

    def read_output(self):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            while True:
                chunk = os.read(self.proc.stdout.fileno(), 16384)
                if not chunk:
                    break
                self.append(decoder.decode(chunk))
            self.append(decoder.decode(b'', final=True))
        finally:
            self.proc.stdout.close()
            code = self.proc.wait()
            with self.lock:
                self.code = code
                self.prompt = None
            self.channel.close()

    def read_prompts(self):
        try:
            with self.channel.makefile('rb') as stream:
                while True:
                    line = stream.readline(16385)
                    if not line:
                        break
                    if len(line) > 16384:
                        raise ValueError('Prompt too long')
                    prompt = json.loads(line)['prompt']
                    with self.lock:
                        self.sequence += 1
                        self.prompt = {'id': self.sequence, 'text': prompt}
        except (OSError, ValueError, KeyError):
            self.cancel()

    def answer(self, prompt_id, answer):
        if answer is not None and (not isinstance(answer, str) or len(answer.encode()) > 1024 or
                                   any(ord(c) < 32 or ord(c) == 127 for c in answer)):
            raise ValueError('Answer must be one line, at most 1024 bytes.')
        with self.lock:
            if self.cancelled or self.code is not None or not self.prompt or prompt_id != self.prompt['id']:
                raise ValueError('This prompt is no longer waiting. Refresh before replying.')
            self.channel.sendall((json.dumps(answer) + '\n').encode())
            self.prompt = None

    def snapshot(self):
        with self.lock:
            self.last_seen = time.monotonic()
            return dict(id=self.id, output=self.output, trimmed=self.trimmed, prompt=self.prompt,
                        code=self.code, cancelled=self.cancelled)

    def cancel(self):
        with self.lock:
            if self.code is not None or self.cancelled:
                return
            self.cancelled = True
            self.prompt = None
            # Signal the CLI and its rsync/SSH children, allowing CLI cleanup.
            try:
                os.killpg(self.proc.pid, signal.SIGINT)
            except ProcessLookupError:
                return
        threading.Thread(target=self.reap, daemon=True).start()

    def reap(self):
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class Portal(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, config, env=None):
        self.config = config
        self.env = env
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.job_lock = threading.Lock()
        self.stopping = threading.Event()
        self.connections = threading.BoundedSemaphore(12)
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)
        threading.Thread(target=self.watch, daemon=True).start()

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def watch(self):
        while not self.stopping.wait(1):
            with self.job_lock:
                if self.job and time.monotonic() - self.job.last_seen > LEASE_SECONDS:
                    self.job.cancel()

    def server_close(self):
        self.stopping.set()
        if self.job:
            self.job.cancel()
            try:
                self.job.proc.wait(timeout=17)
            except subprocess.TimeoutExpired:
                pass
        super().server_close()

    def launch(self, data):
        if set(data) != {'media', 'apply', 'confirm'} or data['media'] not in ('movies', 'tv') or type(data['apply']) is not bool:
            raise ValueError('Choose Movies or TV and preview or apply.')
        if data['apply'] and data['confirm'] is not True:
            raise ValueError('Explicit apply confirmation is required.')
        # Configuration is managed only by the existing local CLI. No paths,
        # command names, environment variables or arguments come from the web.
        config = Path(str(self.config) + '.' + data['media'])
        if data['media'] == 'movies' and not config.exists():
            config = self.config
        if not config.is_file():
            raise ValueError('Profile missing. Run media-sorter configure ' + data['media'] + ' in Terminal first.')
        with self.job_lock:
            if self.job and self.job.code is None:
                raise ValueError('A job is already running. Finish or cancel it first.')
            self.job = Job(data['media'], data['apply'], self.config, self.env)
            return self.job.snapshot()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # Do not log the session token or media names.

    def reply(self, status, value, kind='application/json'):
        body = json.dumps(value).encode() if kind == 'application/json' else value.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def allowed(self, api=False):
        if self.headers.get_all('Host') != [self.server.origin[7:]]:
            self.reply(403, {'error': 'Invalid Host'})
            return False
        origin = self.headers.get('Origin')
        if (origin and origin != self.server.origin) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.reply(403, {'error': 'Invalid Origin'})
            return False
        if api and not hmac.compare_digest(self.headers.get('X-Portal-Token', '').encode(), self.server.token.encode()):
            self.reply(403, {'error': 'Session token required'})
            return False
        return True

    def do_GET(self):
        if not self.allowed(self.path == '/api/state'):
            return
        if self.path == '/api/state':
            self.reply(200, self.server.job.snapshot() if self.server.job else None)
        elif self.path in ('/', '/app.js', '/style.css'):
            filename, kind = {'/': ('web.html', 'text/html'), '/app.js': ('web.js', 'text/javascript'),
                              '/style.css': ('web.css', 'text/css')}[self.path]
            self.reply(200, (ROOT / 'lib' / filename).read_text(), kind)
        else:
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.allowed(api=True):
            return
        if self.headers.get('Origin') != self.server.origin:
            self.reply(403, {'error': 'Same-origin request required'})
            return
        try:
            if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
                raise ValueError('JSON with Content-Length required')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 8192:
                raise ValueError('Request too large or invalid length')
            data = json.loads(self.rfile.read(int(lengths[0])))
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            if self.path == '/api/start':
                result = self.server.launch(data)
            else:
                job = self.server.job
                if not job or data.get('job') != job.id:
                    raise ValueError('Job no longer exists')
                if self.path == '/api/answer' and set(data) == {'job', 'prompt', 'answer'}:
                    job.answer(data['prompt'], data['answer'])
                elif self.path == '/api/cancel' and set(data) == {'job'}:
                    job.cancel()
                else:
                    raise ValueError('Unknown action')
                result = job.snapshot()
            self.reply(200, result)
        except (ValueError, OSError) as error:
            self.reply(400, {'error': str(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('MEDIA_SORTER_CONFIG', '~/.config/media-sorter/config')).expanduser())
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be 0–65535')
    server = Portal(args.port, args.config.expanduser().absolute())
    print('Open locally: ' + server.origin + '/#' + server.token, flush=True)
    print('Press Ctrl-C to stop. Closing the page cancels an active job after 60 seconds.', flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
