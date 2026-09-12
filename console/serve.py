"""Serve the console, stream control GETs, and forward investigation creation."""
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
    parser.add_argument('--control-port', type=int, default=8001)
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
            return self.proxy_request('GET')

        def do_POST(self):
            if urlsplit(self.path).path != '/api/investigations':
                return self.send_json(405, {'error': {'code': 'method_not_allowed', 'message_zh': '此代理只允許建立調查。', 'details': {}}})
            if mock:
                return self.send_json(503, {'error': {'code': 'unavailable', 'message_zh': '本機模擬不支援真實調查。', 'details': {}}})
            origin = self.headers.get('Origin')
            if origin and origin != f'http://{self.headers.get("Host")}':
                return self.send_json(403, {'error': {'code': 'forbidden', 'message_zh': '調查必須由同源前端送出。', 'details': {}}})
            if self.headers.get('Transfer-Encoding'):
                return self.send_json(400, {'error': {'code': 'invalid_request', 'message_zh': '不支援分塊的建立請求。', 'details': {}}})
            try:
                length = int(self.headers.get('Content-Length', ''))
                if not 0 < length <= 65536:
                    raise ValueError()
            except ValueError:
                return self.send_json(400, {'error': {'code': 'invalid_request', 'message_zh': '建立請求需要合法 Content-Length（1–65536）。', 'details': {}}})
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self.send_json(415, {'error': {'code': 'invalid_request', 'message_zh': '建立請求必須使用 application/json。', 'details': {}}})
            self.connection.settimeout(10)
            try:
                body = self.rfile.read(length)
            except TimeoutError:
                return self.send_json(408, {'error': {'code': 'invalid_request', 'message_zh': '讀取建立請求逾時。', 'details': {}}})
            if len(body) != length:
                return self.send_json(400, {'error': {'code': 'invalid_request', 'message_zh': '建立請求不完整。', 'details': {}}})
            return self.proxy_request('POST', body)

        def proxy_request(self, method, body=None):
            transport = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
            connection = transport(target.hostname, target.port, timeout=20)
            sent = False
            try:
                headers = {'Accept': self.headers.get('Accept', 'application/json')}
                if body is not None:
                    headers['Content-Type'] = 'application/json'
                if self.headers.get('Last-Event-ID') is not None:
                    headers['Last-Event-ID'] = self.headers['Last-Event-ID']
                connection.request(method, target.path.rstrip('/') + self.path, body=body, headers=headers)
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
