"""
scripts/post_ticker_deepdives_to_blogger.py

gexoption.com의 옵션 분석 엔진(options_engine.analyze_ticker)을 그대로 재사용해서,
종목별 "감마 분석" 딥다이브 글을 매일 Google Blogger(블로그스팟)에 초안(Draft)으로
자동으로 만들어 둔다.

대상 종목 = watchlist.txt에 있는 고정 10종목 + 그날 leaders_report.json의
오늘의 급등주 1위 종목(이미 watchlist에 있으면 중복으로 만들지 않음).

각 글에는:
  - 현재가 / Call Wall(상방 저항) / Put Wall(하방 지지) / Gamma Flip / Max Pain
  - 1차 지지·저항이 깨졌을 때의 2차 지지·저항 라인 (gex_by_strike에서 계산)
  - options_engine.build_narrative()가 만드는 시나리오 해설 문장
  - 당일 핵심 조건과 GEXOPTION.COM 연결 버튼
을 넣는다.

필요한 GitHub Actions 시크릿 (post_gamma_to_blogger.py와 동일):
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN / BLOGGER_BLOG_ID

⚠️ 자동으로 "발행"까지 하지는 않는다 (isDraft=True). 본인이 Blogger 편집기에서
직접 코멘트를 채우고 확인한 뒤 직접 "게시"를 눌러야 블로그에 공개된다.

발행이 실패해도 리포트 생성/커밋 같은 나머지 파이프라인은 절대 실패하면 안 되므로,
종목 하나가 실패해도 나머지는 계속 진행하고, 전체를 감싸는 예외 처리로 스크립트
자체는 항상 종료 코드 0을 반환한다.
"""

import json
import os
import sys
import time
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# Blogger API가 짧은 시간에 연속으로 글을 만들면 429(Too Many Requests)를 반환한다.
# 종목 사이에 이 시간(초)만큼 쉬어서 API 호출 속도를 늦춘다.
SECONDS_BETWEEN_TICKERS = 8

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "api"))
from options_engine import analyze_ticker  # noqa: E402

WATCHLIST_FILE = os.path.join(ROOT, "scripts", "watchlist.txt")
LEADERS_REPORT_PATH = os.path.join(ROOT, "public", "leaders_report.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_API = "https://www.googleapis.com/blogger/v3/blogs"
SITE_URL = "https://gexoption.com"

TEXT_COLOR = "#1f2937"
MUTED_COLOR = "#64748b"
ACCENT_COLOR = "#d79a00"
PANEL_COLOR = "#f8fafc"
LINE_COLOR = "#e2e8f0"


# ---------------------------------------------------------------------------
# 대상 종목 선정
# ---------------------------------------------------------------------------
def load_watchlist():
    tickers = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tickers.append(line.upper())
    return tickers


def pick_bonus_ticker(watchlist):
    """오늘의 급등주 1위가 watchlist에 이미 없으면 보너스 종목으로 추가."""
    try:
        with open(LEADERS_REPORT_PATH, encoding="utf-8") as f:
            report = json.load(f)
        top_gainers = report.get("top_gainers") or []
        if top_gainers:
            candidate = top_gainers[0]["ticker"].upper()
            if candidate not in watchlist:
                return candidate
    except Exception as e:
        print(f"[post_ticker_deepdives] 보너스 종목 선정 실패: {e}")
    return None


# ---------------------------------------------------------------------------
# 2차 지지/저항 계산
# ---------------------------------------------------------------------------
def find_secondary_levels(gex_by_strike, call_wall, put_wall):
    above_call_wall = [
        s for s in gex_by_strike if call_wall is not None and s["strike"] > call_wall
    ]
    below_put_wall = [
        s for s in gex_by_strike if put_wall is not None and s["strike"] < put_wall
    ]
    secondary_resistance = (
        max(above_call_wall, key=lambda s: s["call_gex"])["strike"]
        if above_call_wall
        else None
    )
    secondary_support = (
        min(below_put_wall, key=lambda s: s["put_gex"])["strike"]
        if below_put_wall
        else None
    )
    return secondary_support, secondary_resistance


def _fmt(x):
    if x is None:
        return "-"
    x = float(x)
    return f"{x:.0f}" if x.is_integer() else f"{x:.1f}"


def _money(x):
    value = _fmt(x)
    return value if value == "-" else f"${value}"


def _metric_row(label, value, note=""):
    note_html = (f'<span style="display:block; color:{MUTED_COLOR}; font-size:12px; margin-top:2px;">{escape(note)}</span>' if note else "")
    return (f'<tr><th style="width:42%; padding:11px 10px; background:{PANEL_COLOR}; text-align:left; border-top:1px solid {LINE_COLOR};">{escape(label)}</th>'
            f'<td style="padding:11px 10px; border-top:1px solid {LINE_COLOR}; font-weight:700;">{escape(str(value))}{note_html}</td></tr>')


def _today_summary(ticker, data):
    spot, put_wall, call_wall = data.get("spot"), data.get("put_wall"), data.get("call_wall")
    regime_text = "양의 감마 구간" if data.get("regime") == "positive" else "음의 감마 구간"
    if spot is None or put_wall is None or call_wall is None:
        return f"{ticker}는 현재 {regime_text}입니다. 주요 GEX 레벨과 실시간 가격을 함께 확인하세요."
    if put_wall <= spot <= call_wall:
        location = f"Put Wall {_money(put_wall)}과 Call Wall {_money(call_wall)} 사이"
    elif spot > call_wall:
        location = f"Call Wall {_money(call_wall)} 위"
    else:
        location = f"Put Wall {_money(put_wall)} 아래"
    return f"{ticker} 현재가는 {location}에 있으며, 옵션 구조는 {regime_text}입니다."


def narrative_html(lines):
    cards = []
    for line in lines:
        cleaned = line.replace("&nbsp;&nbsp;• ", "")
        cards.append(f'<div style="padding:12px 14px; margin:8px 0; background:{PANEL_COLOR}; border-left:3px solid {ACCENT_COLOR}; border-radius:6px;">{cleaned}</div>')
    return "".join(cards) or f'<p style="color:{MUTED_COLOR};">해설 데이터가 없습니다.</p>'


def secondary_levels_html(put_wall, call_wall, secondary_support, secondary_resistance):
    lines = []
    if put_wall is not None and secondary_support is not None:
        lines.append(
            f"<li>1차 지지({_fmt(put_wall)})가 깨지면 다음 지지선은 "
            f"<b>{_fmt(secondary_support)}</b> 부근입니다.</li>"
        )
    if call_wall is not None and secondary_resistance is not None:
        lines.append(
            f"<li>1차 저항({_fmt(call_wall)})을 돌파하면 다음 저항선은 "
            f"<b>{_fmt(secondary_resistance)}</b> 부근입니다.</li>"
        )
    if not lines:
        return ""
    return ('<h3 style="margin:28px 0 8px; color:#0f172a;">🧭 다음 지지·저항</h3>'
            f'<ul style="margin:0; padding-left:22px;">{"".join(lines)}</ul>')


# ---------------------------------------------------------------------------
# 글 본문 생성
# ---------------------------------------------------------------------------
def build_post(ticker, data, date_str):
    title = f"[{date_str}] {ticker} 옵션 GEX 분석: Call Wall·Put Wall·Gamma Flip"

    secondary_support, secondary_resistance = find_secondary_levels(
        data.get("gex_by_strike", []), data.get("call_wall"), data.get("put_wall")
    )

    scenario_html = narrative_html(data.get("narrative", []))
    price_change = data.get("price_change_pct")
    price_change_text = (
        f"({price_change:+.2f}%)" if price_change is not None else ""
    )

    body = f"""
<div style="max-width:820px; margin:0 auto; color:{TEXT_COLOR}; font-family:Arial,sans-serif; line-height:1.75;">
  <h2 style="margin:0 0 8px; color:#0f172a;">{escape(ticker)} 옵션 GEX 분석</h2>
  <p style="margin:0 0 20px; color:{MUTED_COLOR};">{date_str} 기준 · Call Wall / Put Wall / Gamma Flip</p>
  <div style="padding:16px 18px; background:#fff8e1; border-left:4px solid {ACCENT_COLOR}; border-radius:8px; margin-bottom:24px;">
    <b>오늘의 한 줄 분석</b><br>{escape(_today_summary(ticker, data))}</div>
  <h3 style="margin:0 0 8px; color:#0f172a;">📊 핵심 옵션 레벨</h3>
  <div style="overflow-x:auto; margin-bottom:24px;"><table cellpadding="0" cellspacing="0" style="border-collapse:collapse; width:100%; min-width:520px; font-size:14px; border:1px solid {LINE_COLOR};">
    {_metric_row('현재가', f"${data.get('spot', '-')} {price_change_text}", '분석 시점 가격')}
    {_metric_row('Call Wall', _money(data.get('call_wall')), '상방 저항 후보')}
    {_metric_row('Put Wall', _money(data.get('put_wall')), '하방 지지 후보')}
    {_metric_row('Gamma Flip', _money(data.get('gamma_flip')), '감마 체제 전환 기준')}
    {_metric_row('Max Pain', _money(data.get('max_pain')), f"만기 {data.get('expiry_used', '-')}")}
    {_metric_row('감마 체제', '양의 감마' if data.get('regime') == 'positive' else '음의 감마')}
  </table></div>
  <h3 style="margin:28px 0 8px; color:#0f172a;">📈 오늘의 시나리오</h3>{scenario_html}
  {secondary_levels_html(data.get('put_wall'), data.get('call_wall'), secondary_support, secondary_resistance)}
  <h3 style="margin:28px 0 8px; color:#0f172a;">✅ 오늘 확인할 핵심 조건</h3>
  <ul style="margin:0; padding-left:22px;">
    <li>현재가가 Put Wall {_money(data.get('put_wall'))} 위에서 지지를 유지하는지 확인</li>
    <li>Call Wall {_money(data.get('call_wall'))} 돌파 시 거래량과 종가 안착 여부 확인</li>
    <li>Gamma Flip {_money(data.get('gamma_flip'))} 이탈 또는 회복 시 변동성 변화 확인</li>
  </ul>
  <p style="text-align:center; margin:30px 0;"><a href="{SITE_URL}" target="_blank" rel="noopener" style="display:inline-block; padding:12px 20px; background:#0f172a; color:#fff; text-decoration:none; border-radius:8px; font-weight:700;">GEXOPTION에서 최신 데이터 확인하기</a></p>
  <p style="font-size:12px; color:#888; margin-top:26px; padding-top:16px; border-top:1px solid {LINE_COLOR};">⚠️ 이 글은 정보 제공 목적으로 자동 생성되었으며, 특정 종목의 매수·매도를 권유하는 투자자문이 아닙니다. 모든 투자 판단과 책임은 본인에게 있습니다.</p>
</div>
"""
    return title, body


# ---------------------------------------------------------------------------
# Blogger API
# ---------------------------------------------------------------------------
def get_access_token():
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def already_posted_today(access_token, blog_id, title):
    try:
        resp = requests.get(
            f"{BLOGGER_API}/{blog_id}/posts/search",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": title},
            timeout=20,
        )
        if resp.status_code != 200:
            return False
        items = resp.json().get("items", [])
        return any(item.get("title") == title for item in items)
    except Exception:
        return False


def publish_draft(access_token, blog_id, title, body, ticker, max_retries=3):
    """429(Too Many Requests)를 만나면 잠깐 기다렸다가 재시도한다."""
    for attempt in range(1, max_retries + 1):
        resp = requests.post(
            f"{BLOGGER_API}/{blog_id}/posts/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            params={"isDraft": "true"},
            json={"kind": "blogger#post", "title": title, "content": body,
                  "labels": [ticker, "미국주식", "옵션", "GEX", "감마분석"]},
            timeout=30,
        )
        if resp.status_code == 429 and attempt < max_retries:
            wait_s = 20 * attempt
            print(
                f"[post_ticker_deepdives] 429(속도 제한) — {wait_s}초 대기 후 재시도 "
                f"({attempt}/{max_retries})"
            )
            time.sleep(wait_s)
            continue
        resp.raise_for_status()
        return resp.json()


def main():
    try:
        blog_id = os.environ["BLOGGER_BLOG_ID"]
    except KeyError:
        print("[post_ticker_deepdives] BLOGGER_BLOG_ID 없음 — 건너뜁니다.")
        return

    watchlist = load_watchlist()
    bonus = pick_bonus_ticker(watchlist)
    tickers = list(watchlist) + ([bonus] if bonus else [])

    date_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    try:
        access_token = get_access_token()
    except Exception as e:
        print(f"[post_ticker_deepdives] 토큰 발급 실패, 전체 건너뜀: {e}")
        return

    for i, ticker in enumerate(tickers):
        if i > 0:
            # Blogger API 속도 제한(429)을 피하기 위해 종목 사이에 잠깐 쉰다.
            time.sleep(SECONDS_BETWEEN_TICKERS)
        try:
            data = analyze_ticker(ticker)
            title, body = build_post(ticker, data, date_str)

            if already_posted_today(access_token, blog_id, title):
                print(f"[post_ticker_deepdives] 이미 있음, 건너뜀: {title}")
                continue

            result = publish_draft(access_token, blog_id, title, body, ticker)
            post_id = result.get("id")
            edit_url = f"https://www.blogger.com/blog/post/edit/{blog_id}/{post_id}"
            print(f"[post_ticker_deepdives] {ticker} 초안 생성 완료 — {edit_url}")
        except Exception as e:
            print(f"[post_ticker_deepdives] {ticker} 처리 실패 (다음 종목 계속): {e}")


if __name__ == "__main__":
    main()
