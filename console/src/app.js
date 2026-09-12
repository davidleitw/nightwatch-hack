import {apiPath,readJSON,validateState,validateGraph,validateIncident,loadRecording,projectRecording} from './data.js';
import {setupConnection} from './connect.js';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const phaseNames={baseline:'基線收集中',detected:'偵測到異常',investigating:'排查中',awaiting_approval:'等待批准',executing:'執行修復中',verifying:'驗證中',recovered:'已修復',unresolved:'未解決',closed:'已結案'};
const outcomeNames={recovered:'已修復',recovered_mitigated:'已繞過（根因還在）',expired_before_approval:'批准前逾時',expired_before_execution:'執行前逾時',aborted_by_operator:'操作者中止',audit_rejected:'稽核未通過',verification_failed:'驗證失敗',action_failed:'動作失敗',budget_exhausted:'預算用完',no_remediation_path:'沒有可用的修復',safety_restore_failed:'安全還原失敗',unresolved:'未解決'};
const statusNames={ok:'正常',warning:'警告',failing:'異常',unknown:'無資料'};
const assessmentNames={unassessed:'未判定',suspect:'懷疑',origin:'根因',ruled_out:'已排除'};
const kinds={service:'服務',datastore:'資料庫',queue:'佇列',volume:'儲存',external:'外部服務',synthetic:'合成顧客'};
const trends={rising:'↗ 上升',falling:'↘ 下降',flat:'→ 持平',na:'無趨勢'};
const isClosed=i=>Boolean(i.closed_at)||['closed','recovered','unresolved'].includes(i.phase);
const time=value=>value && Number.isFinite(Date.parse(value))?new Date(value).toISOString().replace('T',' ').replace(/\.\d+Z$/,''):'—';
const number=(value,unit='',factor=1)=>typeof value==='number'&&Number.isFinite(value)?`${(value*factor).toLocaleString('en-US',{maximumFractionDigits:1})}${unit}`:'—';
const source=new URLSearchParams(location.search).get('source')==='recording'?'recording':'live';
let journal=[], journalFilter="all";
let localMock=false;
let state=null, incidents=[], selectedNode=null, selectedIncident=null, incidentDetail=null, recording=null;
let zoom=1, autoFit=true, filterStatus='all', listNote='', stream=null, retryTimer=null, refreshTimer=null, freshnessTimer=null, refreshBusy=false, refreshAgain=false;
let lastActivity=0, retryDelay=1000, cursor=0, detailRequest=0, shapeKey='', snapshotWidth=0, snapshotHeight=0;
const errors=new Map();
function error(key,message){if(message)errors.set(key,message);else errors.delete(key);$('errors').innerHTML=[...errors.values()].map(message=>`<div class="error">${esc(message)}</div>`).join('');}
function connection(label,kind=''){ $('connection').textContent=(localMock && source==='live'?'模擬 · ':'')+label; $('connection').className=kind; }
function badge(incident){return `<span class="badge ${!isClosed(incident)?'active':['unresolved','closed'].includes(incident.phase)&&incident.outcome!=='recovered'?'failed':''}">${esc(phaseNames[incident.phase]||incident.phase)}</span>`;}
function allIncidents(){const map=new Map(incidents.map(i=>[i.id,i]));if(state?.incident)map.set(state.incident.id,{...map.get(state.incident.id),...state.incident});return [...map.values()].sort((a,b)=>Date.parse(b.detected_at)-Date.parse(a.detected_at));}
function primary(node){const axis=node.primary_axis;const values={traffic:number(node.traffic,' req/s'),errors:number(node.errors,'%',100),latency:number(node.p95_ms,' ms'),saturation:number(node.saturation,'%',100),liveness:node.alive===true?'存活':node.alive===false?'無回應':'—'};const labels={traffic:'請求量',errors:'錯誤率',latency:'P95',saturation:node.sat_label||'飽和度',liveness:'存活'};return axis?`${labels[axis]||axis} ${values[axis]||'—'}`:'主要量測 —';}
function renderGraph(){
  if(!state)return;
  const graph=state.graph_now, caps=state.capabilities.nodes;
  const signature=JSON.stringify(caps.map(n=>[n.id,n.layout.row,n.layout.col]));
  if(shapeKey!==signature){shapeKey=signature;autoFit=true;}
  const rows=caps.map(n=>n.layout.row), cols=caps.map(n=>n.layout.col);
  const minRow=Math.min(0,...rows), minCol=Math.min(0,...cols);
  snapshotWidth=(Math.max(0,...cols)-minCol+1)*132+24;
  snapshotHeight=(Math.max(0,...rows)-minRow+1)*132+16;
  const positions=new Map(caps.map(n=>[n.id,{x:24+(n.layout.col-minCol)*132,y:14+(n.layout.row-minRow)*132}]));
  if(autoFit)zoom=Math.min(1.15,Math.max(.55,($('graph-viewport').clientWidth-15)/snapshotWidth));
  const search=$('node-search').value.toLowerCase().trim(), filter=$('health-filter').value;
  const matches=new Set(graph.nodes.filter(n=>(!search||n.id.toLowerCase().includes(search))&&(filter==='all'||n.status===filter)).map(n=>n.id));
  $('total-nodes').textContent=graph.nodes.length;$('failing-count').textContent=graph.nodes.filter(n=>n.status==='failing').length;$('warning-count').textContent=graph.nodes.filter(n=>n.status==='warning').length;
  $('active-phase').textContent=state.incident?phaseNames[state.incident.phase]:'無進行中事故';
  $('snapshot-at').textContent=time(graph.at);$('graph-count').textContent=`${graph.nodes.length} 個節點 / ${graph.edges.length} 條連線`;
  $('match-count').textContent=`${matches.size} / ${graph.nodes.length} 個節點符合條件`;
  $('graph-empty').hidden=graph.nodes.length>0;$('graph-empty').textContent='目前沒有服務節點。';
  $('graph-canvas').style.width=`${snapshotWidth*zoom}px`;$('graph-canvas').style.height=`${snapshotHeight*zoom}px`;
  $('nodes').style.width=`${snapshotWidth}px`;$('nodes').style.height=`${snapshotHeight}px`;$('nodes').style.transform=`scale(${zoom})`;
  $('edges').setAttribute('width',snapshotWidth);$('edges').setAttribute('height',snapshotHeight);$('edges').style.transform=`scale(${zoom})`;
  $('zoom-value').textContent=`${Math.round(zoom*100)}%`;
  let edgeHTML='<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7" fill="none" stroke="#9cb48c"/></marker></defs>';
  graph.edges.forEach((e,index)=>{const a=positions.get(e.from),b=positions.get(e.to);let d;
    if(a.y===b.y && Math.abs(a.x-b.x)<133){const right=a.x<b.x;d=`M${a.x+(right?120:0)} ${a.y+53}H${b.x+(right?0:120)}`;}
    else if(a.y===b.y){const lane=a.y+116+(index%3)*4;d=`M${a.x+60} ${a.y+106}V${lane}H${b.x+60}V${b.y+106}`;}
    else{const down=b.y>a.y,start=a.y+(down?106:0),end=b.y+(down?0:106),lane=down?end-10-(index%3)*4:start-10-(index%3)*4;d=`M${a.x+60} ${start}V${lane}H${b.x+60}V${end}`;}
    edgeHTML+=`<path class="edge ${e.from===selectedNode||e.to===selectedNode?'related':''} ${!e.observed?'unobserved':''} ${(!matches.has(e.from)&&!matches.has(e.to))?'dim':''}" d="${d}" marker-end="url(#arrow)"><title>${esc(e.from)} → ${esc(e.to)} · ${esc(e.kind)} · ${e.observed?'已觀測':'近期未觀測'} · ${esc(number(e.rps,' req/s'))}</title></path>`;
  });$('edges').innerHTML=edgeHTML;
  $('nodes').innerHTML=graph.nodes.map(n=>{const p=positions.get(n.id);return `<button class="graph-node ${esc(n.kind)} ${esc(n.assessment)} ${n.id===selectedNode?'selected':''} ${matches.has(n.id)?'':'dim'}" style="left:${p.x}px;top:${p.y}px" data-node="${esc(n.id)}" aria-label="選取 ${esc(n.id)}" aria-pressed="${n.id===selectedNode}"><span class="node-name">${esc(n.id)}</span><span class="node-meta"><span class="${esc(n.status)}"><i class="dot ${esc(n.status)}"></i>${statusNames[n.status]}</span><span class="node-assessment">${n.assessment==='unassessed'?kinds[n.kind]||n.kind:assessmentNames[n.assessment]}</span></span><span class="node-value">${esc(primary(n))}</span></button>`;}).join('');
  $('nodes').querySelectorAll('[data-node]').forEach(button=>button.onclick=()=>{selectedNode=button.dataset.node;renderGraph();renderNode();$('nodes').querySelectorAll('[data-node]').forEach(b=>{if(b.dataset.node===selectedNode)b.focus({preventScroll:true});});});
  $('sources').innerHTML=`<span>資料來源健康${source==='recording'?'（錄影當時）':''}</span>`+['prometheus','jaeger','logstore'].map(key=>{const s=graph.sources[key];return `<span><i class="dot ${s?.ok===true?'ok':s?.ok===false?'failing':'unknown'}"></i>${key} · ${s?.ok===true?'正常':s?.ok===false?'無回應':'無資料'} · ${esc(number(s?.age_secs,' 秒前'))}</span>`;}).join('');
  $('footer-status').textContent=`${source==='recording'?'錄影資料':localMock?'本機模擬資料':'即時資料'} · ${state.run.id} · 快照 #${graph.seq}`;
}
function renderNode(){
 if(!state)return;
 const n=state.graph_now.nodes.find(n=>n.id===selectedNode);
 if(!n){$('node-detail').innerHTML='<div class="empty">點選節點查看量測與相鄰連線。</div>';return;}
 const metrics=[['請求量',number(n.traffic,' req/s'),n.trend?.traffic],['錯誤率',number(n.errors,'%',100),n.trend?.errors],['P95 延遲',number(n.p95_ms,' ms'),n.trend?.latency],['飽和度',number(n.saturation,'%',100),n.trend?.saturation],['存活',n.alive===true?'存活':n.alive===false?'無回應':'—',null]];
 const neighbors=state.graph_now.edges.filter(e=>e.from===n.id||e.to===n.id);
 $('node-detail').innerHTML=`<div class="node-detail-top"><h3>${esc(n.id)}</h3><span class="${esc(n.status)}">${statusNames[n.status]}</span><span>Agent：${assessmentNames[n.assessment]}</span><span class="metric-label">${esc(kinds[n.kind]||n.kind)} ${n.revision?'· '+esc(n.revision):''}</span></div><div class="node-metrics">${metrics.map(([label,value,trend])=>`<div><span class="metric-label">${label}</span><strong>${esc(value)}</strong> <small>${trend?trends[trend]||trend:''}</small></div>`).join('')}</div><details class="neighbors"><summary>${neighbors.length} 條相鄰連線 · 查看方向與流量</summary><ul>${neighbors.map(e=>`<li>${esc(e.from)} → ${esc(e.to)} · ${esc(e.kind)}${e.kind==='consumes'?'（前者消費後者）':''} · ${esc(number(e.rps,' req/s'))} · ${e.observed?'已觀測':'近期未觀測，不代表斷線'}</li>`).join('')}</ul></details>`;
}
function renderCurrent(){const i=state?.incident;$('current-incident').innerHTML=i?`<div><span class="eyebrow">${source==='recording'?'錄影時間的事故':'目前事故'} · ${esc(i.id)}</span><h3>${esc(i.detection?.summary_zh||phaseNames[i.phase])}</h3><p>${esc(state.next_step_zh||'')}</p></div>${badge(i)}<a href="#incidents/${encodeURIComponent(i.id)}">查看事故 →</a>`:'<div><h3>目前沒有進行中的事故</h3><p>服務圖仍會持續顯示最新快照。</p></div><a href="#incidents">查看事故列表 →</a>';}
function renderList(){
 const all=allIncidents(), query=$('incident-search').value.toLowerCase().trim();
 const rows=all.filter(i=>(filterStatus==='all'||(filterStatus==='closed'?isClosed(i):!isClosed(i)))&&[i.id,i.card_id,i.phase,phaseNames[i.phase],i.outcome,outcomeNames[i.outcome]].some(value=>String(value||'').toLowerCase().includes(query)));
 $('nav-count').textContent=all.length;$('list-note').textContent=listNote;
 $('incident-rows').innerHTML=rows.map(i=>`<tr><td>${esc(i.id)}</td><td>${badge(i)}</td><td>${esc(i.card_id||'結案後揭曉')}</td><td>${time(i.detected_at)}</td><td>${esc(i.outcome?outcomeNames[i.outcome]||i.outcome:'—')}</td><td><a class="detail-link" href="#incidents/${encodeURIComponent(i.id)}">查看詳情 ↗</a></td></tr>`).join('');
 $('list-empty').hidden=rows.length>0;$('list-empty').textContent=all.length?'沒有符合搜尋或篩選條件的事故。':errors.has('list')||errors.has('state')?'無法取得事故資料；請查看上方錯誤並重試。':'目前沒有事故紀錄。';
 $('list-count').textContent=`顯示 ${rows.length} / ${all.length} 件事故 · 最新偵測在前${source==='recording'?' · 此錄影僅包含一件事故':''}`;
}
function renderIncidentDetail(){
 const i=incidentDetail;$('incident-detail').hidden=!selectedIncident;
 if(!selectedIncident)return;
 if(!i){$('incident-detail').innerHTML=`<div class="empty">${errors.has('detail')?'事故詳情載入失敗；可按「更新資料」重試。':'正在載入事故詳情…'}</div>`;return;}
 const root=i.hypothesis?.root_cause, evidence=i.evidence||[];
 $('incident-detail').innerHTML=`<div class="panel-header"><div><h2>事故詳情</h2><span>${esc(i.id)}</span></div>${badge(i)}</div><div class="detail-body"><p class="detail-summary">${esc(i.detection?.summary_zh||'後端未提供事故摘要。')}</p><div class="facts"><div><span class="metric-label">偵測時間 · UTC</span><strong>${time(i.detected_at)}</strong></div><div><span class="metric-label">結案時間 · UTC</span><strong>${time(i.closed_at)}</strong></div><div><span class="metric-label">偵測來源 / 規則</span><strong>${esc(i.detection?.source||'—')} / ${esc(i.detection?.rule||'—')}</strong></div><div><span class="metric-label">結局</span><strong>${esc(outcomeNames[i.outcome]||i.outcome||'尚未結案')}</strong></div></div><div class="detail-section"><h3>Agent 根因判斷</h3><p>${esc(root?.summary_zh||'尚未提出根因結論。')}</p>${root?.node?`<button data-locate="${esc(root.node)}">${esc(root.node)} ↗ 在拓樸中定位</button>`:''}<p class="detail-disclaimer">${i.hypothesis?.confidence!=null?'模型信心 '+esc(number(i.hypothesis.confidence,'%',100))+' · ':''}定位只顯示${source==='recording'?'目前錄影時間':'目前'}的服務快照，不切換成事故歷史圖。</p></div><div class="detail-section"><h3>關聯節點</h3>${(i.nodes||[]).map(n=>`<button data-locate="${esc(n.id)}">${esc(n.id)} · ${esc(assessmentNames[n.assessment]||n.assessment||'未判定')}</button>`).join('')||'<p>後端未提供關聯節點。</p>'}</div><div class="detail-section"><h3>已記錄證據 · ${evidence.length} 筆</h3>${evidence.map(e=>`<div class="evidence-row"><small>${esc(e.id)} · ${esc(e.tool||e.source||'—')}</small>${esc(e.summary_zh||e.summary||'未提供摘要')}</div>`).join('')||'<p>尚無證據。</p>'}</div></div>`;
 $('incident-detail').querySelectorAll('[data-locate]').forEach(button=>button.onclick=()=>{const id=button.dataset.locate;if(!state?.graph_now.nodes.some(n=>n.id===id)){error('locate',`圖外來源：${id} 不在目前的服務節點清單。`);return;}error('locate');selectedNode=id;$('node-search').value='';$('health-filter').value='all';location.hash='topology';renderGraph();renderNode();for(const n of $('nodes').querySelectorAll('[data-node]'))if(n.dataset.node===id)n.scrollIntoView({block:'nearest',inline:'center'});});
}
async function selectIncident(id){
 selectedIncident=id;incidentDetail=null;error('detail');renderIncidentDetail();const request=++detailRequest;
 if(!id)return;
 try{let detail;if(source==='recording'){if(state?.incident?.id!==id)throw new Error('此錄影時間沒有這件事故。');detail=state.incident;}else detail=validateIncident(await readJSON(`/api/incidents/${encodeURIComponent(id)}`),true);
  if(request!==detailRequest)return;if(detail.id!==id)throw new Error('事故詳情 ID 與要求的事故不一致。');incidentDetail=detail;renderIncidentDetail();
 }catch(e){if(request!==detailRequest)return;error('detail',e.message);renderIncidentDetail();}
}
function route(){const hash=location.hash.slice(1), parts=hash.split('/');const list=parts[0]==='incidents';$('topology-view').hidden=list;$('incidents-view').hidden=!list;$('nav-topology').setAttribute('aria-current',list?'false':'page');$('nav-incidents').setAttribute('aria-current',list?'page':'false');$('page-title').textContent=list?'事故列表':'調查工作台';$('page-description').textContent=list?'查看事故進度、偵測來源與已記錄的證據。':'左側追蹤服務狀態，右側查看 Monitor 與 Agent 的調查紀錄。';let id=null;try{id=list&&parts[1]?decodeURIComponent(parts[1]):null;}catch{error('route','事故網址編碼不合法。');}if(id!==selectedIncident)selectIncident(id);if(!list){renderGraph();renderNode();}renderList();}
function adopt(next){validateState(next);
 if(state?.run.id===next.run.id){
  // A cleared incident has no revision; order full states by their server time.
  if(Date.parse(next.server_now)<Date.parse(state.server_now))return;
  if(state.incident && next.incident && state.incident.id===next.incident.id && state.incident.revision>next.incident.revision)return;
  // Graph SSE can advance independently while GET /api/state is in flight.
  if(state.graph_now.seq>next.graph_now.seq)next={...next,graph_now:state.graph_now};
 }
 const oldRun=state?.run.id;if(oldRun && oldRun!==next.run.id){cursor=0;selectedNode=null;shapeKey='';journal=[];}
 state=next;cursor=Math.max(cursor,next.incident?.revision||0);if(!state.graph_now.nodes.some(n=>n.id===selectedNode))selectedNode=state.graph_now.nodes.find(n=>n.assessment==='origin')?.id||state.graph_now.nodes.find(n=>n.status==='failing')?.id||state.graph_now.nodes[0]?.id||null;
 renderGraph();renderNode();renderCurrent();renderList();renderInvestigation();if(selectedIncident===next.incident?.id){incidentDetail=next.incident;renderIncidentDetail();}
}
function showRecording(seconds){try{const next=projectRecording(recording,seconds);journal=recording.events.filter(line=>line.event==='incident' && (typeof line.data.event.t==='number' ? line.data.event.t<=seconds : Date.parse(line.data.event.occurred_at)<=Date.parse(next.server_now))).map(line=>({kind:'incident',data:line.data.event}));state=null;adopt(next);incidents=next.incident?[next.incident]:[];$('replay-label').textContent=`+${seconds} 秒`;listNote='此清單只包含錄影時間當下可見的事故，並非完整歷史。';connection('錄影 · 已暫停');renderList();if(selectedIncident)selectIncident(selectedIncident);}catch(e){error('recording',e.message);}}
async function refreshLive(){
 if(refreshBusy){refreshAgain=true;return;}refreshBusy=true;$('refresh').disabled=true;
 try{const results=await Promise.allSettled([readJSON('/api/state'),readJSON('/api/incidents')]);
  if(results[0].status==='fulfilled'){try{adopt(results[0].value);error('state');}catch(e){error('state',e.message);}}else error('state',`讀取服務拓樸失敗：${results[0].reason.message}`);
  if(results[1].status==='fulfilled'){try{const data=results[1].value;if(!Array.isArray(data))throw new Error('事故列表不是契約規定的陣列。');data.forEach(i=>validateIncident(i));incidents=data;listNote='';error('list');}catch(e){error('list',e.message);}}
  else{const e=results[1].reason;if(e.status===404){listNote='後端未提供歷史事故列表（HTTP 404）；目前僅顯示 /api/state 中的事故。';error('list');}else error('list',`讀取事故列表失敗：${e.message}`);}
  renderList();
 }finally{refreshBusy=false;$('refresh').disabled=false;if(refreshAgain){refreshAgain=false;scheduleRefresh();}}
}
function scheduleRefresh(){if(refreshTimer)return;refreshTimer=setTimeout(()=>{refreshTimer=null;refreshLive();},150);}
function reconnect(){stream?.close();stream=null;if(retryTimer)return;connection('disconnected · 正在重連','disconnected');retryTimer=setTimeout(()=>{retryTimer=null;connect();},retryDelay);retryDelay=Math.min(8000,retryDelay*2);}
function connect(){
 const query=state?.run.id?`?cursor=${encodeURIComponent(state.run.id+':'+cursor)}`:'';
 stream=new EventSource(apiPath('/events'+query));const current=stream;
 const receive=handler=>event=>{if(stream!==current)return;lastActivity=Date.now();retryDelay=1000;connection('即時連線');try{handler(JSON.parse(event.data));error('stream');}catch(e){error('stream',`SSE 資料錯誤：${e.message}`);scheduleRefresh();}};
 current.addEventListener('state',receive(data=>{adopt(data);error('state');}));
 current.addEventListener('graph',receive(data=>{if(!state){scheduleRefresh();return;}validateGraph(data,state.capabilities);if(data.seq>state.graph_now.seq){state.graph_now=data;renderGraph();renderNode();}}));
 current.addEventListener('incident',receive(data=>{if(!state || data.run_id!==state.run.id){scheduleRefresh();return;}if(!Number.isInteger(data.revision)||!Number.isInteger(data.base_revision))throw new Error('incident revision 不合法。');appendJournal('incident',data.event);if(data.revision<=cursor)return;cursor=data.revision;scheduleRefresh();}));
 current.addEventListener('run',receive(()=>{cursor=0;state=null;shapeKey='';journal=[];renderInvestigation();scheduleRefresh();}));
 current.addEventListener('log',receive(data=>appendJournal('monitor',data)));
 for(const name of ['ping','faults','readiness'])current.addEventListener(name,receive(()=>{}));
 current.onerror=()=>{if(stream===current)reconnect();};
}
$('source').value=source;$('source').onchange=e=>{const url=new URL(location.href);url.searchParams.set('source',e.target.value);location.href=url.href;};
$('refresh').onclick=async()=>{if(source==='recording'){showRecording(Number($('replay-time').value));}else{await refreshLive();if(selectedIncident)selectIncident(selectedIncident);}};
$('node-search').oninput=renderGraph;$('health-filter').onchange=renderGraph;
$('incident-search').oninput=renderList;document.querySelectorAll('[data-status]').forEach(button=>button.onclick=()=>{filterStatus=button.dataset.status;document.querySelectorAll('[data-status]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));renderList();});
function changeZoom(amount){autoFit=false;zoom=Math.min(1.5,Math.max(.55,zoom+amount));renderGraph();}
$('zoom-out').onclick=()=>changeZoom(-.1);$('zoom-in').onclick=()=>changeZoom(.1);$('fit').onclick=()=>{autoFit=true;renderGraph();};
$('replay-time').oninput=e=>showRecording(Number(e.target.value));
window.addEventListener('hashchange',route);window.addEventListener('resize',()=>{if(autoFit&&!$('topology-view').hidden)renderGraph();});
window.addEventListener('pagehide',()=>{stream?.close();clearTimeout(retryTimer);clearTimeout(refreshTimer);clearInterval(freshnessTimer);});
async function start(){localMock=await setupConnection(source);route();if(source==='recording'){$('recording').hidden=false;try{recording=await loadRecording();showRecording(60);if(selectedIncident)selectIncident(selectedIncident);}catch(e){error('recording',`錄影載入失敗：${e.message}`);connection('錄影載入失敗','disconnected');}}else{await refreshLive();connect();freshnessTimer=setInterval(()=>{const age=Date.now()-lastActivity;if(!lastActivity)return;if(age>=15000){connection('disconnected · 資料未更新','disconnected');reconnect();}else if(age>=6000)connection('stale · 資料延遲','stale');},1000);}}
start().catch(e=>error('startup',e.message));

function appendJournal(kind,data){
 if(kind==='monitor'){
  if(data?.schema_version!=='nightwatch.log.v1'||!data.event_id||!data.monitor_id||typeof data.message!=='string'||!Number.isFinite(Date.parse(data.occurred_at))||!['TRACE','DEBUG','INFO','WARN','ERROR','FATAL'].includes(data.level)||!Array.isArray(data.refs?.node_ids))throw new Error('Monitor log 不符合 nightwatch.log.v1 草案。');
 }else if(!data?.id||!data.actor?.kind||!data.timeline||!Number.isFinite(Date.parse(data.occurred_at)))throw new Error('調查事件缺少必要欄位。');
 const key=kind==='monitor'?JSON.stringify([data.monitor_id,data.event_id]):data.id;
 if(journal.some(row=>row.kind===kind&&(kind==='monitor'?JSON.stringify([row.data.monitor_id,row.data.event_id]):row.data.id)===key))return;
 journal.push({kind,data});renderInvestigation();
}
function eventSource(row){return row.kind==='monitor'?'monitor':['model','tool'].includes(row.data.actor?.kind)?'agent':'system';}
function locateInvestigation(id){
 if(!state?.graph_now.nodes.some(n=>n.id===id)){error('locate',`圖外來源：${id} 不在目前的服務節點清單。`);return;}
 error('locate');selectedNode=id;$('node-search').value='';$('health-filter').value='all';renderGraph();renderNode();
 for(const n of $('nodes').querySelectorAll('[data-node]'))if(n.dataset.node===id)n.scrollIntoView({block:'nearest',inline:'center'});
}
function bindInvestigationNodes(){document.querySelectorAll('[data-investigation-node]').forEach(b=>b.onclick=()=>locateInvestigation(b.dataset.investigationNode));}
function nodeLink(id){return `<button class="node-link" data-investigation-node="${esc(id)}">${esc(id)} ↗</button>`;}
function renderInvestigation(){
 const i=state?.incident,root=i?.hypothesis?.root_cause;
 $('investigation-phase').innerHTML=i?badge(i):'';
 $('investigation-summary').innerHTML=`<div class="report-kicker">${root?'AI 結論':'目前調查'}</div><h3>${esc(root?.summary_zh||i?.detection?.summary_zh||'等待事故資料')}</h3><div class="summary-meta">${root?.node?nodeLink(root.node):''}<span>${esc(i?.id||'尚無事故')}</span></div>`;
 const rows=journal.filter(row=>journalFilter==='all'||eventSource(row)===journalFilter);
 $('journal-count').textContent=`${rows.length} 筆`;
 $('journal-note').textContent=source==='recording'?'依錄影接收順序 · 此錄影未包含 Monitor 原始 log':'依接收順序 · 僅保留本頁收到的事件；重新整理與斷線可能缺漏';
 const actors={model:'模型',tool:'工具',go:'系統',operator:'操作者',grafana:'Grafana 告警'};
 $('journal-entries').innerHTML=rows.map(row=>{const e=row.data,kind=eventSource(row),monitor=row.kind==='monitor';const nodes=e.refs?.node_ids||[];const args=e.payload?.args;const target=args?.node||args?.service;const ids=[...new Set([...nodes,...(typeof target==='string'?[target]:[])])];return `<article class="journal-entry ${kind}"><div class="journal-meta"><strong>${monitor?'Monitor':kind==='agent'?'Agent':'系統'}</strong><span>${esc(monitor?e.monitor_id:actors[e.actor.kind]||e.actor.kind)}</span><time>${esc(time(e.occurred_at).slice(11))} UTC</time></div><p>${esc(monitor?e.message:e.timeline.summary_zh||e.timeline.title||e.type)}</p><div class="journal-links">${ids.map(nodeLink).join('')}${monitor?`<span class="log-level">${esc(e.level)}</span>`:e.payload?.tool?`<span class="tool-name">${esc(e.payload.tool)}</span>`:''}</div><details><summary>${monitor?'原始 log':'事件內容'}${!monitor&&e.refs?.evidence_ids?.length?' · '+e.refs.evidence_ids.length+' 筆證據':''}</summary><pre class="raw">${esc(JSON.stringify(monitor?e:e.payload||{},null,2))}</pre></details></article>`;}).join('')||`<div class="empty">${journalFilter==='monitor'?'尚未收到 Monitor log。':'目前沒有符合來源的調查事件。'}</div>`;
 const onset=i?.hypothesis?.timeline?.onset, propagation=i?.hypothesis?.timeline?.propagation||[], evidence=i?.evidence||[];
 $('investigation-report').innerHTML=`<div class="report-section-label">01 ／ 因果鏈</div>${onset?`<p class="report-note">起點：${esc(onset.node)} · ${esc(onset.t)} 秒 · ${esc(onset.signal)}</p>`:''}${propagation.length?`<ol class="report-chain">${propagation.map(n=>`<li>${nodeLink(n.node)}<span>${esc(n.t)} 秒 · ${esc(({sat:'飽和度偏離',err:'錯誤率偏離',p95:'延遲偏離',up:'存活狀態偏離'})[n.signal]||n.signal)}</span></li>`).join('')}</ol><p class="report-note">相對於事故偵測時間；依 Agent 報告呈現。</p>`:'<p class="report-note">尚未提供傳播路徑。</p>'}<div class="report-section-label">02 ／ 處理建議</div><p>${esc(i?.proposal?.description_zh||'尚未提出修復建議。')}</p>${i?.proposal?.verify_zh?`<p class="report-note">驗證方式：${esc(i.proposal.verify_zh)}</p>`:''}<div class="report-section-label">03 ／ 日誌與量測證據 <span>${evidence.length} 筆</span></div>${evidence.map((e,index)=>`<details class="report-evidence"><summary><span>${String(index+1).padStart(2,'0')}</span> ${esc(e.summary_zh||e.summary||e.id)}</summary><p class="report-note">${esc(e.id)} · ${esc(e.tool||e.source||'來源未提供')}</p><pre class="raw">${esc(JSON.stringify(e,null,2))}</pre></details>`).join('')||'<p class="report-note">尚未記錄證據。</p>'}`;
 bindInvestigationNodes();
}
function switchInvestigation(report){$('journal-panel').hidden=report;$('investigation-report').hidden=!report;$('show-report').setAttribute('aria-pressed',String(report));$('show-journal').setAttribute('aria-pressed',String(!report));}
$('show-report').onclick=()=>switchInvestigation(true);$('show-journal').onclick=()=>switchInvestigation(false);
document.querySelectorAll('[data-event-source]').forEach(button=>button.onclick=()=>{journalFilter=button.dataset.eventSource;document.querySelectorAll('[data-event-source]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));renderInvestigation();});
