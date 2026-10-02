const MAX=20,KEY='gex.positions.v2';
const $=s=>document.querySelector(s),cards=$('#cards'),empty=$('#empty'),dialog=$('#positionDialog'),form=$('#positionForm');
let positions=load(),quotes={},walls={};

function load(){
  try{
    const v2=JSON.parse(localStorage.getItem(KEY));
    if(Array.isArray(v2)) return v2;
    const old=JSON.parse(localStorage.getItem('gex.positions.v1'))||[];
    const migrated=old.map(({ticker,shares,avgPrice,hasCC,hasPut,hasCSP,createdAt})=>({ticker,shares,avgPrice,hasCC,hasPut,hasCSP,createdAt}));
    localStorage.setItem(KEY,JSON.stringify(migrated));
    return migrated;
  }catch{return[]}
}
function save(){localStorage.setItem(KEY,JSON.stringify(positions))}
function pct(a,b){return b?((a-b)/b)*100:0}
function dist(price,wall){return price&&wall?Math.abs((wall-price)/price)*100:null}

function evaluate(p,price,w){
  const cw=Number(w?.call_wall),pw=Number(w?.put_wall);
  if(!Number.isFinite(cw)||!Number.isFinite(pw)||!Number.isFinite(price)) return {code:'WAIT',label:'⚪ DATA WAIT',reason:'Wall/실시간 시세를 불러오는 중입니다.'};
  const c=dist(price,cw),u=dist(price,pw);
  if(price<pw)return{code:'HEDGE',label:'🔴 HEDGE REVIEW',reason:'현재가가 Put Wall 아래입니다. CSP보다 방어 상태를 먼저 확인하세요.'};
  if(c<=1)return{code:'CC',label:'🟠 CC REVIEW',reason:`Call Wall까지 ${c.toFixed(2)}%. CC/헤지 조건을 점검하세요.`};
  if(u<=1)return{code:'CSP',label:'🔵 CSP REVIEW',reason:`Put Wall까지 ${u.toFixed(2)}%. 지지 확인 후 CSP 조건을 점검하세요.`};
  if(c<=2)return{code:'CCW',label:'🟡 CC WATCH',reason:`Call Wall까지 ${c.toFixed(2)}%. 접근 구간입니다.`};
  if(u<=2)return{code:'CSPW',label:'🟡 CSP WATCH',reason:`Put Wall까지 ${u.toFixed(2)}%. 접근 구간입니다.`};
  return{code:'HOLD',label:'🟢 HOLD',reason:'현재 Wall 접근 경고가 없습니다.'}
}

async function fetchQuote(ticker){
  try{
    const r=await fetch('/api/search?mode=schwab_quote&ticker='+encodeURIComponent(ticker),{cache:'no-store'});
    const d=await r.json();
    if(d.error) throw new Error(d.detail||d.error);
    const price=Number(d.last??d.mark??d.ask??d.bid);
    if(!Number.isFinite(price))throw new Error('no realtime price');
    return {...d,price};
  }catch(e){return{error:String(e)}}
}
async function fetchWall(ticker){
  try{
    const r=await fetch('/api/call_wall_monitor?ticker='+encodeURIComponent(ticker),{cache:'no-store'});
    const d=await r.json();
    if(d.error) throw new Error(d.error);
    const cw=Number(d.call_wall),pw=Number(d.put_wall);
    if(!Number.isFinite(cw)||!Number.isFinite(pw)) throw new Error('wall unavailable');
    return d;
  }catch(e){return{error:String(e)}}
}
async function refresh(){
  if(!positions.length){render();return}
  const rows=await Promise.all(positions.map(async p=>{
    const [q,w]=await Promise.all([fetchQuote(p.ticker),fetchWall(p.ticker)]);
    return [p.ticker,q,w];
  }));
  for(const [t,q,w] of rows){quotes[t]=q;walls[t]=w}
  render();
}
function money(n){return Number.isFinite(n)?'$'+n.toFixed(2):'—'}
function dpct(v){return Number.isFinite(v)?v.toFixed(2)+'%':'—'}

function render(){
  cards.innerHTML='';$('#count').textContent=`${positions.length} / ${MAX}`;let actions=0;
  positions.forEach((p,i)=>{
    const q=quotes[p.ticker]||{},w=walls[p.ticker]||{};
    const live=Number.isFinite(q.price),fallback=Number(w.spot),price=live?q.price:(Number.isFinite(fallback)?fallback:NaN);
    const s=evaluate(p,price,w); if(!['HOLD','WAIT'].includes(s.code)) actions++;
    const pnl=Number.isFinite(price)?(price-p.avgPrice)*p.shares:NaN,pp=Number.isFinite(price)?pct(price,p.avgPrice):NaN;
    const cw=Number(w.call_wall),pw=Number(w.put_wall),c=dist(price,cw),u=dist(price,pw);
    const el=document.createElement('article');el.className='card';
    el.innerHTML=`
      <div class="row">
        <div><div class="ticker">${p.ticker}</div><div class="muted">${p.shares}주 @ $${p.avgPrice.toFixed(2)}</div></div>
        <div style="text-align:right"><div class="price">${money(price)}</div><div class="live-badge">${live?'Schwab 실시간':'시세 연결 확인'}</div></div>
      </div>
      <div class="grid">
        <div class="metric"><span>P/L</span><strong>${Number.isFinite(pnl)?((pnl>=0?'+':'')+'$'+pnl.toFixed(0)+' ('+(pp>=0?'+':'')+pp.toFixed(2)+'%)'):'—'}</strong></div>
        <div class="metric wall-card"><span>CALL WALL</span><strong>${money(cw)}</strong><small class="wall-auto">GEXOption 자동</small></div>
        <div class="metric wall-card"><span>PUT WALL</span><strong>${money(pw)}</strong><small class="wall-auto">GEXOption 자동</small></div>
        <div class="metric"><span>WALL DISTANCE</span><strong>C ${dpct(c)} · P ${dpct(u)}</strong></div>
      </div>
      <div class="row"><span class="status">${s.label}</span><button class="delete" data-i="${i}">삭제</button></div>
      <div class="reason">${s.reason}${w.error?'<br><span class="wall-loading">⚠ Wall: '+w.error+'</span>':''}${q.error?'<br><span class="wall-loading">⚠ 실시간: '+q.error+'</span>':''}</div>`;
    cards.appendChild(el);
  });
  $('#actionCount').textContent=actions;empty.style.display=positions.length?'none':'flex';
  document.querySelectorAll('.delete').forEach(b=>b.onclick=()=>{positions.splice(+b.dataset.i,1);save();refresh()});
}
function openAdd(){if(positions.length>=MAX)return alert('최대 20개까지 등록할 수 있습니다.');form.reset();dialog.showModal()}
$('#addBtn').onclick=openAdd;$('#fabBtn').onclick=openAdd;$('#cancelBtn').onclick=()=>dialog.close();
form.onsubmit=async e=>{
  e.preventDefault();const ticker=$('#ticker').value.trim().toUpperCase();
  if(!/^[A-Z][A-Z.\-]{0,9}$/.test(ticker))return alert('Ticker를 확인하세요.');
  if(positions.some(p=>p.ticker===ticker))return alert('이미 등록된 Ticker입니다.');
  positions.push({ticker,shares:+$('#shares').value,avgPrice:+$('#avgPrice').value,hasCC:$('#hasCC').checked,hasPut:$('#hasPut').checked,hasCSP:$('#hasCSP').checked,createdAt:Date.now()});
  save();dialog.close();await refresh();
};
if('serviceWorker'in navigator)navigator.serviceWorker.register('/position-manager/sw.js?v=3').catch(()=>{});
render();refresh();setInterval(refresh,10000);
