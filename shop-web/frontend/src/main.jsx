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
    impact: '訂單寫入 API 與 checkout',
    description: '以獨立連線持有 BEGIN IMMEDIATE，訂單寫入最多等待 10 秒後失敗 500；商品與購物袋服務仍可用。',
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

const PRODUCT_IMAGES = {
  '晨光陶瓷杯': '/images/products/morning-ceramic-mug.png',
  '日常帆布托特包': '/images/products/daily-canvas-tote.png',
  '木質香氛蠟燭': '/images/products/wood-scented-candle.png',
  '靈感方格筆記本': '/images/products/grid-notebook.png',
  '輕旅保溫水瓶': '/images/products/travel-water-bottle.png',
  '桌上綠意盆栽': '/images/products/desk-houseplant.png',
};

const CART_ID_STORAGE_KEY = 'daily-cart-id';
const LEGACY_CART_STORAGE_KEY = 'daily-cart';

function readCartId() {
  try {
    const value = localStorage.getItem(CART_ID_STORAGE_KEY);
    return value && value.trim() ? value.trim() : '';
  } catch { return ''; }
}

function readLegacyCart() {
  try {
    const value = JSON.parse(localStorage.getItem(LEGACY_CART_STORAGE_KEY) || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([id, quantity]) => /^\d+$/.test(id) && Number.isInteger(quantity) && quantity >= 1 && quantity <= 99));
  } catch { return {}; }
}

function saveCartId(cartId) {
  try { localStorage.setItem(CART_ID_STORAGE_KEY, cartId); } catch { /* The server cart remains usable without browser storage. */ }
}

function clearLegacyCart() {
  try { localStorage.removeItem(LEGACY_CART_STORAGE_KEY); } catch { /* A later load can retry the migration. */ }
}

function responseError(payload, fallback) {
  if (payload && typeof payload === 'object') {
    if (typeof payload.detail === 'string') return payload.detail;
    if (Array.isArray(payload.detail)) {
      const details = payload.detail.map(item => {
        if (typeof item === 'string') return item;
        if (item && typeof item.msg === 'string') return item.msg;
        return '';
      }).filter(Boolean);
      if (details.length) return details.join('；');
    }
    if (typeof payload.message === 'string') return payload.message;
    if (payload.error && typeof payload.error.message === 'string') return payload.error.message;
  }
  return fallback;
}

class ApiRequestError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
  }
}

async function requestApi(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new ApiRequestError(error?.message || '目前無法連線到服務。', 0);
  }
  let payload = null;
  if (response.status !== 204) {
    try { payload = await response.json(); } catch { /* The HTTP status still explains the failure. */ }
  }
  if (!response.ok) {
    throw new ApiRequestError(responseError(payload, `服務回傳 HTTP ${response.status}。`), response.status);
  }
  return payload;
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
  const rawRemaining = payload.remaining_seconds
    ?? payload.ttl_remaining_seconds
    ?? nested.remaining_seconds
    ?? nested.ttl_remaining_seconds
    ?? null;
  const remaining = rawRemaining === null ? null : Number(rawRemaining);

  if (faultId !== null && (typeof faultId !== 'string' || !FAULT_IDS.has(faultId))) {
    throw new Error('故障控制 API 回傳了未知的 fault_id。');
  }
  if (remaining !== null && (!Number.isFinite(remaining) || remaining < 0)) {
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
    remainingSeconds: remaining === null ? null : Math.floor(remaining),
  };
}

function DemoFaultPage() {
  const [faultState, setFaultState] = useState({
    loading: true,
    error: '',
    cards: FAULT_CARDS,
    activeFaultId: null,
    status: 'idle',
    remainingSeconds: null,
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
          <div className="event-remaining">持續啟用，需手動解除</div>
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
        <div><h2>操作提示</h2><p>啟用後請回到購物頁手動操作，觀察 API 回應與 log；本頁只控制故障，不會替你送出訂單。故障會持續啟用，必須在本頁手動解除。</p></div>
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
  const [cartId, setCartId] = useState(readCartId);
  const [cart, setCart] = useState(null);
  const [cartLoading, setCartLoading] = useState(true);
  const [cartError, setCartError] = useState('');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [itemPending, setItemPending] = useState('');
  const [error, setError] = useState('');
  const [order, setOrder] = useState(null);
  const [unknownOrder, setUnknownOrder] = useState(false);
  const [notice, setNotice] = useState('');
  const dialog = useRef(null);
  const submitting = useRef(false);
  const checkoutAttempt = useRef(null);
  const isEvents = hash === '#/events';

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  async function ensureCart(currentProducts) {
    setCartLoading(true);
    try {
      const legacy = readLegacyCart();
      const legacyEntries = Object.entries(legacy);
      const productById = new Map(currentProducts.map(product => [String(product.id), product]));
      const missing = legacyEntries.filter(([id]) => !productById.has(id));
      if (missing.length) {
        const labels = missing.map(([id]) => `商品 ID ${id}`);
        throw new ApiRequestError(`舊購物袋中的商品已不存在：${labels.join('、')}。內容已保留，請移除後再試。`, 409);
      }

      let id = readCartId();
      let currentCart = null;
      if (id) {
        try {
          currentCart = await requestApi(`/api/carts/${encodeURIComponent(id)}`);
        } catch (requestError) {
          if (requestError.status !== 404) throw requestError;
          id = '';
        }
      }
      if (!id) {
        currentCart = await requestApi('/api/carts', { method: 'POST' });
        id = currentCart.id;
      }

      for (const [productId, quantity] of legacyEntries) {
        const existing = currentCart.items.find(item => String(item.product_id) === productId);
        const path = `/api/carts/${encodeURIComponent(id)}/items`;
        currentCart = await requestApi(
          existing ? `${path}/${encodeURIComponent(productId)}` : path,
          {
            method: existing ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...(existing ? {} : { product_id: Number(productId) }), quantity }),
          },
        );
      }

      saveCartId(id);
      clearLegacyCart();
      setCartId(id);
      setCart(currentCart);
      setCartError('');
    } finally {
      setCartLoading(false);
    }
  }

  async function loadProducts() {
    setLoading(true); setLoadError('');
    try {
      const data = await requestApi('/api/products');
      if (!Array.isArray(data)) throw new ApiRequestError('商品服務回傳了無法辨識的資料。', 0);
      setProducts(data);
      try {
        await ensureCart(data);
      } catch (requestError) {
        setCartError(requestError.message || '暫時無法同步購物袋。');
      }
    } catch (requestError) {
      setLoadError(requestError.message || '暫時無法取得商品，請稍後再試。');
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { if (!isEvents) loadProducts(); }, [isEvents]);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open, isEvents]);
  useEffect(() => { if (isEvents) { setOpen(false); setOrder(null); setError(''); } }, [isEvents]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 2500); return () => clearTimeout(timer); }, [notice]);

  const lines = cart?.items || [];
  const count = lines.reduce((n, p) => n + p.quantity, 0);
  const total = cart?.total ?? 0;
  const visible = products.filter(p => (category === '全部商品' || p.category === category) && p.name.includes(search.trim()));
  const cartQuantities = new Map(lines.map(item => [item.product_id, item.quantity]));

  function resetCheckoutAttempt() {
    if (!unknownOrder) checkoutAttempt.current = null;
  }

  async function change(id, delta) {
    if (submitting.current || busy || unknownOrder || itemPending) return;
    const existing = lines.find(item => item.product_id === id);
    const currentQuantity = existing?.quantity || 0;
    const nextQuantity = currentQuantity + delta;
    if (nextQuantity < 0 || nextQuantity > 99 || !cartId) return;
    setItemPending(String(id));
    setError('');
    try {
      const path = `/api/carts/${encodeURIComponent(cartId)}/items`;
      const response = nextQuantity === 0
        ? await requestApi(`${path}/${encodeURIComponent(id)}`, { method: 'DELETE' })
        : await requestApi(
          existing ? `${path}/${encodeURIComponent(id)}` : path,
          {
            method: existing ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...(existing ? {} : { product_id: id }), quantity: nextQuantity }),
          },
        );
      setCart(response);
      setCartError('');
      setOrder(null);
      resetCheckoutAttempt();
      const product = products.find(item => item.id === id);
      if (product) setNotice(`已更新${product.name}的數量`);
    } catch (requestError) {
      setError(requestError.message || '購物袋更新失敗，請稍後再試。');
    } finally {
      setItemPending('');
    }
  }

  async function checkout(event) {
    event.preventDefault();
    if (submitting.current || !lines.length || !cartId) return;
    submitting.current = true; setBusy(true); setError('');
    const form = event.currentTarget;
    const data = new FormData(form);
    const payload = unknownOrder && checkoutAttempt.current
      ? checkoutAttempt.current.payload
      : { name: data.get('name'), address: data.get('address') };
    const fingerprint = JSON.stringify({ cartId, ...payload, items: lines.map(item => ({ product_id: item.product_id, quantity: item.quantity })) });
    if (!checkoutAttempt.current || checkoutAttempt.current.fingerprint !== fingerprint) {
      if (unknownOrder) {
        submitting.current = false; setBusy(false);
        return;
      }
      const key = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      checkoutAttempt.current = { key, fingerprint, payload };
    }
    try {
      const result = await requestApi(`/api/carts/${encodeURIComponent(cartId)}/checkout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': checkoutAttempt.current.key },
        body: JSON.stringify(payload),
      });
      if (!result || typeof result !== 'object') throw new ApiRequestError('訂單服務回傳了無法辨識的結果。', 0);
      setOrder(result);
      setCart(current => ({ ...current, items: [], total: 0 }));
      setUnknownOrder(false);
      checkoutAttempt.current = null;
      form.reset();
    } catch (requestError) {
      if (requestError instanceof ApiRequestError && requestError.status > 0 && requestError.status < 500) {
        setError(requestError.message || '請確認收件資料與購物袋內容。');
      } else {
        setUnknownOrder(true);
        setError('連線或服務異常，訂單狀態未確認；請用同一筆內容重試，確認前不能修改購物袋。');
      }
    }
    finally { submitting.current = false; setBusy(false); }
  }

  if (isEvents) return <DemoFaultPage />;

  return <>
    <div className="announcement">慢慢挑選，好好生活 · 每件選物都是日常的小幸福</div>
    <header className="header wrap"><a href="/#" className="brand"><span className="brand-icon">日</span><span>日日選物<small>DAILY FINDS</small></span></a><a href="#products" className="nav-link">探索選物 ↗</a><a href="/#/events" className="event-link">故障演練 ↗</a><button className="cart-button" onClick={() => setOpen(true)}>購物袋 <span>{count}</span></button></header>
    <main className="wrap">
      <section className="hero"><div className="hero-copy"><span className="eyebrow">LESS, BUT BETTER.</span><h1>把喜歡，<br/>帶進日常生活。</h1><p>從一杯咖啡到一段旅程，<br/>精選實用而美好的物件，陪你度過每一天。</p><a className="primary" href="#products">發現你的日常好物 <span>↗</span></a><div className="hero-note">用心選品 <i/> 簡單生活 <i/> 長久陪伴</div></div><div className="hero-art" aria-hidden="true"><div className="circle"/><span className="art-leaf">🌿</span><span className="art-cup">☕</span><span className="art-book">📓</span><div className="art-caption">A little joy,<br/><em>every day.</em></div><span className="art-tag">THE EVERYDAY COLLECTION — 01</span></div></section>
      <section id="products" className="catalog"><div className="section-top"><div><span className="eyebrow">CURATED FOR YOU</span><h2>為日常，挑一點喜歡</h2></div><label className="search"><span aria-hidden="true">⌕</span><input aria-label="搜尋商品" placeholder="搜尋你的生活好物" value={search} onChange={e => setSearch(e.target.value)}/></label></div><div className="filters">{['全部商品', '居家生活', '隨身好物', '文具選物'].map(c => <button key={c} className={category === c ? 'active' : ''} aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>)}<span>{visible.length} 件選物</span></div>
      {loading ? <p className="empty" role="status">正在準備美好的選物…</p> : loadError ? <div className="empty" role="alert">{loadError}<button onClick={loadProducts}>重新載入</button></div> : !visible.length ? <p className="empty">沒有符合的商品，試試其他關鍵字吧。</p> : <div className="grid">{visible.map((p, index) => { const image = PRODUCT_IMAGES[p.name]; const quantity = cartQuantities.get(p.id) || 0; return <article className="product" key={p.id}><div className="product-art" style={{ background: p.color }}><span className="product-number">0{index + 1} / DAILY FINDS</span>{image && <img className="product-photo" src={image} alt={p.name} onError={event => { event.currentTarget.hidden = true; event.currentTarget.nextElementSibling.hidden = false; }}/>}<span className="product-emoji" hidden={Boolean(image)} role="img" aria-label={p.name}>{p.icon}</span><span className="product-label">日常精選</span></div><div className="product-info"><small>{p.category}</small><h3>{p.name}</h3><p>{p.description}</p><div className="product-bottom"><strong>{money(p.price)}</strong><button disabled={cartLoading || !cartId || quantity >= 99 || Boolean(itemPending)} aria-label={`將${p.name}加入購物袋`} onClick={() => change(p.id, 1)}>＋ <span>加入購物袋</span></button></div></div></article>; })}</div>}</section>
      <section className="closing"><span>✳</span><div><h2>少一點將就，多一點喜歡。</h2><p>讓每一件留下的物品，都成為生活的好夥伴。</p></div></section>
    </main><footer className="wrap"><span>日日選物 <small>DAILY FINDS</small></span><p>簡易購物示範網站 · 結帳不會實際扣款或出貨</p></footer>
    <div className="toast" role="status">{notice}</div>
    <dialog ref={dialog} onCancel={event => { if (busy) event.preventDefault(); else setOpen(false); }} onClose={() => setOpen(false)}>
      <div className="dialog-head"><h2>你的購物袋 <small>({count})</small></h2><button disabled={busy} aria-label="關閉購物袋" onClick={() => setOpen(false)}>✕</button></div>
      {order ? <div className="success" role="status"><span>✓</span><h3>謝謝你，訂單已成立！</h3><p>訂單編號：{order.id}</p><strong>{money(order.total)}</strong><p>這是模擬訂單，不會實際扣款或出貨。</p><button className="primary" onClick={() => { setOpen(false); setOrder(null); }}>繼續探索</button></div>
        : cartLoading && !cart ? <div className="empty" role="status"><p>正在同步購物袋…</p></div>
        : cartError && !cart ? <div className="empty" role="alert"><p>{cartError}</p><button className="primary" onClick={loadProducts}>重新同步</button></div>
        : !lines.length ? <div className="empty"><p>購物袋還空著，挑一件喜歡的吧。</p><button className="primary" onClick={() => setOpen(false)}>去逛逛</button></div>
        : <>
          {cartError && <p className="error" role="alert">{cartError}</p>}
          <div className="cart-lines">{lines.map(p => { const image = PRODUCT_IMAGES[p.name]; const pending = itemPending === String(p.product_id); return <div className="cart-line" key={p.product_id}>
            {image && <img className="cart-photo" src={image} alt={p.name} onError={event => { event.currentTarget.hidden = true; event.currentTarget.nextElementSibling.hidden = false; }}/>}<span className="cart-icon" hidden={Boolean(image)} style={{ background: p.color }} role="img" aria-label={p.name}>{p.icon}</span>
            <div><h3>{p.name}</h3><small>{money(p.price)}</small><div className="quantity"><button disabled={busy || unknownOrder || pending} aria-label={`減少${p.name}數量`} onClick={() => change(p.product_id, -1)}>−</button><span>{p.quantity}</span><button disabled={busy || unknownOrder || pending || p.quantity >= 99} aria-label={`增加${p.name}數量`} onClick={() => change(p.product_id, 1)}>＋</button><button disabled={busy || unknownOrder || pending} className="remove" onClick={() => change(p.product_id, -p.quantity)}>移除</button></div></div><strong>{money(p.line_total)}</strong>
          </div>; })}</div>
          <div className="total"><span>總計 <small>以 server 回傳為準 · 免運費</small></span><strong>{money(total)}</strong></div>
          <form onSubmit={checkout}><label>收件人<input name="name" required maxLength={80} autoComplete="name" disabled={busy || unknownOrder} onChange={() => { if (!unknownOrder) { checkoutAttempt.current = null; setError(''); } }} placeholder="請輸入姓名"/></label><label>收件地址<input name="address" required minLength={5} maxLength={300} autoComplete="street-address" disabled={busy || unknownOrder} onChange={() => { if (!unknownOrder) { checkoutAttempt.current = null; setError(''); } }} placeholder="請輸入完整地址"/></label>{error && <p className="error" role="alert">{error}</p>}{unknownOrder && <p className="error" role="status">訂單結果尚未確認，請按下方按鈕以同一組 Idempotency-Key 重試。</p>}<button className="primary checkout" disabled={busy}>{busy ? '訂單送出中…' : unknownOrder ? '重試同一筆結帳 →' : '確認模擬結帳 →'}</button><p className="form-note">此為示範結帳，無金流串接，請勿填寫真實個資。</p></form>
        </>}
    </dialog>
  </>;
}

createRoot(document.getElementById('root')).render(<App/>);
