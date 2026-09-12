let data = JSON.parse(document.getElementById('snapshot').textContent);
let selected = null;
let signature = '';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {OPEN:'待合併',MERGED:'已合併',CLOSED:'已關閉',held:'等待處理',returned:'退回修改',merged:'已合併',answered:'契約已回答',env:'環境待重驗',note:'Leader 紀錄',day_start:'開始'};
const reasonLabels = {bad_format:'格式不符',outside_dir:'超出目錄範圍',no_screenshot:'缺少截圖',contract_question:'待回答契約疑問',conflict:'合併衝突',check_red:'實測未通過',merge_failed:'合併失敗'};
const stamp = value => value ? new Date(value).toLocaleString('zh-TW', {hour12:false}) : '尚無紀錄';
const ago = value => value ? Math.max(0, Math.floor((Date.now() - Date.parse(value))/1000)) : null;
function badge(value, text) { return `<span class="badge ${esc(value)}">${esc(text || labels[value] || value)}</span>`; }
function status(mr) { return mr.state === 'OPEN' && mr.gate.latest ? mr.gate.latest.kind : mr.state; }
function gateResult(mr) {
  const check = mr.gate.verdict?.check;
  return check?.ran === true ? check.pass === true ? '實際驗證通過' : check.pass === false ? '實際驗證未通過' : '實測結果不完整' : '沒有實測紀錄';
}
function empty(title, text, extra='') {return `<div class="empty ${extra}"><span class="empty-mark">◈</span><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;}
function health() {
  $('repo').textContent = data.repo;
  const offline = EXPORTED || data.sync.mode === 'offline';
  const stale = !offline && ago(data.sync.last_success_at) > Math.max(90, data.sync.interval_secs * 3);
  $('mode').textContent = offline ? '◌ 離線快照' : data.sync.error ? '● 同步失敗' : stale ? '◌ 資料已過期' : data.sync.syncing ? '◌ 正在同步' : data.sync.last_success_at ? '● 持續監控' : '◌ 等待首次同步';
  $('mode').classList.toggle('offline', offline || !!data.sync.error || stale);
  $('freshness').textContent = `最後成功同步 ${stamp(data.sync.last_success_at)}${offline ? '' : ` · 每 ${data.sync.interval_secs} 秒掃描`}`;
  const issues = [...data.gate.errors];
  if (data.sync.error) issues.unshift(`同步失敗，保留上次成功資料：${data.sync.error}`);
  if (offline) issues.unshift('目前顯示本機快照；MR 狀態停留在最後成功同步時間。');
  if (!data.sync.last_success_at) issues.unshift('尚未取得 GitHub 資料，無法判定目前 MR 數量。');
  else if (stale) issues.unshift('資料已超過更新期限；以下數字是上次成功同步的結果。');
  $('alert').hidden = issues.length === 0;
  $('alert').textContent = issues.join('\n');
  $('export').hidden = EXPORTED;
  const automation = data.automation;
  $('automation-status').hidden = !automation;
  if (automation) {
    const staleWorker = ago(automation.heartbeat_at) > 120;
    $('automation-status').textContent = `${offline ? '快照記錄 · ' : ''}${!automation.enabled || staleWorker ? '自動合併已停止或心跳逾時' : '自動讀取 → Codex 審查 → 合併 master'}${automation.processing_pr ? ` · 正在處理 MR #${automation.processing_pr}` : ' · 等待下一筆 MR'}${automation.error ? ` · ${automation.error}` : ''}`;
    $('automation-status').classList.toggle('warning', !!automation.error || staleWorker || !automation.enabled);
  }
  const heartbeat = ago(data.gate.heartbeat_at);
  $('gate-health').textContent = heartbeat === null ? 'Gate 尚無心跳紀錄' : `${EXPORTED ? '匯出時的 ' : ''}Gate 最後心跳 ${stamp(data.gate.heartbeat_at)}${heartbeat > 300 ? ' · 超過 5 分鐘' : ''}`;
}
function render() {
  health();
  const mrs = data.mrs;
  const known = !!data.sync.last_success_at;
  const held = mrs.filter(m => m.state === 'OPEN' && ['held','returned'].includes(status(m))).length;
  const actual = mrs.filter(m => m.gate.verdict?.check?.ran === true).length;
  const stats = [
    ['累計 MR', known ? mrs.length : '—', 'open · merged · closed'],
    ['已合併', known ? mrs.filter(m => m.state === 'MERGED').length : '—', '依 GitHub 合併狀態'],
    ['等待處理', known ? held : '—', '目前 head 的擱置／退回紀錄'],
    ['有實測證據', known ? actual : '—', 'gate check.ran = true']
  ];
  $('stats').innerHTML = stats.map(([label, count, note], i) => `<div class="stat ${i===1?'highlight':''}"><div class="stat-label">${label}</div><div class="stat-value">${count}<small>筆</small></div><div class="stat-note">${note}</div></div>`).join('');
  const search = $('search').value.toLowerCase();
  const rows = mrs.filter(m => (!$('part').value || ($('part').value === 'other' ? !m.part : m.part === $('part').value)) && (!$('state').value || m.state === $('state').value) && `${m.number} ${m.title} ${m.author} ${m.branch} ${m.files.map(f=>f.path).join(' ')}`.toLowerCase().includes(search));
  if (!rows.some(m=>m.number === selected)) selected = rows[0]?.number ?? null;
  $('count').textContent = known ? rows.length : '—';
  $('list').innerHTML = rows.length ? rows.map(m=>`<button type="button" class="mr-card ${selected===m.number?'selected':''}" data-pr="${m.number}" aria-pressed="${selected===m.number}"><div class="card-top"><span>#${m.number} · ${esc(m.part || '其他')}${m.nn?` / ${esc(m.nn)}`:''}</span>${badge(status(m), m.draft?'草稿':null)}</div><h3>${esc(m.title)}</h3><p class="card-summary">${esc(m.summary.text)}</p><div class="card-bottom"><span>@${esc(m.author)}</span><span>${m.changed_files} files <span class="add">+${m.additions}</span> <span class="del">−${m.deletions}</span></span></div></button>`).join('') : empty(known && !mrs.length ? '等待第一筆 MR' : known ? '沒有符合的 MR' : '等待資料同步', known && !mrs.length ? 'GitHub 目前尚無 PR。團隊提交後，這裡會自動出現改動摘要與驗證紀錄。' : known ? '調整搜尋文字或篩選條件。' : '成功讀取 GitHub 之後，才會顯示筆數與狀態。');
  $('list').querySelectorAll('[data-pr]').forEach(button=>button.addEventListener('click',()=>{selected=Number(button.dataset.pr);render();}));
  detail(rows.find(m=>m.number===selected));
}
function detail(mr) {
  if (!mr) { $('detail').innerHTML = empty('每筆改動，都有一份交付紀錄', '選擇一筆 MR，查看改了什麼、作者如何驗證、哪些還沒驗證，以及 gate 的判定過程。', 'detail-empty'); return; }
  const gate = mr.gate;
  const verdict = gate.verdict;
  const report = mr.report.latest;
  const href = /^https:\/\/github\.com\//.test(mr.url) ? mr.url : '#';
  const history = gate.history.slice().reverse();
  $('detail').innerHTML = `
    <div class="detail-head"><span>MR #${mr.number} · ${esc(mr.part || '其他')} ${esc(mr.nn || '')}</span>${badge(mr.state, mr.draft ? '草稿' : null)}</div>
    <h2 class="detail-title"><a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(mr.title)} ↗</a></h2>
    <p class="metadata">@${esc(mr.author)} · ${esc(mr.branch)} → ${esc(mr.base_branch)}<br>head ${esc(mr.sha.slice(0,12))} · 更新於 ${stamp(mr.updated_at)}</p>
    <div class="summary-box"><h3>這筆 MR 改了什麼</h3><p>${esc(mr.summary.text)}</p><small>來源：${mr.summary.source==='author_report'?'作者「做了什麼」最新一輪回報':'PR 標題（缺少改動回報）'} · 未由監控工具驗證</small></div>
    <div class="evidence"><div><strong>作者自述</strong><p>${mr.self_reported.check ? `註解 check=${esc(mr.self_reported.check)}；見下方回報` : '未提供 nightwatch check 註解'}</p></div><div><strong>Gate 實測</strong><p>${esc(gateResult(mr))}</p></div></div>
    ${mr.review?`<section class="review-box"><h3>Codex 靜態審查 · ${mr.review.decision==='approve'?'同意合併':'保留待處理'}</h3><p>${esc(mr.review.summary_zh)}</p>${mr.review.findings.map(f=>`<p class="review-finding">${esc(f.severity)} · ${esc(f.message_zh)}</p>`).join('')}${mr.review.waivers.length?`<p class="review-finding">逐筆例外：${mr.review.waivers.map(esc).join('、')} · ${esc(mr.review.waiver_reason_zh)}</p>`:''}<small>已讀取差異；沒有執行 MR 的程式或測試。審查 base ${esc(mr.review.base_sha.slice(0,12))}</small></section>`:''}
    ${!mr.files_complete?'<p class="notice">GitHub 檔案清單不完整，下方僅列已取得的檔案。</p>':''}
    ${gate.has_previous_head_events && !gate.latest?'<p class="notice">已有新 commit；舊 head 的判定只保留在歷史中，目前 head 尚無 gate 判定。</p>':''}
    ${mr.report.missing_sections.length?`<p class="notice">最新回報缺少：${mr.report.missing_sections.map(esc).join('、')}</p>`:''}
    <section class="detail-section"><h3>驗證依據 · 作者回報</h3><p class="prose">${esc(report['依據什麼驗證的'] || '尚未提供')}</p></section>
    <section class="detail-section"><h3>沒有驗證的 · 作者回報</h3><p class="prose">${esc(report['沒有驗證的'] || '尚未提供')}</p></section>
    <section class="detail-section"><h3>契約疑問 · 作者回報</h3><p class="prose">${esc(report['契約疑問'] || '尚未提供')}</p></section>
    <section class="detail-section"><h3>改動檔案 <span class="micro">${mr.files.length} / ${mr.changed_files} FILES</span></h3>${mr.files.map(f=>`<details class="file-row"><summary><span>${esc(f.path)}</span><span class="add">+${f.additions} <span class="del">−${f.deletions}</span></span></summary><div class="file-note">${esc(f.status)}${f.previous_path?` · 原路徑 ${esc(f.previous_path)}`:''} · ${f.patch===null?'GitHub 未提供 patch，可能為二進位或大型檔案':'GitHub patch 節錄，不保證完整'}</div>${f.patch!==null?`<pre class="patch">${esc(f.patch)}</pre>`:''}</details>`).join('') || '<p class="prose">沒有檔案改動紀錄。</p>'}</section>
    <section class="detail-section"><h3>Gate 判定歷程 <span class="micro">${history.length} EVENTS</span></h3>${verdict?.reasons?.length?`<p class="notice">${verdict.reasons.map(r=>esc(reasonLabels[r] || r)).join(' · ')}</p>`:''}${history.length?`<ol class="timeline">${history.map(e=>`<li><strong>${esc(labels[e.kind] || e.kind)} · ${esc(e.by)}${e.attempt?` · 第 ${e.attempt} 次`:''}</strong><small>${stamp(e.ts)} · ${esc(e.sha || '')}${e.sha && !mr.sha.startsWith(e.sha)?' · 舊 head':''}</small><p>${esc(e.note || '')}${e.reasons?.length?'\n'+e.reasons.map(r=>esc(reasonLabels[r] || r)).join('、'):''}${e.check?.ran===true?`\n實測：${e.check.pass===true?'通過':e.check.pass===false?'未通過':'結果不完整'} · ${esc(e.check.secs)} 秒` : ['held','merged'].includes(e.kind)?'\n此判定未執行實測':''}</p></li>`).join('')}</ol>`:'<p class="prose">尚無 gate 事件。此監控頁只讀取資料；檢查、合併與退回由 gate 流程執行。</p>'}</section>
    <details class="report"><summary>展開完整 PR 回報 · ${mr.report.rounds.length} 輪</summary><pre class="prose">${esc(mr.report.body || 'PR 內文為空')}</pre></details>
    <details class="report"><summary>查看此 MR 的 structured output</summary><pre class="patch">${esc(JSON.stringify(mr,null,2))}</pre></details>`;
}
['search','part','state'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',render));
$('download').addEventListener('click',()=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const a=document.createElement('a'); a.href=url; a.download='nightwatch-snapshot.json'; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function refresh() {
  try {
    const response = await fetch('/api/snapshot', {cache:'no-store', signal:AbortSignal.timeout(10000)});
    if(!response.ok) throw new Error(`HTTP ${response.status}`);
    const next = await response.json();
    const nextSignature=JSON.stringify([next.mrs,next.gate.events,Boolean(next.sync.last_success_at)]);
    data=next;
    if(nextSignature!==signature) {signature=nextSignature;render();} else health();
  } catch(error) {
    $('alert').hidden=false;
    $('alert').textContent=`無法連到本機監控服務，保留畫面上的資料：${error.message}`;
    $('mode').textContent='◌ 監控服務中斷';
    $('mode').classList.add('offline');
  } finally { setTimeout(refresh,1000); }
}
render();
if(!EXPORTED) setTimeout(refresh,1000);
