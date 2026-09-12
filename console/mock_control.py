"""Explicit local HTTP/SSE simulation; never probes or changes a real shop."""
import copy
import json
import time
from datetime import datetime, timezone


SCENARIOS = ('cycle', 'ok', 'warning', 'failing', 'unknown', 'unavailable')


def timestamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class MockControl:
    def __init__(self, recording):
        self.template = json.loads(recording.read_text())
        self.started = timestamp()
        self.clock = time.monotonic()

    def state(self, scenario):
        elapsed = int(time.monotonic() - self.clock)
        status = ('ok', 'warning', 'failing', 'unknown')[(elapsed // 10) % 4] if scenario == 'cycle' else scenario
        state = copy.deepcopy(self.template)
        now = timestamp()
        state['server_now'] = now
        state['run'].update(id=f'local-mock-{self.started}-{scenario}', started_at=self.started)
        state['incident'] = None
        state['faults'] = {'instances': [], 'generation': 0}
        state['model'] = {'available': False, 'model': '', 'effort': ''}
        state['next_step_zh'] = '本機模擬：只檢查健康狀態顯示，未連接真實 shop 或 Agent。'
        state['readiness'] = {'ready': False, 'checks': [], 'next_step_zh': state['next_step_zh']}
        state['capabilities']['links'] = {}
        graph = state['graph_now']
        graph.update(seq=elapsed // 5 + 1, at=now, gap_before=None)
        graph.pop('t', None)
        for node in graph['nodes']:
            node.update(traffic=20.0, errors=0.0, p95_ms=80.0, saturation=0.2,
                        alive=True, status='ok', assessment='unassessed', primary_axis='traffic',
                        extras={}, logs_indexed=False, revision='v1')
            node['trend'] = {axis: 'flat' for axis in ('traffic', 'errors', 'latency', 'saturation')}
            if node['id'] == 'catalog':
                node['status'] = status
                if status == 'warning':
                    node.update(p95_ms=240.0, primary_axis='latency')
                    node['trend']['latency'] = 'rising'
                elif status == 'failing':
                    node.update(errors=0.3, p95_ms=900.0, saturation=0.96, primary_axis='errors')
                    node['trend'].update(errors='rising', latency='rising', saturation='rising')
                elif status == 'unknown':
                    node.update(traffic=None, errors=None, p95_ms=None, saturation=None, primary_axis=None)
                    node['trend'] = {axis: 'na' for axis in node['trend']}
        for edge in graph['edges']:
            missing = status == 'unknown' and 'catalog' in (edge['from'], edge['to'])
            edge.update(rps=None if missing else 20.0, errors=None if missing else 0.0,
                        p95_ms=None if missing else 80.0, observed=not missing)
        graph['sources'] = {name: {'ok': True, 'age_secs': 0} for name in ('prometheus', 'jaeger', 'logstore')}
        return state

    def handle(self, handler, path, scenario):
        if scenario not in SCENARIOS:
            return handler.send_json(400, {'error': {'code': 'bad_request', 'message_zh': '未知的本機模擬情境', 'details': {}}})
        if scenario == 'unavailable':
            return handler.send_json(503, {'error': {'code': 'internal', 'message_zh': '本機模擬：後端暫時無法使用（HTTP 503）', 'details': {}}})
        if path == '/api/state':
            return handler.send_json(200, self.state(scenario))
        if path == '/api/incidents':
            return handler.send_json(200, [])
        if path == '/api/graph':
            return handler.send_json(200, self.state(scenario)['graph_now'])
        if path != '/events':
            return handler.send_json(404, {'error': {'code': 'not_found', 'message_zh': '本機模擬未提供此端點', 'details': {}}})
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Accel-Buffering', 'no')
        handler.end_headers()

        def emit(event, data):
            handler.wfile.write(f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode())
            handler.wfile.flush()

        try:
            state = self.state(scenario)
            emit('state', state)
            last_seq = state['graph_now']['seq']
            last_ping = time.monotonic()
            while True:
                time.sleep(0.5)
                state = self.state(scenario)
                if state['graph_now']['seq'] != last_seq:
                    emit('graph', state['graph_now'])
                    last_seq = state['graph_now']['seq']
                if time.monotonic() - last_ping >= 2:
                    emit('ping', {'server_now': timestamp()})
                    last_ping = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            handler.log_message('local mock SSE client disconnected')
        finally:
            handler.close_connection = True
