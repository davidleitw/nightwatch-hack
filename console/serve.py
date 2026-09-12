"""Serve the static console and proxy read-only control APIs on loopback."""
import argparse
import http.client
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--control-port', type=int, default=3300)
    args = parser.parse_args()
    directory = str(Path(__file__).resolve().parent / 'dist')

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, directory=directory, **kwargs)

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if not (path.startswith('/api/') or path == '/events'):
                return super().do_GET()
            if self.path.startswith('//') or not self.path.startswith('/'):
                return self.send_error(400)
            connection = http.client.HTTPConnection('127.0.0.1', args.control_port, timeout=20)
            sent = False
            try:
                connection.request('GET', self.path, headers={'Accept': self.headers.get('Accept', 'application/json')})
                response = connection.getresponse()
                self.send_response(response.status)
                self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                sent = True
                while block := response.read1(65536):
                    self.wfile.write(block)
                    self.wfile.flush()
            except (ConnectionError, TimeoutError, OSError, http.client.HTTPException) as exc:
                if not sent:
                    body = json.dumps({'error': {'code': 'internal', 'message_zh': f'無法連線至本機 control :{args.control_port}（{type(exc).__name__}）', 'details': {}}}, ensure_ascii=False).encode()
                    self.send_response(502)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                self.log_error('control proxy: %s', exc)
            finally:
                connection.close()
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'NightWatch: http://127.0.0.1:{args.port}/?source=recording', flush=True)
    print(f'Live control: 127.0.0.1:{args.control_port}; Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nNightWatch server stopped.', flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
