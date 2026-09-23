/* ETF representative-tracker comparison; do not represent fixed maps as live ETF holdings. */
(function(){
'use strict';
const $=id=>document.getElementById(id);
const val=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?Number(v):null;
const pct=v=>(v>=0?'+':'')+Number(v).toFixed(2)+'%';
const money=v=>val(v)===null?'—':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:2});
const timeString=v=>{const ms=typeof v==='number'&&v<1e11?v*1000:v;const d=new Date(ms);return Number.isNaN(d.getTime())?'시각 미확인':d.toLocaleString('ko-KR')};
const ageMinutes=v=>{if(!v)return Infinity;const ms=typeof v==='number'&&v<1e11?v*1000:typeof v==='string'&&/^\\d{13}$/.test(v)?Number(v):Date.parse(v);return Number.isFinite(ms)?(Date.now()-ms)/60000:Infinity};
function quoteTime(q){return q.quote_time||q.trade_time}
function usable(q){const t=ageMinutes(quoteTime(q));return val(q.last)!==null&&val(q.close)!==null&&q.close>0&&t>=-2&&t<20}
function movement(q){return (Number(q.last)-Number(q.close))/Number(q.close)*100}
const element=(tag,cls,txt)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e};
const removeChildren=(node,child)=>node.replaceChildren(child);
async function getQuotes(symbols){
 const batches=[];for(let i=0;i<symbols.length;i+=5)batches.push(symbols.slice(i,i+5));
 const result={};let failed=0;
 // Bound simultaneous auth/quote requests and never replace unavailable quotes with stale defaults.
 for(let i=0;i<batches.length;i+=3){
  const chunk=await Promise.all(batches.slice(i,i+3).map(async batch=>{
   try{const response=await fetch('/api/search?mode=schwab_quote&symbols='+encodeURIComponent(batch.join(',')),{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json();if(data.error)throw Error(data.error);return data.quotes||{}}catch(_){failed++;return {}}
  }));
  for(const quotes of chunk)Object.assign(result,quotes);
 }
 return {quotes:result,failed};
}
function compare(quotes){
 const etfs=new Map(ETF_LIST.filter(etf=>usable(quotes[etf.ticker])).map(etf=>[etf.ticker,movement(quotes[etf.ticker])]));
 const stocks=new Map();
 for(const etf of ETF_LIST){
  const change=etfs.get(etf.ticker);if(change===undefined)continue;
  for(const symbol of etf.leaders){
   const q=quotes[symbol];if(!usable(q))continue;
   const move=movement(q),excess=move-change;
   if(move<=0||excess<0.5)continue;
   const r=stocks.get(symbol)||{ticker:symbol,quote:q,move,etfs:[],groups:new Set(),maxExcess:-Infinity};
   r.etfs.push({ticker:etf.ticker,excess,etfChange:change});
   r.groups.add(etf.group);r.maxExcess=Math.max(r.maxExcess,excess);stocks.set(symbol,r);
  }
 }
 return [...stocks.values()].sort((a,b)=>b.etfs.length-a.etfs.length||b.maxExcess-a.maxExcess||b.move-a.move).slice(0,6);
}
function card(row){
 const card=element('article','standout-card'),head=element('div','standout-title');
 head.append(element('b','',row.ticker),element('span','standout-change',pct(row.move)));card.append(head);
 card.append(element('div','standout-stat','현재가 '+money(row.quote.last)+' · 누적 거래량 '+(val(row.quote.volume)===null?'미확인':Number(row.quote.volume).toLocaleString('en-US'))));
 const sorted=[...row.etfs].sort((a,b)=>b.excess-a.excess);
 card.append(element('div','standout-reason',sorted.map(x=>x.ticker+' 대비 +'+x.excess.toFixed(2)+'%p').join(' · ')));
 card.append(element('div','standout-flag','비교 ETF '+row.etfs.length+'개 · 시세 '+timeString(quoteTime(row.quote))));
 const actions=element('div','standout-actions');
 const detail=element('a','','종목 5분봉 →');detail.href='/trade-tracker.html?ticker='+encodeURIComponent(row.ticker);actions.append(detail);
 const monitor=element('a','','Live Monitor →');monitor.href='/monitoring.html';actions.append(monitor);card.append(actions);
 return card;
}
async function run(){
 $('standoutRefresh').disabled=true;$('standoutStatus').textContent='대표 추적종목과 ETF의 최신 Schwab 시세를 확인 중…';
 removeChildren($('standoutBoard'),element('div','standout-empty','데이터를 비교하는 중입니다.')); 
 try{
  const stocks=[...new Set(ETF_LIST.flatMap(x=>x.leaders))],symbols=[...new Set([...stocks,...ETF_LIST.map(x=>x.ticker)])];
  const {quotes,failed}=await getQuotes(symbols);
  const etfCount=ETF_LIST.filter(etf=>usable(quotes[etf.ticker])).length;
  const candidates=compare(quotes);
  $('standoutBoard').replaceChildren();
  if(!candidates.length)$('standoutBoard').append(element('div','standout-empty','기준을 충족하는 종목이 없습니다. ETF·종목 시세가 오래되었거나 비교 조건을 만족하지 못했을 수 있습니다.'));
  else for(const row of candidates)$('standoutBoard').append(card(row));
  $('standoutStatus').textContent='현재 비교 가능 ETF '+etfCount+'/'+ETF_LIST.length+' · 종목 '+candidates.length+'개 표시 · 조회 '+new Date().toLocaleTimeString('ko-KR')+(failed?' · 시세 요청 '+failed+'건 실패':'');
 }catch(_){
  $('standoutStatus').textContent='비교 데이터 조회 오류';
  removeChildren($('standoutBoard'),element('div','standout-empty','시세를 조회하지 못했습니다. 이전 시세를 현재값처럼 표시하지 않습니다. 다시 분석을 눌러주세요.'));
 }finally{$('standoutRefresh').disabled=false}
}
$('standoutRefresh').addEventListener('click',run);run();
})();
