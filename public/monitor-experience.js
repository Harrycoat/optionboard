/* Presentation layer only. Reads existing Schwab monitor data; never places orders. */
(function(){
'use strict';
const $=id=>document.getElementById(id);
const cache=new Map();
let selected='',planMode='breakout',lastData=null;
const number=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?Number(v):null;
const cash=(v,d=2)=>number(v)===null?'—':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});
const esc=v=>String(v===null||v===undefined?'':v).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const n=v=>number(v)===null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:2});
const validTimestamp=v=>{if(!v)return '기준 시각 미확인';const d=new Date(typeof v==='number'&&v<1e11?v*1000:v);return Number.isNaN(d.getTime())?'기준 시각 미확인':d.toLocaleString('ko-KR',{hour:'2-digit',minute:'2-digit',month:'numeric',day:'numeric'})};
const condition=x=>{
 if(!x)return '데이터 확인 중';
 if(x.state==='BREAKOUT')return 'Call Wall 상단 — 돌파 후 지지 여부 관찰';
 if(['CALL REJECTION','HEDGE READY','HULL21 BREAK'].includes(x.state))return 'Call Wall 저항 반응 — 5분봉 확인';
 if(x.state==='HEDGE WATCH')return '저항선 접근 — HULL21 방향 관찰';
 if(x.state==='ADD WATCH')return 'Put Wall 부근 — 반등 조건 관찰';
 if(x.state==='QUOTE ONLY')return 'Schwab 가격 확인 · 5분봉 데이터 대기';
 return 'Wall 위치와 5분봉 추세 관찰';
};
function select(t){
 if(!t)return;
 selected=t;renderRadar();renderStory();renderPlan();
 const node=$('tickerStory');if(node)node.scrollIntoView({behavior:'smooth',block:'start'});
}
function renderRadar(){
 const list=typeof getList==='function'?getList().slice(0,3):[...cache.keys()].slice(0,3);
 $('radarCount').textContent=list.length+'개 빠른 보기';
 $('radarList').innerHTML=list.length?list.map(t=>{
   const x=cache.get(t);
   return '<button class="ex-radar-button '+(selected===t?'selected':'')+'" type="button" data-radar="'+esc(t)+'"><span class="ex-radar-ticker">'+esc(t)+'</span><span class="ex-radar-summary">'+esc(condition(x))+'</span><span class="ex-radar-price">'+(x?cash(x.spot):'조회 중')+'</span></button>';
 }).join(''):'<div class="ex-muted">모니터할 종목을 추가해 주세요.</div>';
 $('radarNote').textContent='관심종목 순서대로 최대 3개 표시 · 자동 추천/순위 아님 · 가격 기준시각은 종목 상세 참고';
}
function narrative(x){
 if(!x)return '종목을 선택하면 가격과 Wall 위치를 기반으로 확인할 조건을 표시합니다.';
 if(number(x.spot)===null)return 'Schwab 가격을 확인할 수 없습니다.';
 if(number(x.call)!==null&&x.spot>=x.call)return '현재가가 Call Wall 위에 있습니다. 돌파한 가격대에서 5분봉이 유지되는지, 다시 저항선 아래로 밀리지 않는지 확인할 구간입니다.';
 if(number(x.call)!==null&&x.spot>=x.call*.985)return '현재가가 Call Wall에 접근하고 있습니다. 저항 반응과 거래량을 동반한 돌파를 구분하고, 돌파 후 기존 저항선이 지지선이 되는지 살펴봅니다.';
 if(number(x.put)!==null&&x.spot<=x.put)return '현재가가 Put Wall 아래에 있습니다. 지지선 회복 여부와 HULL21 방향, 거래량 변화를 함께 관찰할 구간입니다.';
 if(number(x.put)!==null&&x.spot<=x.put*1.02)return '현재가가 Put Wall 부근입니다. 가격이 지지 구간을 유지하는지, 매도 압력이 둔화되는지와 HULL21의 방향 변화를 확인합니다.';
 return '현재가가 주요 Wall 사이에 있습니다. 가까운 지지·저항 구간과 HULL21의 방향을 함께 확인합니다.';
}
function chart(bars){
 const svg=$('storyChart');
 const closes=(bars||[]).slice(-48).map(v=>number(v.close)).filter(v=>v!==null);
 if(closes.length<2){svg.innerHTML='<text x="18" y="75" fill="#748991" font-size="12">5분봉 데이터 확인 중</text>';return}
 const lo=Math.min(...closes),hi=Math.max(...closes),range=hi-lo||1;
 const points=closes.map((v,i)=>[12+i* (516/Math.max(1,closes.length-1)),127-(v-lo)/range*106]);
 const path=points.map(([x,y],i)=>(i?'L':'M')+x.toFixed(2)+' '+y.toFixed(2)).join(' ');
 svg.innerHTML='<path d="'+path+'" fill="none" stroke="#0b806c" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/><text x="14" y="145" fill="#71858c" font-size="10">Schwab 정규장 5분봉 · 최근 '+closes.length+'개</text>';
}
function renderStory(){
 const x=cache.get(selected);lastData=x||null;
 $('storyTicker').textContent=selected||'종목을 선택하세요';
 $('storyPrice').textContent=x?cash(x.spot):'—';
 $('storyBadge').textContent=condition(x);
 $('storyNarrative').textContent=narrative(x);
 $('storyPut').textContent=x?cash(x.put,1):'—';
 $('storyCall').textContent=x?cash(x.call,1):'—';
 $('storyHull').textContent=x&&number(x.hull)!==null?n(x.hull)+(x.slope>0.03?' ▲':x.slope<-.03?' ▼':' →'):'—';
 $('storyVolume').textContent=x?x.vol.label:'—';
 $('storyQuoteTime').textContent=x?(x.quoteRealtime?'Schwab 실시간 표기 · ':'Schwab 실시간 여부 미확인 · ')+validTimestamp(x.quoteTime):'시세 기준시각 확인 중';
 $('storyWallTime').textContent='옵션 Wall: 별도 API · 기준 시각 표시 기능 준비 중';
 $('storyNews').textContent='뉴스·공시 원문은 아직 이 모니터에 연결되지 않았습니다. 움직이는 이유는 확인된 출처가 있을 때만 표시합니다.';
 $('storyStructure').textContent=x?'Put '+cash(x.put,1)+' / 현재 '+cash(x.spot)+' / Call '+cash(x.call,1):'Wall 데이터 확인 중';
 $('storyFlow').textContent=x?'HULL21 '+$('storyHull').textContent+' · 5분 거래량 '+x.vol.label:'Schwab 5분봉 확인 중';
 chart(x&&x.bars);
 $('storyTracker').href='/trade-tracker.html?ticker='+encodeURIComponent(selected||'NVDA');
}
function renderPlan(){
 const x=cache.get(selected),call=x?number(x.call):null,put=x?number(x.put):null;
 document.querySelectorAll('[data-plan-mode]').forEach(b=>b.classList.toggle('active',b.dataset.planMode===planMode));
 let title='',body='',invalid='';
 if(planMode==='breakout'){
 title='돌파 조건 확인';body='Call Wall '+(call===null?'(데이터 대기)':cash(call,1))+' 상단에서 5분봉 유지와 거래량 동반 여부를 확인합니다. 돌파 직후가 아니라 재확인 구간을 관찰합니다.';
 invalid='돌파 가격 아래로 복귀하면 해당 시나리오를 다시 검토합니다.';
 }else if(planMode==='pullback'){
 title='눌림 조건 확인';body='Put Wall '+(put===null?'(데이터 대기)':cash(put,1))+' 근처에서 지지 유지, 매도 압력 둔화와 HULL21 방향을 확인합니다.';
 invalid='지지 구간 아래에서 유지되면 눌림 시나리오를 다시 검토합니다.';
 }else{
 title='관망 조건';body='가격·Wall 기준 시각을 확인할 수 없거나 거래량·5분봉 조건이 불분명할 때는 다음 확인 조건까지 기다립니다.';
 invalid='명확한 무효화 가격이 없으면 거래 계획을 저장하지 않습니다.';
 }
 $('planTitle').textContent=title;$('planBody').textContent=body;$('planInvalid').textContent=invalid;
 $('planTicker').textContent=selected||'종목 미선택';
 calc();
}
function calc(){
 const account=number($('riskAccount').value),risk=number($('riskPercent').value),entry=number($('riskEntry').value),stop=number($('riskStop').value);
 const valid=account!==null&&account>0&&risk!==null&&risk>0&&risk<=100&&entry!==null&&entry>0&&stop!==null&&stop>0&&stop<entry;
 $('riskResult').textContent=valid?Math.floor(account*risk/100/(entry-stop)).toLocaleString('en-US')+'주':'—';
 $('riskLoss').textContent=valid?cash(Math.floor(account*risk/100/(entry-stop))*(entry-stop)):'값을 입력하면 계산됩니다';
 $('copyScenario').disabled=!valid||!selected;
 $('riskError').textContent=entry!==null&&stop!==null&&stop>=entry?'손절가는 진입가보다 낮아야 합니다.':(!valid?'계좌·위험 한도·진입가·손절가를 직접 입력하세요.':'수수료·슬리피지·갭 리스크는 별도입니다.');
}
document.addEventListener('click',e=>{
 const b=e.target.closest('[data-radar],[data-select],[data-plan-mode]');
 if(!b)return;
 if(b.dataset.radar)select(b.dataset.radar);
 if(b.dataset.select)select(b.dataset.select);
 if(b.dataset.planMode){planMode=b.dataset.planMode;renderPlan()}
});
['riskAccount','riskPercent','riskEntry','riskStop'].forEach(id=>$(id).addEventListener('input',calc));
$('copyScenario').addEventListener('click',async()=>{
 const text=[selected+' 개인 거래 시나리오', $('planTitle').textContent,$('planBody').textContent,$('planInvalid').textContent,
 '계좌 '+$('riskAccount').value+', 위험 '+$('riskPercent').value+'%, 진입 '+$('riskEntry').value+', 손절 '+$('riskStop').value,
 '계산 수량 '+$('riskResult').textContent+', 예상 손실 '+$('riskLoss').textContent,'정보 제공·개인 기록용. 수수료·슬리피지·갭 위험 미반영.'].join('\n');
 try{await navigator.clipboard.writeText(text);$('copyScenario').textContent='✓ 복사됨';setTimeout(()=>$('copyScenario').textContent='시나리오 요약 복사',1400)}
 catch(_){$('riskError').textContent='클립보드 접근이 차단되었습니다. 브라우저 권한을 확인해 주세요.'}
});
window.addEventListener('gex-monitor-update',e=>{
 const x=e.detail;if(!x||!x.ticker)return;
 cache.set(x.ticker,x);
 const displayed=typeof getList==='function'?getList():[];
 if(!selected||!displayed.includes(selected))selected=displayed[0]||x.ticker;
 renderRadar();if(selected===x.ticker){renderStory();renderPlan()}
});
window.addEventListener('gex-monitor-list-change',()=>{
 const list=typeof getList==='function'?getList():[];
 if(!list.includes(selected))selected=list[0]||'';
 renderRadar();renderStory();renderPlan();
});
window.addEventListener('load',()=>{const list=typeof getList==='function'?getList():[];selected=list[0]||'';renderRadar();renderStory();renderPlan()});
})();
