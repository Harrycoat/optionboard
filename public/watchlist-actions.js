(function(){
  'use strict';
  const BASE_KEY='gexoption_watchlist_v1';
  const MONITOR_KEY='gexoption_monitor_list_v1';
  const USER_KEY='gexoption_active_watchlist_user';
  const LIMIT=20;
  const MONITOR_LIMIT=10;
  const clean=value=>String(value||'').trim().toUpperCase().replace(/[^A-Z0-9.\-]/g,'').slice(0,12);
  const parse=value=>{try{return JSON.parse(value||'[]')}catch(_){return[]}};
  function userId(){return localStorage.getItem(USER_KEY)||''}
  function scopedKey(){const id=userId();return id?`${BASE_KEY}_${id}`:''}
  function monitorScopedKey(){const id=userId();return id?`${MONITOR_KEY}_${id}`:''}
  function list(){const key=scopedKey();return key?parse(localStorage.getItem(key)).map(clean).filter(Boolean):[]}
  function monitorList(){const key=monitorScopedKey();return key?parse(localStorage.getItem(key)).map(clean).filter(Boolean).slice(0,MONITOR_LIMIT):[]}
  function setMonitor(tickers){
    const key=monitorScopedKey();
    if(!key)return false;
    const next=[...new Set((tickers||[]).map(clean).filter(Boolean))].slice(0,MONITOR_LIMIT);
    localStorage.setItem(key,JSON.stringify(next));
    window.dispatchEvent(new CustomEvent('gex-monitor-list-change',{detail:{list:next}}));
    return next;
  }
  function addMonitor(ticker){
    ticker=clean(ticker); const key=monitorScopedKey(); if(!key)return false;
    const current=monitorList(); if(!ticker)return false;
    if(current.includes(ticker))return true;
    if(current.length>=MONITOR_LIMIT){alert(`LIVE MONITOR는 최대 ${MONITOR_LIMIT}개까지 표시할 수 있습니다.`);return false}
    setMonitor([...current,ticker]); return true;
  }
  function removeMonitor(ticker){ ticker=clean(ticker); return setMonitor(monitorList().filter(item=>item!==ticker)); }
  function refresh(root=document){
    const saved=new Set(list());
    root.querySelectorAll('[data-watch-add]').forEach(button=>{
      const ticker=clean(button.dataset.watchAdd),active=saved.has(ticker);
      button.classList.add('gex-watch-add');
      button.classList.toggle('is-saved',active);
      button.textContent=active?'★ 관심종목':'☆ 관심추가';
      button.setAttribute('aria-label',active?`${ticker} 관심종목에 저장됨`:`${ticker} 관심종목 추가`);
    });
  }
  function add(ticker){
    ticker=clean(ticker);
    const key=scopedKey();
    if(!key){alert('관심종목을 저장하려면 먼저 로그인해 주세요.');location.href='/?view=watchlist#interestSection';return false}
    const current=list();
    if(current.includes(ticker)){refresh();return true}
    if(current.length>=LIMIT){alert(`관심종목은 최대 ${LIMIT}개까지 저장할 수 있습니다.`);return false}
    const next=[...current,ticker];
    localStorage.setItem(key,JSON.stringify(next));
    // 메인 화면이 기존 클라우드 관심종목과 합쳐 저장할 수 있도록 이관 목록에도 기록합니다.
    const legacy=parse(localStorage.getItem(BASE_KEY)).map(clean).filter(Boolean);
    localStorage.setItem(BASE_KEY,JSON.stringify([...new Set([...legacy,ticker])]));
    window.dispatchEvent(new CustomEvent('gex-watchlist-change',{detail:{ticker,list:next}}));
    refresh();
    return true;
  }
  function remove(ticker){
    ticker=clean(ticker);
    const key=scopedKey();
    if(!key)return false;
    const next=list().filter(item=>item!==ticker);
    localStorage.setItem(key,JSON.stringify(next));
    const legacy=parse(localStorage.getItem(BASE_KEY)).map(clean).filter(Boolean).filter(item=>item!==ticker);
    localStorage.setItem(BASE_KEY,JSON.stringify(legacy));
    window.dispatchEvent(new CustomEvent('gex-watchlist-change',{detail:{ticker,list:next,removed:true}}));
    refresh();
    return true;
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-watch-add]');
    if(!button)return;
    event.preventDefault();event.stopPropagation();
    add(button.dataset.watchAdd);
  },true);
  window.addEventListener('storage',()=>refresh());
  window.addEventListener('gex-watchlist-change',()=>refresh());
  document.addEventListener('DOMContentLoaded',()=>refresh());
  new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===1)refresh(node)}))).observe(document.documentElement,{childList:true,subtree:true});
  const style=document.createElement('style');
  style.textContent='.gex-watch-add{display:inline-flex;align-items:center;justify-content:center;min-height:30px;padding:6px 9px;border:1px solid #e0a838;border-radius:6px;background:rgba(224,168,56,.08);color:#f2bd4c;font:800 11px Arial,"Noto Sans KR",sans-serif;white-space:nowrap;cursor:pointer}.gex-watch-add:hover{background:rgba(224,168,56,.18)}.gex-watch-add.is-saved{border-color:#55e6b5;background:rgba(85,230,181,.1);color:#55e6b5}';
  document.head.appendChild(style);
  window.GexWatchlist={add,remove,list,refresh,monitorList,setMonitor,addMonitor,removeMonitor};
})();
