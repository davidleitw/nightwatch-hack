import {apiPath, readJSON, setMockMode, validateState, validateGraph} from './data.js';
import {validateInvestigationState, validateObservation} from './investigation-data.js';

const $ = id => document.getElementById(id);
const shellQuote = value => "'" + value.replaceAll("'", "'\\''") + "'";

function startupCommand() {
  try {
    const input = $('control-url').value.trim();
    const url = new URL(input);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || /\s/.test(input)) {
      throw new Error('請使用 http:// 或 https://，網址不含帳密、查詢參數或片段。');
    }
    $('connect-command').textContent = `python3 console/build.py\npython3 console/serve.py --port 4174 --control-url ${shellQuote(url.href.replace(/\/$/, ''))}`;
    $('command-note').textContent = '在專案根目錄執行；先停止佔用 4174 的測試服務。此欄位只產生指令，不會立即更改連線。';
  } catch (error) {
    $('connect-command').textContent = '';
    $('command-note').textContent = error.message;
  }
}

function checkStream(state, mock) {
  return new Promise((resolve, reject) => {
    const events = new EventSource(mock ? apiPath('/events') : '/api/investigations/stream');
    let receivedState = false;
    const leave = () => finish(new Error('頁面已離開。'));
    const finish = error => { clearTimeout(timer); events.close(); window.removeEventListener('pagehide', leave); error ? reject(error) : resolve(); };
    const timer = setTimeout(() => finish(new Error('7 秒內未收到有效的 state 與 ping／graph。')), 7000);
    events.addEventListener('state', event => {
      try { state = (mock ? validateState : validateInvestigationState)(JSON.parse(event.data)); receivedState = true; }
      catch (error) { finish(error); }
    });
    for (const kind of ['ping', 'graph']) events.addEventListener(kind, event => {
      try {
        const data = JSON.parse(event.data);
        if (kind === 'graph') { if (mock) validateGraph(data, state.capabilities); else validateObservation(data); }
        else if (!Number.isFinite(Date.parse(data.server_now))) throw new Error('ping 缺少合法 server_now。');
        if (receivedState) finish();
      } catch (error) { finish(error); }
    });
    events.onerror = () => finish(new Error('即時推播無法連線或已中斷。'));
    window.addEventListener('pagehide', leave, {once: true});
  });
}

export async function setupConnection(source) {
  let config;
  try {
    config = await readJSON('/__console/config');
    if (!['mock', 'live'].includes(config.mode)) throw new Error('接線資訊缺少 mode。');
    $('connection-target').textContent = config.mode === 'mock' ? '本機模擬伺服器（無真實後端）' : config.upstream;
  } catch (error) {
    $('connection-target').textContent = `使用同源 /api 與 /events；無法取得伺服器接線資訊：${error.message}`;
  }
  const mock = config?.mode === 'mock';
  setMockMode(mock);
  if (mock) {
    $('source').options[0].textContent = '本機模擬 API';
    $('mock-banner').hidden = source === 'recording';
    $('mock-scenario').value = new URLSearchParams(location.search).get('scenario') || 'cycle';
    $('mock-scenario').onchange = event => {
      const url = new URL(location.href);
      url.searchParams.set('source', 'live');
      url.searchParams.set('scenario', event.target.value);
      location.href = url.href;
    };
  }
  $('control-url').value = config?.upstream || 'http://127.0.0.1:9999';
  $('control-url').addEventListener('input', startupCommand);
  startupCommand();
  $('check-connection').onclick = async () => {
    $('check-connection').disabled = true;
    const label = mock ? '本機模擬' : '目前伺服器';
    const path = mock ? '/api/state' : '/api/investigations/state';
    $('connection-check').textContent = `${label}：正在讀取 ${path}…`;
    try {
      const state = (mock ? validateState : validateInvestigationState)(await readJSON(path));
      const nodes = mock ? state.graph_now.nodes.length : state.graph?.nodes.length;
      $('connection-check').textContent = `${label}：${nodes ?? '尚無'} 個節點。正在檢查推播…`;
      await checkStream(state, mock);
      $('connection-check').textContent = `${label}：狀態與調查 SSE 接收成功。${mock ? '這不代表真實 shop 串接通過。' : state.graph_error ? `graph_error：${state.graph_error}` : '尚未驗證真實模型、graph 新量測或 Monitor log。'}`;
    } catch (error) {
      $('connection-check').textContent = `${label}檢查失敗：${error.message}`;
    } finally { $('check-connection').disabled = false; }
  };
  $('check-connection').disabled = false;
  return mock;
}
