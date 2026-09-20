(function(){
  const items=[
    {href:'/',label:'🏠 홈',match:p=>p==='/'||p==='/index.html'},
    {href:'/ai-leader-roadmap.html#aiLeaderMap',label:'🧭 AI 주도주 로드맵',match:p=>p==='/ai-leader-roadmap.html',tone:'#55e6b5'},
    {href:'/industry-leaders.html',label:'🏭 산업별 주도주',match:p=>p==='/industry-leaders.html',tone:'#81adff'},
    {href:'/momentum-top-100.html',label:'모멘텀 TOP 100',match:p=>p==='/momentum-top-100.html'},
    {href:'/etf-market-leader.html',label:'대형주 ETF',match:p=>p==='/etf-market-leader.html'},
    {href:'/unusual-options.html',label:'거래량 급증주',match:p=>p==='/unusual-options.html'},
    {href:'/today-buy-signal.html',label:'🎯 오늘의 매수 신호',match:p=>p==='/today-buy-signal.html',tone:'#e0a838'},
    {href:'https://gex-future-leaders.harryahn.chatgpt.site/#future-leaders-core',label:'장기보유 운용',external:true,tone:'#55e6b5'},
    {href:'https://gex-future-leaders.harryahn.chatgpt.site/#premium-swing',label:'스윙 트레이딩',external:true,tone:'#e0a838'}
  ];
  const style=document.createElement('style');
  style.textContent=`
    .shared-category-nav{border-bottom:1px solid #252b36;background:#10141b;overflow-x:auto;position:relative;z-index:8}
    .shared-category-inner{max-width:1180px;margin:0 auto;padding:9px 20px;display:flex;align-items:center;gap:6px;white-space:nowrap}
    .shared-category-link{display:inline-flex;align-items:center;gap:4px;padding:8px 10px;border:1px solid #252b36;border-radius:6px;background:#151a22;color:#e7e5df;text-decoration:none;font:600 12px Arial,"Noto Sans KR",sans-serif;white-space:nowrap}
    .shared-category-link:hover{border-color:#e0a838;color:#e0a838}.shared-category-link.active{background:#1a2230;box-shadow:inset 0 -2px 0 currentColor}
    @media(max-width:600px){.shared-category-inner{padding:8px 12px}.shared-category-link{min-height:40px}}
  `;
  document.head.appendChild(style);
  const path=location.pathname;
  const nav=document.createElement('nav');
  nav.className='shared-category-nav';nav.setAttribute('aria-label','메인 카테고리');
  nav.innerHTML=`<div class="shared-category-inner">${items.map(item=>{
    const active=item.match&&item.match(path),attrs=item.external?' target="_blank" rel="noopener"':'';
    return `<a class="shared-category-link${active?' active':''}" href="${item.href}"${attrs} style="${item.tone?`border-color:${item.tone};color:${item.tone}`:''}">${item.label}</a>`;
  }).join('')}</div>`;
  const header=document.querySelector('body > header, header');
  if(header) header.insertAdjacentElement('afterend',nav); else document.body.prepend(nav);
  const active=nav.querySelector('.active');if(active) requestAnimationFrame(()=>active.scrollIntoView({block:'nearest',inline:'center'}));
})();
