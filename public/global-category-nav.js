(function(){
  const items=[
    {href:'/',label:'🏠 홈',match:p=>p==='/'||p==='/index.html'},
    {href:'/ai-leader-roadmap.html#aiLeaderMap',label:'🧭 AI 주도주 로드맵',match:p=>p==='/ai-leader-roadmap.html',tone:'#55e6b5'},
    {href:'/industry-leaders.html',label:'🏭 산업별 주도주',match:p=>p==='/industry-leaders.html',tone:'#81adff'},
    {href:'/momentum-top-100.html',label:'모멘텀 TOP 100',match:p=>p==='/momentum-top-100.html'},
    {href:'/etf-market-leader.html',label:'성장성 ETF',match:p=>p==='/etf-market-leader.html'},
    {href:'/unusual-options.html',label:'거래량 급증주',match:p=>p==='/unusual-options.html'},
    {href:'/today-buy-signal.html',label:'🎯 오늘의 매수 신호',match:p=>p==='/today-buy-signal.html',tone:'#e0a838'},
    {href:'https://gex-future-leaders.harryahn.chatgpt.site/#future-leaders-core',label:'장기보유 운용',external:true,tone:'#55e6b5'},
    {href:'https://gex-future-leaders.harryahn.chatgpt.site/#premium-swing',label:'스윙 트레이딩',external:true,tone:'#e0a838'}
  ];
  const style=document.createElement('style');
  style.textContent=`
    .shared-market-wrap{max-width:1180px;margin:18px auto 12px;padding:0 20px;color:#ebe9e3;font-family:Arial,"Noto Sans KR",sans-serif}
    .shared-market-banner{padding:16px 18px;border:1px solid #e0a838;border-radius:12px;background:linear-gradient(135deg,rgba(224,168,56,.09),rgba(14,20,28,.96) 52%);box-shadow:0 10px 26px rgba(0,0,0,.2)}
    .shared-market-head{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:14px}.shared-market-head h2{margin:0;color:#ffd45f;font-size:22px;letter-spacing:-.02em}.shared-market-guide{color:#8f99a9;font-size:10px;white-space:nowrap}
    .shared-market-grid{display:grid;grid-template-columns:220px minmax(0,1fr);gap:18px}.shared-market-score{display:flex;flex-direction:column;justify-content:center;min-height:116px;padding:14px 20px;border:1px solid #27303c;border-radius:10px;background:rgba(8,13,19,.72)}
    .shared-market-label{color:#8f99a9;font-size:10px}.shared-market-value{margin-top:5px;color:#ff676d;font:800 34px ui-monospace,SFMono-Regular,Consolas,monospace}.shared-market-response{margin-top:9px;color:#8f99a9;font-size:9px;line-height:1.4}
    .shared-market-gauge{min-width:0;padding:10px 10px 5px;border:1px solid rgba(38,48,61,.65);border-radius:10px;background:rgba(8,13,19,.58)}.shared-market-axis{display:flex;justify-content:space-between;padding:0 2px 7px;color:#ebe9e3;font:9px ui-monospace,SFMono-Regular,Consolas,monospace}
    .shared-market-track{position:relative;height:17px;border-radius:999px;background:linear-gradient(90deg,#c91d2e 0%,#ff8a3d 25%,#a7afb8 50%,#53c74c 75%,#08763c 100%)}.shared-market-marker{position:absolute;top:50%;left:50%;width:18px;height:18px;border:3px solid #fff;border-radius:50%;background:#ff676d;transform:translate(-50%,-50%);box-shadow:0 0 0 2px rgba(255,103,109,.28),0 3px 10px rgba(0,0,0,.45);transition:left .35s ease}
    .shared-market-scale{display:grid;grid-template-columns:repeat(5,1fr);margin-top:9px}.shared-market-step{padding:5px 3px 2px;border-right:1px dashed rgba(139,152,170,.24);color:#8f99a9;text-align:center;font-size:9px}.shared-market-step:last-child{border-right:0}.shared-market-step.active{color:#e0a838;font-weight:800}.shared-market-quote{margin-top:14px;padding-top:11px;border-top:1px solid rgba(224,168,56,.22);color:#ffd45f;text-align:center;font-weight:800;font-size:12px}
    .shared-category-nav{border-bottom:1px solid #252b36;background:#10141b;overflow-x:auto;position:relative;z-index:8}
    .shared-category-inner{max-width:1180px;margin:0 auto;padding:9px 12px 9px 28px;display:flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap}
    .shared-category-link{display:inline-flex;align-items:center;gap:4px;padding:8px 10px;border:1px solid #252b36;border-radius:6px;background:#151a22;color:#e7e5df;text-decoration:none;font:600 12px Arial,"Noto Sans KR",sans-serif;white-space:nowrap}
    .shared-category-link:hover{border-color:#e0a838;color:#e0a838}.shared-category-link.active{background:#1a2230;box-shadow:inset 0 -2px 0 currentColor}
    body.has-shared-market .market-pill{display:none}
    @media(max-width:900px){.shared-category-inner{justify-content:flex-start;padding:8px 12px}.shared-category-link{min-height:40px}.shared-market-wrap{margin:12px auto 9px;padding:0 10px}.shared-market-banner{padding:12px 10px}.shared-market-head{align-items:flex-start;flex-direction:column;gap:6px}.shared-market-head h2{font-size:18px}.shared-market-guide{white-space:normal}.shared-market-grid{grid-template-columns:1fr;gap:9px}.shared-market-score{min-height:0;padding:12px 14px}.shared-market-value{font-size:28px}.shared-market-gauge{padding:10px 7px 5px}}
  `;
  document.head.appendChild(style);
  const path=location.pathname,query=new URLSearchParams(location.search);
  const market=document.createElement('section');
  market.className='shared-market-wrap';market.setAttribute('aria-label','현재 시장 위치');
  market.innerHTML=`<div class="shared-market-banner">
    <div class="shared-market-head"><h2>▥ 현재 시장 위치 · CNN Fear &amp; Greed</h2><span class="shared-market-guide">ⓘ 먼저 시장 환경을 확인한 뒤 종목별 위치를 판단합니다.</span></div>
    <div class="shared-market-grid">
      <div class="shared-market-score"><div class="shared-market-label">CNN MARKET SENTIMENT</div><div class="shared-market-value" data-market-value>불러오는 중...</div><div class="shared-market-response" data-market-response>시장 심리를 확인하고 있습니다.</div></div>
      <div class="shared-market-gauge"><div class="shared-market-axis"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div><div class="shared-market-track"><i class="shared-market-marker" data-market-marker aria-hidden="true"></i></div><div class="shared-market-scale" data-market-scale aria-label="공포 탐욕 단계"></div></div>
    </div>
    <div class="shared-market-quote">💡 “주식은 공포에서 매수하고, 탐욕에서 매도한다.”</div>
  </div>`;
  const nav=document.createElement('nav');
  nav.className='shared-category-nav';nav.setAttribute('aria-label','메인 카테고리');
  nav.innerHTML=`<div class="shared-category-inner">${items.map(item=>{
    const active=item.match&&item.match(path,query),attrs=item.external?' target="_blank" rel="noopener"':'';
    return `<a class="shared-category-link${active?' active':''}" href="${item.href}"${attrs} style="${item.tone?`border-color:${item.tone};color:${item.tone}`:''}">${item.label}</a>`;
  }).join('')}</div>`;
  const header=document.querySelector('body > header, header');
  if(header){header.insertAdjacentElement('afterend',nav);nav.insertAdjacentElement('afterend',market)}else{document.body.prepend(market);document.body.prepend(nav)}
  document.body.classList.add('has-shared-market');
  const active=nav.querySelector('.active');if(active) requestAnimationFrame(()=>active.scrollIntoView({block:'nearest',inline:'center'}));
  const labels=['극도의 공포','공포','중립','탐욕','극도의 탐욕'];
  const band=score=>score<25?[0,'EXTREME FEAR','극도의 공포 구간 · 신규 진입 규모를 줄이고 Put Wall 지지를 먼저 확인']:score<45?[1,'FEAR','공포 구간 · Stage 2 종목을 선별하고 Put Wall 반등 확인 후 진입']:score<56?[2,'NEUTRAL','중립 구간 · Stage와 Wall 신호가 일치하는 종목만 선택']:score<75?[3,'GREED','탐욕 구간 · 보유는 가능하지만 Call Wall 접근 시 수익 보호 준비']:[4,'EXTREME GREED','극도의 탐욕 구간 · 추격매수보다 Call Wall 저항과 헤지를 우선 확인'];
  fetch('/api/search?mode=fear_greed',{cache:'no-store'}).then(r=>r.json().then(data=>({ok:r.ok,data}))).then(({ok,data})=>{
    const score=Number(data.score);if(!ok||data.error||!Number.isFinite(score))throw new Error('시장 데이터 없음');const state=band(score);
    market.querySelector('[data-market-value]').textContent=`${score.toFixed(0)} · ${state[1]}`;market.querySelector('[data-market-response]').textContent=state[2];market.querySelector('[data-market-scale]').innerHTML=labels.map((label,i)=>`<span class="shared-market-step${i===state[0]?' active':''}">${label}</span>`).join('');
    const marker=market.querySelector('[data-market-marker]');marker.style.left=`${Math.max(1,Math.min(99,score))}%`;marker.style.background=score<45?'#ff676d':score<56?'#a7afb8':'#55e6b5';
  }).catch(()=>{market.querySelector('[data-market-value]').textContent='일시적으로 확인 불가';market.querySelector('[data-market-response]').textContent='시장 지수와 개별 종목의 Stage·Wall 위치를 함께 확인하세요.';market.querySelector('[data-market-scale]').innerHTML=labels.map(label=>`<span class="shared-market-step">${label}</span>`).join('')});
})();
