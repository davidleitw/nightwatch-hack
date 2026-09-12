"""Serve the static console and proxy read-only control APIs on loopback."""
import argparse
import http.client
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mock_control import MockControl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--control-port', type=int, default=3300)
    parser.add_argument('--control-url', default=os.environ.get('NIGHTWATCH_CONTROL_URL'),
                        help='HTTP(S) control base URL; defaults to the local --control-port')
    parser.add_argument('--mock', action='store_true', help='Explicit local simulation; no upstream requests')
    args = parser.parse_args()
    if args.mock and args.control_url:
        parser.error('--mock cannot be combined with --control-url or NIGHTWATCH_CONTROL_URL')
    upstream = args.control_url or f'http://127.0.0.1:{args.control_port}'
    try:
        target = urlsplit(upstream)
        if (target.scheme not in ('http', 'https') or not target.hostname or target.username is not None
                or target.password is not None or target.query or target.fragment
                or any(char.isspace() or ord(char) < 32 for char in upstream)):
            raise ValueError('use an HTTP(S) URL without credentials, query or fragment')
        target.port
    except ValueError as exc:
        parser.error(f'invalid control URL: {exc}')
    directory = str(Path(__file__).resolve().parent / 'dist')
    if not (Path(directory) / 'index.html').is_file():
        parser.error('Build first: python3 console/build.py')
    mock = MockControl(Path(directory) / 'recordings/state.initial.json') if args.mock else None

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, directory=directory, **kwargs)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/__console/config':
                return self.send_json(200, {'mode': 'mock' if mock else 'live', 'upstream': None if mock else upstream})
            if not (path.startswith('/api/') or path == '/events'):
                return super().do_GET()
            if self.path.startswith('//') or not self.path.startswith('/'):
                return self.send_error(400)
            if mock:
                scenario = parse_qs(urlsplit(self.path).query).get('scenario', ['cycle'])[0]
                return mock.handle(self, path, scenario)
            transport = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
            connection = transport(target.hostname, target.port, timeout=20)
            sent = False
            try:
                connection.request('GET', target.path.rstrip('/') + self.path,
                                   headers={'Accept': self.headers.get('Accept', 'application/json')})
                response = connection.getresponse()
                self.send_response(response.status)
                self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Accel-Buffering', 'no')
                self.send_header('Connection', 'close')
                self.end_headers()
                sent = True
                while block := response.read1(65536):
                    self.wfile.write(block)
                    self.wfile.flush()
            except (ConnectionError, TimeoutError, OSError, http.client.HTTPException) as exc:
                if not sent:
                    body = json.dumps({'error': {'code': 'internal', 'message_zh': f'無法連線至 control {upstream}（{type(exc).__name__}）', 'details': {}}}, ensure_ascii=False).encode()
                    self.send_response(502)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                self.log_error('control proxy: %s', exc)
            finally:
                connection.close()
                self.close_connection = True

        def send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'NightWatch: http://127.0.0.1:{args.port}/?source=live', flush=True)
    print('LOCAL MOCK — no real shop or Agent connected.' if mock else f'Live control: {upstream}', flush=True)
    print('Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nNightWatch server stopped.', flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
