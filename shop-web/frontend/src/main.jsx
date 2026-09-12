import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const money = value => `NT$ ${value.toLocaleString('zh-TW')}`;

const FAULT_CARDS = [
  {
    id: 'checkout_exception',
    title: '結帳交易例外',
    eyebrow: 'TRANSACTION ROLLBACK',
    impact: 'POST /api/orders 與購物車 checkout',
    description: '在交易完成前發生例外，回傳 500；訂單與購物車異動會一起 rollback。',
  },
  {
    id: 'database_write_lock',
    title: 'SQLite 寫入鎖',
    eyebrow: 'DATABASE CONTENTION',
    impact: '所有需要寫入 SQLite 的商品、購物車與訂單 API',
    description: '以獨立連線持有 BEGIN IMMEDIATE，其他寫入最多等待 10 秒後失敗 500。',
  },
  {
    id: 'checkout_delay',
    title: '結帳延遲',
    eyebrow: 'CHECKOUT LATENCY',
    impact: 'POST /api/orders 與購物車 checkout',
    description: '結帳請求由 server 延遲 10 秒；頁面不會自動建立訂單。',
  },
];

const FAULT_IDS = new Set(FAULT_CARDS.map(card => card.id));

function readCart() {
  try {
    const value = JSON.parse(localStorage.getItem('daily-cart') || '{}');
    return Object.fromEntries(Object.entries(value).filter(([id, qty]) => /^\d+$/.test(id) && Number.isInteger(qty) && qty > 0 && qty <= 99));
  } catch { return {}; }
}

function responseError(payload, fallback) {
  if (payload && typeof payload === 'object') {
    if (typeof payload.detail === 'string') return payload.detail;
    if (typeof payload.message === 'string') return payload.message;
    if (payload.error && typeof payload.error.message === 'string') return payload.error.message;
  }
  return fallback;
}

async function requestFaults(method, body, signal) {
  const response = await fetch('/api/demo-faults', {
    method,
    signal,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  let payload = null;
  try { payload = await response.json(); } catch { /* The status below still explains the failure. */ }
  if (!response.ok) throw new Error(responseError(payload, `故障控制 API 回傳 HTTP ${response.status}。`));
  return payload;
}

function normalizeFaultState(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('故障控制 API 回傳了無法辨識的狀態。');
  }

  const nested = payload.active_fault && typeof payload.active_fault === 'object'
    ? payload.active_fault
    : payload.active && typeof payload.active === 'object' ? payload.active : {};
  const faultId = payload.active_fault_id ?? payload.fault_id ?? nested.fault_id ?? nested.id ?? null;
  const status = String(payload.status ?? nested.status ?? (faultId ? 'active' : 'idle')).toLowerCase();
  const remaining = Number(
    payload.remaining_seconds
      ?? payload.ttl_remaining_seconds
      ?? nested.remaining_seconds
      ?? nested.ttl_remaining_seconds
      ?? 0,
  );

  if (faultId !== null && (typeof faultId !== 'string' || !FAULT_IDS.has(faultId))) {
    throw new Error('故障控制 API 回傳了未知的 fault_id。');
  }
  if (!Number.isFinite(remaining) || remaining < 0) {
    throw new Error('故障控制 API 回傳了無效的 server 剩餘秒數。');
  }
  if (['active', 'armed', 'applying'].includes(status) && !faultId) {
    throw new Error('故障控制 API 回傳 active 狀態但沒有 fault_id。');
  }

  const serverCards = Array.isArray(payload.cards) ? payload.cards : [];
  const cards = FAULT_CARDS.map(card => {
    const serverCard = serverCards.find(item => item && item.fault_id === card.id);
    return {
      ...card,
      ...(serverCard && typeof serverCard.title === 'string' ? { title: serverCard.title } : {}),
      ...(serverCard && typeof serverCard.description === 'string' ? { description: serverCard.description } : {}),
    };
  });

  return {
    cards,
    activeFaultId: faultId,
    status: faultId ? status : 'idle',
    remainingSeconds: Math.floor(remaining),
  };
}

function DemoFaultPage() {
  const [faultState, setFaultState] = useState({
    loading: true,
    error: '',
    cards: FAULT_CARDS,
    activeFaultId: null,
    status: 'idle',
    remainingSeconds: 0,
  });
  const [pending, setPending] = useState('');
  const mounted = useRef(false);
  const requestSerial = useRef(0);
  const requestInFlight = useRef(false);
  const requestController = useRef(null);

  const refresh = useCallback(async (force = false) => {
    if (!mounted.current || (requestInFlight.current && !force)) return;
    if (force) requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const serial = ++requestSerial.current;
    requestInFlight.current = true;
    try {
      const next = normalizeFaultState(await requestFaults('GET', null, controller.signal));
      if (mounted.current && serial === requestSerial.current) {
        setFaultState({ loading: false, error: '', ...next });
      }
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (mounted.current && serial === requestSerial.current) {
        setFaultState(current => ({ ...current, loading: false, error: error.message }));
      }
    } finally {
      if (serial === requestSerial.current) {
        requestInFlight.current = false;
        requestController.current = null;
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh(true);
    const timer = window.setInterval(() => refresh(), 2000);
    return () => {
      mounted.current = false;
      requestSerial.current += 1;
      requestController.current?.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);

  async function activate(faultId) {
    if (pending || faultState.loading || faultState.error || faultState.activeFaultId || transitioning) return;
    setPending(faultId);
    setFaultState(current => ({ ...current, error: '' }));
    try {
      await requestFaults('POST', { fault_id: faultId });
      await refresh(true);
    } catch (error) {
      if (mounted.current) setFaultState(current => ({ ...current, error: error.message }));
    } finally {
      if (mounted.current) setPending('');
    }
  }

  async function clearFault() {
    if (pending || faultState.loading || transitioning || !faultState.activeFaultId) return;
    setPending('clear');
    setFaultState(current => ({ ...current, error: '' }));
    try {
      await requestFaults('DELETE');
      await refresh(true);
    } catch (error) {
      if (mounted.current) setFaultState(current => ({ ...current, error: error.message }));
    } finally {
      if (mounted.current) setPending('');
    }
  }

  const activeCard = faultState.cards.find(card => card.id === faultState.activeFaultId);
  const transitioning = ['activating', 'restoring'].includes(faultState.status);
  const stateLabel = faultState.loading ? '讀取中' : faultState.error ? '無法同步' : faultState.status === 'activating' ? '啟用中' : faultState.status === 'restoring' ? '解除中' : activeCard ? '作用中' : '待命';

  return <>
    <div className="announcement">故障演練模式 · 先觀察，再讓服務自己說明問題</div>
    <header className="header wrap">
      <a href="/#" className="brand"><span className="brand-icon">日</span><span>日日選物<small>DAILY FINDS</small></span></a>
      <a className="nav-link event-nav" href="/#">返回商店 ↗</a>
    </header>
    <main className="wrap event-page">
      <section className="event-hero">
        <div>
          <span className="eyebrow">OBSERVE THE SYSTEM</span>
          <h1>故障演練</h1>
          <p>選一張卡讓服務真的出現問題，再回到購物頁正常操作。這裡不會自動建立訂單。</p>
        </div>
        <div className={`event-state ${activeCard ? 'is-active' : ''}`} aria-live="polite">
          <span className="event-state-label">SERVER STATUS</span>
          <strong>{stateLabel}</strong>
          {activeCard && <p>{activeCard.title}</p>}
          <div className="event-remaining">伺服器剩餘 <b>{faultState.remainingSeconds}</b> 秒</div>
          {activeCard && <button className="secondary" disabled={Boolean(pending) || faultState.loading || transitioning} onClick={clearFault}>{pending === 'clear' || faultState.status === 'restoring' ? '解除中…' : '解除目前故障'}</button>}
        </div>
      </section>

      <section className="faults-section">
        <div className="section-top">
          <div><span className="eyebrow">THREE FAILURE MODES</span><h2>選擇一張故障卡</h2></div>
          <span className="event-poll-note">每 2 秒與 server 同步</span>
        </div>
        {faultState.error && <div className="api-error" role="alert"><span>{faultState.error}</span><button className="secondary" disabled={pending !== ''} onClick={() => refresh(true)}>重新讀取</button></div>}
        <div className="fault-grid">
          {faultState.cards.map(card => {
            const isActive = faultState.activeFaultId === card.id;
            const blockedByOther = Boolean(faultState.activeFaultId && !isActive);
            const disabled = Boolean(pending) || faultState.loading || Boolean(faultState.error) || blockedByOther || transitioning || isActive;
            return <article className={`fault-card ${isActive ? 'is-active' : ''}`} key={card.id}>
              <div className="fault-card-top"><span className="fault-number">0{FAULT_CARDS.findIndex(baseCard => baseCard.id === card.id) + 1}</span><span className={`fault-status ${isActive ? 'active' : ''}`}>{isActive ? (faultState.status === 'activating' ? '啟用中' : faultState.status === 'restoring' ? '解除中' : '作用中') : blockedByOther ? '另一張卡作用中' : '待命'}</span></div>
              <span className="eyebrow">{card.eyebrow}</span>
              <h3>{card.title}</h3>
              <p className="fault-impact"><b>影響範圍</b>{card.impact}</p>
              <p className="fault-description">{card.description}</p>
              <button className={isActive ? 'secondary fault-action' : 'primary fault-action'} disabled={disabled} onClick={() => activate(card.id)}>{pending === card.id ? '啟用中…' : isActive ? '目前作用中' : '啟用故障'}</button>
            </article>;
          })}
        </div>
      </section>

      <section className="event-notes">
        <span>✳</span>
        <div><h2>操作提示</h2><p>啟用後請回到購物頁手動操作，觀察 API 回應與 log；本頁只控制故障，不會替你送出訂單。故障會在 60 秒後由 server 自動解除，也可以在本頁手動解除。</p></div>
      </section>
    </main>
    <footer className="wrap"><span>日日選物 <small>DAILY FINDS</small></span><p>故障演練不會實際扣款或出貨</p></footer>
  </>;
}

function App() {
  const [hash, setHash] = useState(() => window.location.hash);
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [category, setCategory] = useState('全部商品');
  const [search, setSearch] = useState('');
  const [cart, setCart] = useState(readCart);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [order, setOrder] = useState(null);
  const [notice, setNotice] = useState('');
  const dialog = useRef(null);
  const submitting = useRef(false);
  const isEvents = hash === '#/events';

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  async function loadProducts() {
    setLoading(true); setLoadError('');
    try {
      const response = await fetch('/api/products');
      if (!response.ok) throw new Error();
      const data = await response.json();
      setProducts(data);
      setCart(current => Object.fromEntries(Object.entries(current).filter(([id]) => data.some(p => p.id === Number(id)))));
    } catch { setLoadError('暫時無法取得商品，請稍後再試。'); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (!isEvents) loadProducts(); }, [isEvents]);
  useEffect(() => { try { localStorage.setItem('daily-cart', JSON.stringify(cart)); } catch { /* Cart remains usable without browser storage. */ } }, [cart]);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open, isEvents]);
  useEffect(() => { if (isEvents) { setOpen(false); setOrder(null); setError(''); } }, [isEvents]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 2500); return () => clearTimeout(timer); }, [notice]);

  const lines = products.filter(p => cart[p.id]).map(p => ({ ...p, quantity: cart[p.id] }));
  const count = lines.reduce((n, p) => n + p.quantity, 0);
  const total = lines.reduce((n, p) => n + p.price * p.quantity, 0);
  const visible = products.filter(p => (category === '全部商品' || p.category === category) && p.name.includes(search.trim()));

  function change(id, delta) {
    if (submitting.current) return;
    setOrder(null); setError('');
    setCart(current => {
      const next = { ...current, [id]: Math.min(99, (current[id] || 0) + delta) };
      if (next[id] <= 0) delete next[id];
      return next;
    });
  }

  async function checkout(event) {
    event.preventDefault();
    if (submitting.current || !lines.length) return;
    submitting.current = true; setBusy(true); setError('');
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const response = await fetch('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: data.get('name'), address: data.get('address'), items: lines.map(p => ({ product_id: p.id, quantity: p.quantity })) }) });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '請確認收件資料與商品數量。');
      setOrder(result); setCart({}); form.reset();
    } catch (err) { setError(err.message === 'Failed to fetch' ? '連線中斷，訂單狀態未確認，請勿連續重送。' : err.message); }
    finally { submitting.current = false; setBusy(false); }
  }

  if (isEvents) return <DemoFaultPage />;

  return <>
    <div className="announcement">慢慢挑選，好好生活 · 每件選物都是日常的小幸福</div>
    <header className="header wrap"><a href="/#" className="brand"><span className="brand-icon">日</span><span>日日選物<small>DAILY FINDS</small></span></a><a href="#products" className="nav-link">探索選物 ↗</a><a href="/#/events" className="event-link">故障演練 ↗</a><button className="cart-button" onClick={() => setOpen(true)}>購物袋 <span>{count}</span></button></header>
    <main className="wrap">
      <section className="hero"><div className="hero-copy"><span className="eyebrow">LESS, BUT BETTER.</span><h1>把喜歡，<br/>帶進日常生活。</h1><p>從一杯咖啡到一段旅程，<br/>精選實用而美好的物件，陪你度過每一天。</p><a className="primary" href="#products">發現你的日常好物 <span>↗</span></a><div className="hero-note">用心選品 <i/> 簡單生活 <i/> 長久陪伴</div></div><div className="hero-art" aria-hidden="true"><div className="circle"/><span className="art-leaf">🌿</span><span className="art-cup">☕</span><span className="art-book">📓</span><div className="art-caption">A little joy,<br/><em>every day.</em></div><span className="art-tag">THE EVERYDAY COLLECTION — 01</span></div></section>
      <section id="products" className="catalog"><div className="section-top"><div><span className="eyebrow">CURATED FOR YOU</span><h2>為日常，挑一點喜歡</h2></div><label className="search"><span aria-hidden="true">⌕</span><input aria-label="搜尋商品" placeholder="搜尋你的生活好物" value={search} onChange={e => setSearch(e.target.value)}/></label></div><div className="filters">{['全部商品', '居家生活', '隨身好物', '文具選物'].map(c => <button key={c} className={category === c ? 'active' : ''} aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>)}<span>{visible.length} 件選物</span></div>
      {loading ? <p className="empty" role="status">正在準備美好的選物…</p> : loadError ? <div className="empty" role="alert">{loadError}<button onClick={loadProducts}>重新載入</button></div> : !visible.length ? <p className="empty">沒有符合的商品，試試其他關鍵字吧。</p> : <div className="grid">{visible.map((p, index) => <article className="product" key={p.id}><div className="product-art" style={{ background: p.color }}><span className="product-number">0{index + 1} / DAILY FINDS</span><span className="product-emoji" role="img" aria-label={p.name}>{p.icon}</span><span className="product-label">日常精選</span></div><div className="product-info"><small>{p.category}</small><h3>{p.name}</h3><p>{p.description}</p><div className="product-bottom"><strong>{money(p.price)}</strong><button disabled={cart[p.id] >= 99} aria-label={`將${p.name}加入購物袋`} onClick={() => { change(p.id, 1); setNotice(`已將${p.name}加入購物袋`); }}>＋ <span>加入購物袋</span></button></div></div></article>)}</div>}</section>
      <section className="closing"><span>✳</span><div><h2>少一點將就，多一點喜歡。</h2><p>讓每一件留下的物品，都成為生活的好夥伴。</p></div></section>
    </main><footer className="wrap"><span>日日選物 <small>DAILY FINDS</small></span><p>簡易購物示範網站 · 結帳不會實際扣款或出貨</p></footer>
    <div className="toast" role="status">{notice}</div>
    <dialog ref={dialog} onCancel={event => { if (busy) event.preventDefault(); else setOpen(false); }} onClose={() => setOpen(false)}><div className="dialog-head"><h2>你的購物袋 <small>({count})</small></h2><button disabled={busy} aria-label="關閉購物袋" onClick={() => setOpen(false)}>✕</button></div>{order ? <div className="success" role="status"><span>✓</span><h3>謝謝你，訂單已成立！</h3><p>訂單編號：{order.id}</p><strong>{money(order.total)}</strong><p>這是模擬訂單，不會實際扣款或出貨。</p><button className="primary" onClick={() => { setOpen(false); setOrder(null); }}>繼續探索</button></div> : !lines.length ? <div className="empty"><p>購物袋還空著，挑一件喜歡的吧。</p><button className="primary" onClick={() => setOpen(false)}>去逛逛</button></div> : <><div className="cart-lines">{lines.map(p => <div className="cart-line" key={p.id}><span className="cart-icon" style={{ background: p.color }}>{p.icon}</span><div><h3>{p.name}</h3><small>{money(p.price)}</small><div className="quantity"><button disabled={busy} aria-label={`減少${p.name}數量`} onClick={() => change(p.id, -1)}>−</button><span>{p.quantity}</span><button disabled={busy || p.quantity >= 99} aria-label={`增加${p.name}數量`} onClick={() => change(p.id, 1)}>＋</button><button disabled={busy} className="remove" onClick={() => change(p.id, -p.quantity)}>移除</button></div></div><strong>{money(p.price * p.quantity)}</strong></div>)}</div><div className="total"><span>總計 <small>免運費</small></span><strong>{money(total)}</strong></div><form onSubmit={checkout}><label>收件人<input name="name" required maxLength={80} autoComplete="name" disabled={busy} placeholder="請輸入姓名"/></label><label>收件地址<input name="address" required minLength={5} maxLength={300} autoComplete="street-address" disabled={busy} placeholder="請輸入完整地址"/></label>{error && <p className="error" role="alert">{error}</p>}<button className="primary checkout" disabled={busy}>{busy ? '訂單送出中…' : '確認模擬結帳 →'}</button><p className="form-note">此為示範結帳，無金流串接，請勿填寫真實個資。</p></form></>}</dialog>
  </>;
}

createRoot(document.getElementById('root')).render(<App/>);
