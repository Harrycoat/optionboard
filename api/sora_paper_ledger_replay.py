"""Replay immutable-style SORA paper trade events into positions, cash, and P/L.

Compatible with existing SORA-01 administrator BUY ledger. Supports subsequent
SELL receipts and fractional-free lots. No I/O and never submits orders.
"""
from collections import defaultdict
from math import isfinite

def _money(x):
    if type(x) not in (int,float) or not isfinite(x) or x<=0:
        raise ValueError("INVALID_PRICE")
    return round(float(x),4)

def replay(ledger):
    initial=ledger.get("initial_cash")
    if type(initial) not in (int,float) or not isfinite(initial) or initial<=0:
        raise ValueError("INVALID_INITIAL_CASH")
    cash=round(float(initial),2)
    lots=defaultdict(list)
    trades=ledger.get("trades")
    if not isinstance(trades,list):raise ValueError("INVALID_TRADES")
    ids=set()
    realized=0.0
    for idx,t in enumerate(trades):
        if t.get("mode")!="PAPER":raise ValueError("NON_PAPER_EVENT")
        key=t.get("request_id")
        if not key or key in ids:raise ValueError("DUPLICATE_OR_MISSING_ID")
        ids.add(key)
        ticker=t.get("ticker")
        if not isinstance(ticker,str) or not ticker.isupper():raise ValueError("INVALID_SYMBOL")
        qty=t.get("quantity");price=_money(t.get("fill_price"))
        if type(qty)!=int or qty<=0:raise ValueError("INVALID_QUANTITY")
        amount=round(qty*price,2)
        if t.get("side")=="BUY":
            if cash+0.001<amount:raise ValueError("INSUFFICIENT_CASH")
            cash=round(cash-amount,2)
            lots[ticker].append([qty,price])
        elif t.get("side")=="SELL":
            if sum(n for n,_ in lots[ticker])<qty:raise ValueError("OVERSOLD")
            remaining=qty;basis=0.0
            while remaining:
                held,cost=lots[ticker][0];take=min(held,remaining)
                basis+=take*cost;remaining-=take;held-=take
                if held:lots[ticker][0][0]=held
                else:lots[ticker].pop(0)
            cash=round(cash+amount,2)
            realized=round(realized+amount-round(basis,2),2)
        else:raise ValueError("INVALID_SIDE")
    positions=[]
    for ticker,ticker_lots in sorted(lots.items()):
        qty=sum(n for n,_ in ticker_lots)
        if qty:
            basis=round(sum(n*p for n,p in ticker_lots),2)
            positions.append({"ticker":ticker,"quantity":qty,"cost_basis":basis,
                              "average_cost":round(basis/qty,4)})
    return {"cash":cash,"positions":positions,"trade_count":len(trades),
            "realized_pnl":realized,"mode":"PAPER"}

def confirm_balance(ledger):
    state=replay(ledger)
    if "cash" in ledger and abs(float(ledger["cash"])-state["cash"])>0.01:
        raise ValueError("LEDGER_CASH_MISMATCH")
    return state
