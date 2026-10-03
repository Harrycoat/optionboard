const MAX=20,KEY='gex.positions.v2';
const QUOTE_MS=10000,WALL_MS=300000;
const $=s=>document.querySelector(s),cards=$('#cards'),empty=$('#empty'),dialog=$('#positionDialog'),form=$('#positionForm');
let positions=load(),quotes={},walls={},lastQuoteRefresh=0,lastWallRefresh=0,refreshing=false;

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
function money(n){return Number.isFinite(n)?'$'+n.toFixed(2):'—'}
function dpct(v){return Number.isFinite(v)?v.toFixed(2)+'%':'—'}
function nowLabel(){return new Date().toLocaleTimeString([], {hour:'numeric',minute:'2-digit',second:'2-digit'})}

function evaluate(price,w){
  const cw=Number(w?.call_wall),pw=Number(w?.put_wall);
  if(!Number.isFinite(cw)||!Number.isFinite(pw)||!Number.isFinite(price)) return {code:'WAIT',label:'⚪ DATA WAIT',reason:'실시간 시세 또는 Wall 연결을 기다리는 중입니다.'};
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
    if(d.error) throw new Error('auth');
    const price=Number(d.last??d.mark??d.ask??d.bid);
    if(!Number.isFinite(price))throw new Error('price');
    return {...d,price,fetchedAt:Date.now()};
  }catch{return{error:'SCHWAB_REAUTH_REQUIRED',fetchedAt:Date.now()}}
}
async function fetchWall(ticker){
  try{
    const r=await fetch('/api/call_wall_monitor?ticker='+encodeURIComponent(ticker),{cache:'no-store'});
    const d=await r.json();
    if(d.error) throw new Error(d.error);
    const cw=Number(d.call_wall),pw=Number(d.put_wall);
    if(!Number.isFinite(cw)||!Number.isFinite(pw)) throw new Error('wall unavailable');
    return {...d,fetchedAt:Date.now()};
  }catch(e){return{error:String(e),fetchedAt:Date.now()}}
}

async function refresh({forceWalls=false}={}){
  if(refreshing)return;
  refreshing=true;
  $('#refreshBtn').textContent='갱신중…';
  try{
    if(!positions.length){render();return}
    const tickers=[...new Set(positions.map(p=>p.ticker))];
    const needWalls=forceWalls||!lastWallRefresh||(Date.now()-lastWallRefresh>=WALL_MS);
    const quoteRows=await Promise.all(tickers.map(async t=>[t,await fetchQuote(t)]));
    for(const [t,q] of quoteRows)quotes[t]=q;
    lastQuoteRefresh=Date.now();
    if(needWalls){
      const wallRows=await Promise.all(tickers.map(async t=>[t,await fetchWall(t)]));
      for(const [t,w] of wallRows)walls[t]=w;
      lastWallRefresh=Date.now();
    }
    render();
  }finally{
    refreshing=false;
    $('#refreshBtn').textContent='새로고침';
  }
}

function wallSignal(distance,side){
  if(!Number.isFinite(distance)) return '대기';
  if(distance<=1) return side==='call'?'CC 검토':'CSP 검토';
  if(distance<=2) return 'WATCH';
  return '대기';
}
function hedgeText(c){
  if(!Number.isFinite(c)) return '실시간 시세 연결 대기';
  if(c<=1) return '콜월 1% 근접 · 헤지 준비';
  if(c<=2) return '콜월 접근 · 헤지 관찰';
  return '헤지 대기';
}

function render(){
  cards.innerHTML='';
  $('#count').textContent=`${positions.length} / ${MAX}`;
  let actions=0,liveCount=0,wallCount=0;
  positions.forEach((p,i)=>{
    const q=quotes[p.ticker]||{},w=walls[p.ticker]||{};
    const live=Number.isFinite(q.price),price=live?q.price:NaN;
    if(live)liveCount++;
    const cw=Number(w.call_wall),pw=Number(w.put_wall);
    if(Number.isFinite(cw)&&Number.isFinite(pw))wallCount++;
    const c=dist(price,cw),u=dist(price,pw);
    const callSig=wallSignal(c,'call'),putSig=wallSignal(u,'put');
    if(callSig!=='대기'||putSig!=='대기')actions++;
    const el=document.createElement('article');el.className='card signal-card';
    el.innerHTML=`
      <div class="signal-head">
        <div>
          <div class="signal-ticker">${p.ticker}</div>
          <div class="muted">${p.shares}주 @ $${p.avgPrice.toFixed(2)}</div>
        </div>
        <div class="signal-price">
          <div class="price">${money(price)}</div>
          <div class="live-badge ${live?'':'offline'}">${live?'Schwab 실시간':'재인증 대기'}</div>
        </div>
      </div>

      <div class="wall-row">
        <div class="wall-label">CALL WALL</div>
        <div class="wall-value">${money(cw)}</div>
        <div class="wall-distance">${dpct(c)}</div>
        <div class="wall-signal">${callSig}</div>
      </div>

      <div class="wall-row">
        <div class="wall-label">PUT WALL</div>
        <div class="wall-value">${money(pw)}</div>
        <div class="wall-distance">${dpct(u)}</div>
        <div class="wall-signal">${putSig}</div>
      </div>

      <div class="hedge-line">헤지: <strong>${hedgeText(c)}</strong></div>

      <div class="simple-actions">
        <button class="edit" data-edit="${i}">수정</button>
        <button class="delete" data-i="${i}">삭제</button>
      </div>`;
    cards.appendChild(el);
  });
  $('#actionCount').textContent=actions;
  $('#wallStatus').textContent=positions.length?`${wallCount}/${positions.length} 자동`:'자동';
  const dot=$('#sourceDot'),mode=$('#mode');
  if(positions.length&&liveCount===positions.length){
    dot.classList.remove('warn');mode.textContent='Schwab 실시간 + GEXOption Wall 자동';
  }else{
    dot.classList.add('warn');mode.textContent='Schwab 재인증 대기 · GEXOption Wall 자동';
  }
  $('#updatedAt').textContent='마지막 갱신 '+(lastQuoteRefresh?nowLabel():'—')+' · Wall 5분 주기';
  empty.style.display=positions.length?'none':'flex';

  document.querySelectorAll('.delete').forEach(b=>b.onclick=()=>{
    const i=+b.dataset.i;if(confirm(positions[i].ticker+' 포지션을 삭제할까요?')){positions.splice(i,1);save();render()}
  });
  document.querySelectorAll('.edit').forEach(b=>b.onclick=()=>openEdit(+b.dataset.edit));
}

function openAdd(){
  if(positions.length>=MAX)return alert('최대 20개까지 등록할 수 있습니다.');
  form.reset();$('#editIndex').value='';$('#sheetTitle').textContent='포지션 추가';$('#ticker').disabled=false;dialog.showModal()
}
function openEdit(i){
  const p=positions[i];if(!p)return;
  $('#editIndex').value=String(i);$('#sheetTitle').textContent=p.ticker+' 포지션 수정';
  $('#ticker').value=p.ticker;$('#ticker').disabled=true;$('#shares').value=p.shares;$('#avgPrice').value=p.avgPrice;
  $('#hasCC').checked=!!p.hasCC;$('#hasPut').checked=!!p.hasPut;$('#hasCSP').checked=!!p.hasCSP;dialog.showModal()
}

$('#addBtn').onclick=openAdd;$('#fabBtn').onclick=openAdd;$('#cancelBtn').onclick=()=>dialog.close();
$('#refreshBtn').onclick=()=>refresh({forceWalls:true});
form.onsubmit=async e=>{
  e.preventDefault();
  const editIndex=$('#editIndex').value;
  const ticker=$('#ticker').value.trim().toUpperCase();
  if(!/^[A-Z][A-Z.\-]{0,9}$/.test(ticker))return alert('Ticker를 확인하세요.');
  const row={ticker,shares:+$('#shares').value,avgPrice:+$('#avgPrice').value,hasCC:$('#hasCC').checked,hasPut:$('#hasPut').checked,hasCSP:$('#hasCSP').checked,createdAt:Date.now()};
  if(editIndex!==''){
    const i=+editIndex;row.createdAt=positions[i].createdAt||Date.now();positions[i]=row;
  }else{
    if(positions.some(p=>p.ticker===ticker))return alert('이미 등록된 Ticker입니다.');
    positions.push(row);
  }
  save();dialog.close();await refresh({forceWalls:true});
};

if('serviceWorker'in navigator)navigator.serviceWorker.register('/position-manager/sw.js?v=4').catch(()=>{});
render();refresh({forceWalls:true});
setInterval(()=>refresh(),QUOTE_MS);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
