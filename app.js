'use strict';
const $ = id => document.getElementById(id);
const esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = x => (x===null || x===undefined || x==='') ? '—' : '$'+Number(x).toFixed(2);
const num = x => (x===null || x===undefined) ? '—' : Number(x).toLocaleString('en-US');
const timestamp = x => { if (!x) return 'No scan yet'; const d=new Date(x); return isNaN(+d)?'—':d.toLocaleString('vi-VN',{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit'}); };
const safeLink = url => /^https:\/\/polymarket\.com\/(event|market)\/[\w-]+$/.test(String(url||'')) ? url : null;
const n4 = x => x===undefined||x===null||x==='' ? '—' : Number(x).toFixed(4);
let lastState = null, opps = [], rewards = [], history = [], currentView='overview', loading=false;
async function api(endpoint,options={}) {const r=await fetch(endpoint,{...options,cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status+' '+endpoint);return r.json();}
function navigate(name) {
  currentView=name;
  document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('selected',el.dataset.view===name));
  document.querySelectorAll('[data-panel]').forEach(el=>el.classList.toggle('hidden',el.dataset.panel!==name));
  const data={overview:['Market intelligence','Observing real order books, not predicting guaranteed profits.'],opportunities:['Opportunity scanner','YES / NO paired order book depth, fees and simulated budgets.'],rewards:['Liquidity reward pools','Public allocation metadata, not user rewards.'],history:['Research history','Persisted observations from the online research worker.']};
  $('pageTitle').innerHTML=esc(data[name][0])+'<span class="title-dot">.</span>';
  $('pageSub').textContent=data[name][1];
  $('crumbView').textContent=name.toUpperCase();
  if(name==='opportunities')renderTable();
  if(name==='rewards')renderRewards();
  if(name==='history')renderHistory();
  window.scrollTo({top:0,behavior:'smooth'});
}
function statusClass(x){return x==='PAPER_POSITIVE'?'positive':x==='PAPER_NEGATIVE'?'negative':'no';}
function rowsHtml(rows){return `<table class="data-table"><thead><tr><th>MARKET</th><th>CAPITAL</th><th>YES / NO ASK AVG</th><th>PAIRED SHARES</th><th>FEES</th><th>PAPER PNL</th><th>EVALUATION</th></tr></thead><tbody>${rows.map(r=>{
  const url=safeLink(r.url),link=url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(r.question||r.market_id)} ↗</a>`:`<span>${esc(r.question||r.market_id)}</span>`;
  const pnl=r.pnl!==undefined?Number(r.pnl):null;
  return `<tr><td class="market-cell">${link}<small>Market ${esc(r.market_id)} · ${esc(r.version)}</small></td><td>${money(r.capital)}</td><td>${n4(r.avg_yes)} / ${n4(r.avg_no)}</td><td>${r.shares===undefined?'—':esc(r.shares)}</td><td>${money(r.taker_fees)}</td><td class="${pnl===null?'num-muted':pnl>0?'num-positive':'num-negative'}">${money(pnl)}</td><td><span class="tag ${statusClass(r.status)}">${esc(String(r.status||'').replace('PAPER_',''))}</span></td></tr>`;
}).join('')}</tbody></table>`;}
function tableOrBlank(rows,msg){return rows.length?rowsHtml(rows):`<div class="table-empty">${esc(msg)}</div>`;}
function renderTable(){
  const search=$('search').value.trim().toLowerCase(),cap=$('budget').value,status=$('filterStatus').value,min=Number($('minEdge').value||0);
  let rows=opps.filter(r=>(!search||(String(r.question||'')+' '+String(r.market_id||'')).toLowerCase().includes(search))&&(!cap||String(r.capital)===cap)&&(!status||r.status===status)&&(min<=0||r.pnl!==undefined&&Number(r.pnl)>=min));
  $('resultsCount').textContent=rows.length.toLocaleString('vi-VN');
  $('fullTable').innerHTML=tableOrBlank(rows.slice(0,500),lastState?.last_success_utc?'No evaluations match these filters.':'Waiting for first valid scan.');
  const top=opps.filter(r=>r.status==='PAPER_POSITIVE').slice(0,5);
  $('overviewTable').innerHTML=tableOrBlank(top,lastState?.last_success_utc?'No paper-positive observations in the latest scan.':'Waiting for first valid scan.');
}
function renderRewards(){
  $('rewardSummary').textContent=`${rewards.length} allocation records · market-wide pool amounts (USDC/day)`;
  if(!rewards.length){$('rewardTable').innerHTML=`<div class="table-empty">${esc(lastState?.reward_error||'No currently eligible reward pools found in the scanned sample.')}</div>`;return;}
  $('rewardTable').innerHTML=`<table class="data-table"><thead><tr><th>MARKET</th><th>DAILY POOL</th><th>MIN SIZE</th><th>MAX SPREAD</th><th>END DATE</th></tr></thead><tbody>${rewards.map(r=>{const url=safeLink('https://polymarket.com/event/'+r.slug);return `<tr><td class="market-cell">${url?`<a href="${esc(url)}" rel="noreferrer" target="_blank">${esc(r.question)} ↗</a>`:esc(r.question)}</td><td class="num-positive">${money(r.pool_daily_usdc)}</td><td>${esc(r.min_qualifying_size||'—')}</td><td>${esc(r.max_qualifying_spread||'—')}</td><td>${esc(r.end_date||'—')}</td></tr>`}).join('')}</tbody></table>`;
}
function renderHistory(){
  $('historyTable').innerHTML=history.length?`<table class="data-table"><thead><tr><th>SCAN ID</th><th>OBSERVED (LOCAL)</th><th>SOURCE</th><th>BOOK PAIRS</th><th>ELIGIBLE MARKETS</th><th>PAPER-POSITIVE ROWS</th></tr></thead><tbody>${history.map(r=>`<tr><td>#${esc(r.scan_id)}</td><td>${esc(timestamp(r.created_utc))}</td><td>${esc(r.mode.toUpperCase())}</td><td>${esc(r.scanned)}</td><td>${esc(r.accepted)}</td><td class="num-positive">${esc(r.paper_positive)}</td></tr>`).join('')}</tbody></table>`:'<div class="table-empty">No persistent scans yet.</div>';
}
function drawChart(){
  if(!history.length){$('activityChart').innerHTML='<div class="chart-empty">Awaiting scan history</div>';return;}
  const data=[...history].reverse().map(x=>Number(x.paper_positive)),w=900,h=188,pad=21,base=153;
  const max=Math.max(2,...data),X=i=>pad+(w-2*pad)*(data.length===1?.5:i/(data.length-1)),Y=v=>base-(base-20)*v/max;
  const line=data.map((v,i)=>`${i?'L':'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ');
  const area=line+` L ${X(data.length-1)} ${base} L ${X(0)} ${base} Z`;
  const grid=[0,.5,1].map(v=>`<line x1="${pad}" y1="${Y(max*v)}" x2="${w-pad}" y2="${Y(max*v)}" stroke="#26374c" stroke-dasharray="3 7"/>`).join('');
  $('activityChart').innerHTML=`<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#42e3b7" stop-opacity=".23"/><stop offset="1" stop-color="#42e3b7" stop-opacity="0"/></linearGradient></defs>${grid}<path d="${area}" fill="url(#fade)"/><path d="${line}" fill="none" stroke="#43ddba" stroke-width="2.5" vector-effect="non-scaling-stroke"/>${data.map((v,i)=>`<circle cx="${X(i)}" cy="${Y(v)}" r="3" fill="#43ddba" opacity="${i===data.length-1?1:.25}"/>`).join('')}<text x="${pad}" y="179" fill="#7086a4" font-size="10">Older scans</text><text x="${w-pad}" y="179" fill="#7086a4" font-size="10" text-anchor="end">Latest scan</text></svg>`;
}
function setState(s){
  lastState=s;
  const live=s.mode==='live',success=Boolean(s.last_success_utc),ok=live&&success&&!s.last_error;
  $('sourceLabel').textContent=live?(ok?'LIVE API / PAPER ANALYSIS':success?'LIVE DATA / LAST GOOD SNAPSHOT':'LIVE / CONNECTING'):'SYNTHETIC DEMO DATA';
  $('sideMode').textContent=live?(ok?'LIVE DATA · PAPER':'LIVE DATA · NO SNAPSHOT'):'DEMO · SYNTHETIC';
  $('mode-dot').className='dot '+(ok?'green':live?'yellow':'yellow');
  $('lastScan').textContent=timestamp(s.last_success_utc);
  $('wsBadge').innerHTML=`<span class="dot ${s.ws_connected?'green':s.ws_enabled?'yellow':'gray'}"></span>${s.ws_enabled?(s.ws_connected?'WS connected':'WS offline'):'WS disabled'}`;
  $('mBooks').textContent=num(s.books_count);
  $('mAccepted').textContent=num(s.accepted_count);
  $('mPositive').textContent=num(s.paper_positive);
  $('mBest').textContent=s.best_paper_pnl===null?'—':money(s.best_paper_pnl);
  $('navSignals').textContent=num(s.paper_positive);
  $('eSource').textContent=live?'Gamma + CLOB REST':'Synthetic fixtures';
  $('eWebsocket').textContent=s.ws_enabled?(s.ws_connected?'CONNECTED':'NOT CONNECTED'):'DISABLED';
  $('eEvents').textContent=num(s.ws_events);
  $('eInterval').textContent=s.interval_seconds+'s (30s min WS)';
  $('eCapitals').textContent=s.capitals.map(c=>'$'+c).join(' / ');
  $('engineState').textContent=s.scanning?'SCANNING':s.last_error?'API ERROR':ok?'ACTIVE':'IDLE';
  const errors=[];
  if(!live)errors.push('<div class="alert demo"><strong>SYNTHETIC DEMO.</strong> All scan results are fabricated for functionality testing. Never treat them as live Polymarket opportunities.</div>');
  if(s.last_error)errors.push(`<div class="alert"><strong>Last scan failed:</strong> ${esc(s.last_error)}. Latest successful snapshot is retained with its timestamp.</div>`);
  if(live&&!success&&!s.last_error)errors.push('<div class="alert info">Connecting to official public Polymarket APIs. No fabricated market data will be shown if the API is unavailable.</div>');
  $('alerts').innerHTML=errors.join('');
}
async function refresh(){
  if(loading)return;
  loading=true;
  try{
    const [s,o,r,h]=await Promise.all([api('/api/state'),api('/api/opportunities?limit=500'),api('/api/rewards'),api('/api/history?limit=30')]);
    setState(s);opps=o.rows||[];rewards=r.rows||[];history=h.rows||[];
    const prev=$('budget').value;
    $('budget').innerHTML='<option value="">All budgets</option>'+(s.capitals||[]).map(c=>`<option value="${esc(c)}">$${esc(c)}</option>`).join('');
    $('budget').value=prev;
    renderTable();renderRewards();renderHistory();drawChart();
  }catch(e){$('alerts').innerHTML=`<div class="alert">Dashboard API unavailable: ${esc(e.message)}. Check the server, access password and network.</div>`;}
  finally{loading=false;}
}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.view)));
document.querySelectorAll('[data-jump]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.jump)));
['search','budget','filterStatus','minEdge'].forEach(id=>$(id).addEventListener(id==='search'||id==='minEdge'?'input':'change',renderTable));
$('refreshBtn').addEventListener('click',async()=>{const btn=$('refreshBtn');btn.disabled=true;try{await api('/api/scan',{method:'POST',headers:{'X-Scanner-Action':'manual-refresh'}});btn.innerHTML='✓ Queued';await refresh();}catch(e){$('alerts').innerHTML=`<div class="alert">${esc(e.message)}</div>`;}setTimeout(()=>{btn.disabled=false;btn.innerHTML='↻ <span>Refresh</span>';},1600);});
refresh();setInterval(refresh,5000);
