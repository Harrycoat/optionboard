"""Deterministic setup rules. No orders; EVP is not TOS Smooth."""
from swing_radar_chart import indicators

def daily_setup(rows):
    if len(rows)<55:raise ValueError('DATA_INSUFFICIENT: 55 completed daily bars required')
    c=[b['c'] for b in rows];ma20=sum(c[-20:])/20;ma50=sum(c[-50:])/50;prior=sum(c[-55:-5])/50
    if min(c)<=0:raise ValueError('DATA_INVALID: nonpositive price')
    ind=indicators(rows)
    touch=any(b['l']<=sum(c[i-19:i+1])/20<=b['h'] or abs(b['c']/(sum(c[i-19:i+1])/20)-1)<=.02 for i,b in list(enumerate(rows))[-5:])
    easing=ind[-1]['evp']>ind[-2]['evp']
    expanding=ind[-1]['evp']<ind[-2]['evp'] and ind[-1]['evp']<0
    holding=min(b['l'] for b in rows[-3:])>=min(b['l'] for b in rows[-8:-3])*.99
    compression=(max(b['h'] for b in rows[-3:])-min(b['l'] for b in rows[-3:]))<=(max(b['h'] for b in rows[-8:-3])-min(b['l'] for b in rows[-8:-3]))
    high=max(b['h'] for b in rows[-252:]);high20=max(b['h'] for b in rows[-20:]);stop=min(b['l'] for b in rows[-5:])*.995
    return {'sma50_rising':ma50>prior,'sma50_slope_pct':(ma50/prior-1)*100,'sma50':ma50,'sma20':ma20,'distance_sma20_pct':(c[-1]/ma20-1)*100,'touched_sma20':touch,'pressure_easing':easing,'pressure_expanding':expanding,'low_holding':holding,'compression':compression,'daily_hull20':ind[-1]['hull20'],'daily_hull_distance_pct':(c[-1]/ind[-1]['hull20']-1)*100,'drawdown_52w_pct':(high-c[-1])/high*100,'drawdown_20d_pct':(high20-c[-1])/high20*100,'stop_idea':stop,'watch_candidate':ma50>prior and touch and easing and holding,'close':c[-1]}

def intraday_setup(rows):
    bars=indicators(rows)
    if len(bars)<35:raise ValueError('DATA_INSUFFICIENT: 35 completed 30m bars required')
    b,p=bars[-1],bars[-2];h=b['hull20'];ph=p['hull20']
    hull_up=h>ph;turned=hull_up and any(bars[i]['hull20']<=bars[i-1]['hull20'] for i in range(len(bars)-4,len(bars)-1))
    evp_up=b['evp']>p['evp'];evp_turn=evp_up and any(bars[i]['evp']<=bars[i-1]['evp'] for i in range(len(bars)-4,len(bars)-1))
    cross=p['close']<=ph and b['close']>h
    pullback=any(v['close']<=v['hull20'] for v in bars[-5:-1])
    dist=(b['close']/h-1)*100
    return {'last_close':b['close'],'hull20':h,'hull_distance_pct':dist,'hull_turned_up':turned,'hull_rising':hull_up,'evp_turn_up':evp_turn,'evp_rising':evp_up,'cross_now':cross,'pullback':pullback,'recent_turn':turned and evp_turn,'bar_start_utc':b['t'],'bar_end_ms':rows[-1]['end_ms'],'ready':cross and turned and evp_turn and pullback,'watch':hull_up and evp_up and abs(dist)<=1.5,'add_setup':hull_up and evp_turn and pullback and b['close']>h}

def classify(d,s):
    if not d['sma50_rising'] or d['pressure_expanding']:return 'WEAK'
    valid=d['touched_sma20'] and d['pressure_easing'] and d['low_holding']
    if valid and s['ready']:return 'ENTRY'
    if s['hull_distance_pct']>3:return 'EXTENDED'
    if valid and s['watch'] and s['hull_turned_up']:return 'READY'
    if valid:return 'WATCH'
    return 'NO_SETUP'
