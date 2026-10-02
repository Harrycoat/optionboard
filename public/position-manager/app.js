const MAX=20,KEY='gex.positions.v1';
const $=s=>document.querySelector(s),cards=$('#cards'),empty=$('#empty'),dialog=$('#positionDialog'),form=$('#positionForm');
let positions=load(),quotes={};
function load(){try{return JSON.parse(localStorage.getItem(KEY))||[]}catch{return[]}}
function save(){localStorage.setItem(KEY,JSON.stringify(positions))}
function pct(a,b){return b?((a-b)/b)*100:0}
function dist(price,wall){return wall?Math.abs((wall-price)/price)*100:null}
function evaluate(p,price){
  const c=dist(price,p.callWall),u=dist(price,p.putWall);
  if(price<p.putWall)return{code:'HEDGE',label:'🔴 HEDGE REVIEW',reason:'현재가가 Put Wall 아래입니다. CSP보다 방어 상태를 먼저 확인하세요.'};
  if(c!=null&&c<=1)return{code:'CC',label:'🟠 CC REVIEW',reason:`Call Wall까지 ${c.toFixed(2)}%. CC/헤지 조건을 점검하세요.`};
  if(u!=null&&u<=1)return{code:'CSP',label:'🔵 CSP REVIEW',reason:`Put Wall까지 ${u.toFixed(2)}%. 지지 확인 후 CSP 조건을 점검하세요.`};
  if(c!=null&&c<=2)return{code:'CCW',label:'🟡 CC WATCH',reason:`Call Wall까지 ${c.toFixed(2)}%. 접근 구간입니다.`};
  if(u!=null&&u<=2)return{code:'CSPW',label:'🟡 CSP WATCH',reason:`Put Wall까지 ${u.toFixed(2)}%. 접근 구간입니다.`};
  return{code:'HOLD',label:'🟢 HOLD',reason:'현재 Wall 접근 경고가 없습니다.'}
}
async function fetchQuote(ticker){
  try{
    const r=await fetch('/api/schwab_quote?ticker='+encodeURIComponent(ticker),{cache:'no-store'});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||'quote failed');
    const price=Number(d.mark??d.last??d.ask??d.bid);
    if(!Number.isFinite(price))throw new Error('no price');
    return {...d,price};
  }catch(e){return{error:String(e)}}
}
async function refresh(){
  const rows=await Promise.all(positions.map(async p=>[p.ticker,await fetchQuote(p.ticker)]));
  quotes=Object.fromEntries(rows);render();
}
function render(){
  cards.innerHTML='';$('#count').textContent=`${positions.length} / ${MAX}`;let actions=0;
  positions.forEach((p,i)=>{
    const q=quotes[p.ticker]||{},live=Number.isFinite(q.price),price=live?q.price:p.avgPrice,s=evaluate(p,price);
    if(s.code!=='HOLD')actions++;
    const pnl=(price-p.avgPrice)*p.shares,pp=pct(price,p.avgPrice),c=dist(price,p.callWall),u=dist(price,p.putWall);
    const el=document.createElement('article');el.className='card';
    el.innerHTML=`<div class="row"><div><div class="ticker">${p.ticker}</div><div class="muted">${p.shares}주 @ $${p.avgPrice.toFixed(2)}</div></div><div style="text-align:right"><div class="price">$${price.toFixed(2)}</div><div class="muted">${live?(q.realtime===true?'Schwab real-time':'Schwab'):'시세 오류'}</div></div></div>
    <div class="grid"><div class="metric"><span>P/L</span><strong>${pnl>=0?'+':''}$${pnl.toFixed(0)} (${pp>=0?'+':''}${pp.toFixed(2)}%)</strong></div><div class="metric"><span>Call Wall</span><strong>$${p.callWall.toFixed(2)}</strong></div><div class="metric"><span>Put Wall</span><strong>$${p.putWall.toFixed(2)}</strong></div><div class="metric"><span>Wall Distance</span><strong>C ${c.toFixed(2)}% · P ${u.toFixed(2)}%</strong></div></div>
    <div class="row"><span class="status">${s.label}</span><button class="delete" data-i="${i}">삭제</button></div><div class="reason">${q.error?'⚠️ '+q.error:s.reason}</div>`;
    cards.appendChild(el);
  });
  $('#actionCount').textContent=actions;empty.style.display=positions.length?'none':'block';
  document.querySelectorAll('.delete').forEach(b=>b.onclick=()=>{positions.splice(+b.dataset.i,1);save();refresh()});
}
$('#addBtn').onclick=()=>{if(positions.length>=MAX)return alert('최대 20개까지 등록할 수 있습니다.');form.reset();dialog.showModal()};
$('#cancelBtn').onclick=()=>dialog.close();
form.onsubmit=async e=>{e.preventDefault();const ticker=$('#ticker').value.trim().toUpperCase();
  if(!/^[A-Z][A-Z.\-]{0,9}$/.test(ticker))return alert('Ticker를 확인하세요.');
  if(positions.some(p=>p.ticker===ticker))return alert('이미 등록된 Ticker입니다.');
  positions.push({ticker,shares:+$('#shares').value,avgPrice:+$('#avgPrice').value,callWall:+$('#callWall').value,putWall:+$('#putWall').value,hasCC:$('#hasCC').checked,hasPut:$('#hasPut').checked,hasCSP:$('#hasCSP').checked,createdAt:Date.now()});
  save();dialog.close();await refresh();
};
if('serviceWorker'in navigator)navigator.serviceWorker.register('/position-manager/sw.js').catch(()=>{});
render();refresh();setInterval(refresh,5000);