(function(){
  const STORAGE_KEY='gexoption-language';
  const pageTitleKo=document.title;
  const requestedLanguage=new URLSearchParams(location.search).get('lang');
  const translations={
    'GEX Option — 감마 익스포저 실시간 조회 | Real-Time Gamma Exposure Dashboard':'GEX Option — Real-Time Gamma Exposure Dashboard',
    '감마 익스포저 조회':'Gamma Exposure Lookup',
    '티커 입력 (예: AAPL, NET, SNDK)':'Enter ticker (e.g. AAPL, NET, SNDK)',
    '조회':'Search','📘 사용법 매뉴얼':'📘 User Guide','👤 로그인':'👤 Sign In','● 내 계정':'● My Account',
    '🏠 홈':'🏠 Home','🧭 AI 주도주 로드맵':'🧭 AI Leaders Roadmap','🏭 산업별 주도주':'🏭 Industry Leaders',
    '모멘텀 TOP 100':'Momentum Top 100','성장성 ETF':'Growth ETFs','거래량 급증주':'Volume Leaders',
    '🎯 오늘의 매수 신호':'🎯 Today’s Buy Signals','장기보유 운용':'Long-Term Portfolio','스윙 트레이딩':'Swing Trading',
    '▥ 현재 시장 위치 · CNN Fear & Greed':'▥ Current Market Position · CNN Fear & Greed',
    'ⓘ 먼저 시장 환경을 확인한 뒤 종목별 위치를 판단합니다.':'ⓘ Check the market environment before evaluating each stock.',
    '불러오는 중...':'Loading...','불러오는 중':'Loading','시장 심리를 확인하고 있습니다.':'Checking market sentiment.',
    '극도의 공포':'Extreme Fear','공포':'Fear','중립':'Neutral','탐욕':'Greed','극도의 탐욕':'Extreme Greed',
    '극도의 공포 구간 · 신규 진입 규모를 줄이고 Put Wall 지지를 먼저 확인':'Extreme Fear · Reduce new position size and confirm Put Wall support first',
    '공포 구간 · Stage 2 종목을 선별하고 Put Wall 반등 확인 후 진입':'Fear · Focus on Stage 2 stocks and wait for a Put Wall rebound',
    '중립 구간 · Stage와 Wall 신호가 일치하는 종목만 선택':'Neutral · Select stocks whose Stage and Wall signals agree',
    '탐욕 구간 · 보유는 가능하지만 Call Wall 접근 시 수익 보호 준비':'Greed · Holding is reasonable, but prepare to protect profits near the Call Wall',
    '극도의 탐욕 구간 · 추격매수보다 Call Wall 저항과 헤지를 우선 확인':'Extreme Greed · Prioritize Call Wall resistance and hedging over chasing',
    '💡 “주식은 공포에서 매수하고, 탐욕에서 매도한다.”':'💡 “Buy when the market is fearful; protect profits when it is greedy.”',
    '미주스탁':'U.S. Stocks','초보부터 전문가까지':'From beginners to professionals','주식거래의 모든 것을':'Everything about stock trading',
    '한곳에서 확인':'All in one place','바로가기 →':'Visit →',
    '좋은 주식은 오래 보유하고,':'Hold great companies for the long term,',
    '옵션으로 수익을 보호하며':'protect gains with options,','매월 수익을 만드세요':'and build monthly income.',
    'Call Wall에서 자산을 보호하고, Put Wall에서 월세형 현금흐름을 만듭니다.':'Protect your holdings near the Call Wall and build option income near the Put Wall.',
    '옵션을 잘 몰라도 정해진 가격과 원칙만 따라가면 되는 기록 기반 트레이딩 시스템입니다.':'A rules-based trading system built around clear price levels—even for investors new to options.',
    '내 관심종목 보기 →':'View My Watchlist →','⌕ 종목 바로 조회':'⌕ Search a Ticker','📊 내 종목':'📊 My Holdings',
    '🎬 실전 대응 영상 · 매일 업데이트':'🎬 Real Trading Videos · Updated Daily',
    '관심종목 중 실제 보유종목의 수량 · 평균단가 · 투자금액 · 평가손익 · 수익률을 확인합니다.':'Review position size, average cost, invested amount, market value, profit and return for your holdings.',
    '현재가':'Current Price','관심종목 저장':'Save Watchlist','PC와 휴대전화에서 동일하게 확인':'Sync across desktop and mobile',
    '핵심 가격 자동 표시':'Automatic Key Levels','매일 업데이트':'Updated Daily','시장 국면과 관심종목 변화를 빠르게 확인':'Quickly track market conditions and watchlist changes',
    '실전 대응 영상':'Real Trading Video','매일 새로운 대응 사례 업데이트':'New real-world trading examples updated daily',
    'Call Wall과 Put Wall을 실제 차트에서 어떻게 활용했는지 짧게 확인합니다.':'See how Call Wall and Put Wall levels are used on real charts.',
    '① Call Wall $370 · 두 차례 저항 확인':'① Call Wall $370 · Resistance confirmed twice',
    '② 보유 주식 보호를 위한 보험 Put 2회':'② Two protective puts used to hedge shares',
    '③ Put Wall $360 · 반등 확인 후 청산':'③ Put Wall $360 · Closed after rebound confirmation',
    'YouTube에서 더 보기 →':'Watch More on YouTube →','📍 시장':'📍 Markets',
    '⭐ 나의 관심종목':'⭐ My Watchlist','📊 내 종목':'📊 My Holdings','🧭 AI 주도주 로드맵':'🧭 AI Leaders Roadmap',
    '⌕ NVDA 검색 결과 미리보기':'⌕ NVDA Search Preview','실제 검색 데이터 · 옵션 15분 지연':'Live search data · Options delayed 15 minutes',
    'GEX 데이터 확인 중':'Checking GEX data','회원가입하고':'Create an account and','내 관심종목 저장하기':'save your watchlist','무료 회원가입':'Free Sign Up',
    'AI 생태계 주도주':'AI Ecosystem Leaders','데이터 준비 중':'Preparing data',
    '종목 · 현재가':'Ticker · Current Price','이번 주 PUT WALL · 현재가 · 이번 주 CALL WALL':'THIS WEEK’S PUT WALL · PRICE · CALL WALL','현재 판단 · 관심종목':'Current View · Watchlist',
    '티커를 입력해 관심종목에 바로 추가할 수 있습니다.':'Enter a ticker to add it directly to your watchlist.',
    '티커 입력 (예: AAPL, NVDA)':'Enter ticker (e.g. AAPL, NVDA)','추가':'Add','새로고침':'Refresh',
    '현재 판단':'Current View','진입 검토':'Consider Entry','보유·관찰':'Hold · Monitor','헤지 검토':'Consider Hedging','관심종목':'Watchlist','☆ 관심추가':'☆ Add to Watchlist','★ 관심종목':'★ Watchlist','GEX 조회':'View GEX',
    '성장성 ETF TOP 10 | GEX Option':'Growth ETF Top 10 | GEX Option','GEX Option · 성장성 ETF':'GEX Option · Growth ETFs','← 메인으로':'← Back to Home',
    '🚀 성장성 ETF TOP 10':'🚀 Growth ETF Top 10','순위는 고정하지 않고 매일 다시 계산합니다.':'Rankings are recalculated daily rather than fixed.',
    '성장·기술·AI·반도체 ETF의 현재 주도력을 비교합니다. ETF를 클릭하면 안에서 움직이는 대표 주도주를 바로 확인할 수 있습니다.':'Compare current leadership among growth, technology, AI and semiconductor ETFs. Open an ETF to see its leading holdings.',
    '당일 모멘텀 · 거래량 · Weinstein Stage · Gamma Flip 및 이번 주 Put Wall·Call Wall 위치를 종합합니다.':'Combines daily momentum, volume, Weinstein Stage, Gamma Flip and this week’s Put Wall and Call Wall levels.',
    '데이터를 불러오는 중...':'Loading data...','ETF·옵션 데이터 지연 가능 · 투자판단 참고용':'ETF and options data may be delayed · For reference only',
    '순위':'Rank','ETF · 분류':'ETF · Category','등락률':'Change','거래량':'Volume','점수':'Score','이번 주 Put Wall · 현재가 · Call Wall':'This Week’s Put Wall · Price · Call Wall',
    '성장성 ETF를 분석하고 있습니다...':'Analyzing growth ETFs...',
    '산업별 주도주 | GEX Option':'Industry Leaders | GEX Option','· 산업별 주도주':'· Industry Leaders','🏭 산업별 주도주':'🏭 Industry Leaders',
    '현재 시장 · CNN Fear & Greed':'Current Market · CNN Fear & Greed','산업 선택':'Select Industry','대표 주도주':'Leading Stocks','현재 판단':'Current View','기능':'Actions','산업별 데이터를 준비하고 있습니다.':'Preparing industry data.',
    '미국시장을 11개 주요 산업으로 나누어 대표 주도주의 현재가와 이번 주 Put Wall·Call Wall을 비교합니다. AI 발전단계가 아닌 시장 전체 산업 분류입니다.':'Compare current prices and weekly Put Wall and Call Wall levels for leaders across 11 major U.S. market sectors.',
    '산업의 방향을 먼저 확인하고, 그 안에서 강한 주도주를 선택합니다.':'Check the sector trend first, then select its strongest leaders.',
    'Put Wall에서는 진입 기회를, Call Wall에서는 수익 보호 시점을 확인합니다.':'Use the Put Wall to assess entry opportunities and the Call Wall to plan profit protection.',
    '모멘텀 강세주 TOP 100 | GEX Option':'Top 100 Momentum Stocks | GEX Option','· 모멘텀 강세주':'· Momentum Leaders','모멘텀 강세주 TOP 100':'Top 100 Momentum Stocks',
    '중장기 가격 강도와 모멘텀 순위입니다. 종목을 관심종목에 저장하거나 GEX 분석 화면으로 바로 이동할 수 있습니다.':'A ranking of intermediate- and long-term price strength. Save a stock to your watchlist or open its GEX analysis.',
    '데이터 불러오는 중...':'Loading data...','25개 보기':'Show 25','50개 보기':'Show 50','100개 보기':'Show 100','티커':'Ticker','회사명':'Company','52주 상승률':'52-Week Gain',
    '🏠 메인으로':'🏠 Back to Home','스캔 유니버스':'Scan Universe','Stage 2 · 진입 검토':'Stage 2 · Entry Review','Stage 1 · 관찰':'Stage 1 · Monitor','최우선 후보':'Top Candidate','오늘의 최우선 신호':'Today’s Top Signal',
    '오늘 신호를 우선 표시하고, 없으면':'Today’s signals appear first. If none qualify, we show','최근 5거래일 신호':'signals from the last five sessions','신호 임박 종목':'stocks approaching a signal',
    'Stage 1은 관찰, Stage 2는 진입 검토이며 Put Wall·Gamma Flip 위치로 다시 확인합니다.':'Stage 1 means monitor; Stage 2 means review for entry. Confirm with Put Wall and Gamma Flip positioning.',
    '매일 장 마감 후 자동 계산':'Automatically calculated after each market close',
    '기술 신호 점수와 GEX 위치가 가장 강한 한 종목':'The stock with the strongest technical score and GEX positioning',
    '오늘의 이상 옵션 거래 | GEX Option':'Today’s Unusual Options Activity | GEX Option','🚨 오늘의 이상 옵션 거래':'🚨 Today’s Unusual Options Activity','🐋 기관은 오늘 어디에 베팅했을까?':'🐋 Where Is Institutional Money Moving Today?',
    '거래량이 기존 미결제약정(OI)보다 유난히 크게 증가한 옵션 계약을 보여드립니다.':'Shows option contracts whose volume is unusually large relative to existing open interest (OI).',
    '유동성 상위 종목에서 새로 유입된 대규모 포지션 가능성을 확인합니다. 데이터는 약 15분 지연됩니다.':'Identify possible new large positions in liquid stocks. Data is delayed by approximately 15 minutes.'
  };
  const attrOriginals=new WeakMap();
  let language=requestedLanguage==='en'||requestedLanguage==='ko'?requestedLanguage:(localStorage.getItem(STORAGE_KEY)==='en'?'en':'ko');
  let applying=false;
  const hasKorean=value=>/[가-힣]/.test(value||'');
  const translateValue=value=>{
    const leading=value.match(/^\s*/)[0],trailing=value.match(/\s*$/)[0],core=value.trim();
    return translations[core]?leading+translations[core]+trailing:value;
  };
  function translateTextNode(node){
    if(!node.nodeValue||!node.nodeValue.trim())return;
    if(language==='en'){
      if(hasKorean(node.nodeValue))node.__gexKo=node.nodeValue;
      node.nodeValue=translateValue(node.nodeValue);
    }else if(node.__gexKo){node.nodeValue=node.__gexKo;}
  }
  function translateAttributes(el){
    if(!(el instanceof Element))return;
    const attrs=['placeholder','title','aria-label'];
    let original=attrOriginals.get(el);
    if(!original){original={};attrOriginals.set(el,original);}
    attrs.forEach(attr=>{
      if(!el.hasAttribute(attr))return;
      const value=el.getAttribute(attr);
      if(language==='en'){
        if(hasKorean(value))original[attr]=value;
        el.setAttribute(attr,translateValue(value));
      }else if(original[attr])el.setAttribute(attr,original[attr]);
    });
  }
  function applyLanguage(root=document.body){
    if(!root)return;
    applying=true;
    document.documentElement.lang=language;
    if(root.nodeType===Node.TEXT_NODE)translateTextNode(root);
    else{
      translateAttributes(root);
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
      let node;while((node=walker.nextNode())){
        if(node.nodeType===Node.ELEMENT_NODE){
          if(['SCRIPT','STYLE','IFRAME','NOSCRIPT'].includes(node.tagName)){walker.currentNode=node;continue;}
          translateAttributes(node);
        }else if(!['SCRIPT','STYLE','NOSCRIPT'].includes(node.parentElement?.tagName))translateTextNode(node);
      }
    }
    document.title=language==='en'?(translations[pageTitleKo]||pageTitleKo):pageTitleKo;
    updateToggle();
    applying=false;
  }
  function updateToggle(){
    const button=document.getElementById('siteLanguageToggle');
    if(button){button.textContent=language==='ko'?'🌐 EN':'🌐 한국어';button.setAttribute('aria-label',language==='ko'?'Switch to English':'한국어로 전환');}
  }
  function setLanguage(next){
    language=next==='en'?'en':'ko';
    localStorage.setItem(STORAGE_KEY,language);
    const url=new URL(location.href);
    if(language==='en')url.searchParams.set('lang','en');else url.searchParams.delete('lang');
    history.replaceState(history.state,'',url.pathname+url.search+url.hash);
    applyLanguage();
    window.dispatchEvent(new CustomEvent('gexlanguagechange',{detail:{language}}));
  }
  function addToggle(){
    if(document.getElementById('siteLanguageToggle'))return;
    const button=document.createElement('button');button.id='siteLanguageToggle';button.type='button';button.className='site-language-toggle';button.addEventListener('click',()=>setLanguage(language==='ko'?'en':'ko'));
    const target=document.querySelector('.header-actions, .header-inner, .bar')||document.querySelector('header')||document.body;
    target.appendChild(button);updateToggle();
  }
  const style=document.createElement('style');style.textContent='.site-language-toggle{display:inline-flex;align-items:center;justify-content:center;min-height:38px;padding:8px 12px;border:1px solid #81adff;border-radius:7px;background:rgba(61,127,224,.08);color:#9fc0ff;font:800 12px Pretendard,Arial,sans-serif;white-space:nowrap;cursor:pointer}.site-language-toggle:hover{background:rgba(61,127,224,.18)}@media(max-width:760px){.site-language-toggle{min-height:42px;padding:8px 11px}}';document.head.appendChild(style);
  window.getSiteLanguage=()=>language;window.siteT=(ko,en)=>language==='en'?(en||translations[ko]||ko):ko;window.setSiteLanguage=setLanguage;
  document.addEventListener('DOMContentLoaded',()=>{
    addToggle();applyLanguage();
    const observer=new MutationObserver(records=>{if(applying)return;records.forEach(record=>{if(record.type==='characterData')applyLanguage(record.target);record.addedNodes.forEach(node=>applyLanguage(node));});});
    observer.observe(document.body,{subtree:true,childList:true,characterData:true});
  });
})();
