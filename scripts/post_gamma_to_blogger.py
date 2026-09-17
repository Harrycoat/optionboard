"""
scripts/post_gamma_to_blogger.py

gexoption.com이 매일 자동으로 계산한 감마 데이터(public/leaders_report.json)를
요약해서 Google Blogger(블로그스팟)에 읽기 쉬운 HTML 형식의
"오늘의 감마 브리핑" 초안(Draft)을 하루 1개씩 자동으로 만들어 둔다.

⚠️ 자동으로 "발행"까지 하지는 않는다 (isDraft=True). 본인이 Blogger 편집기에서
내용과 수치를 확인한 뒤 직접 "게시"를 눌러야 블로그에 공개된다.

필요한 GitHub Actions 시크릿 (1회만 설정하면 됨):
  GOOGLE_CLIENT_ID       - Google Cloud OAuth 클라이언트 ID
  GOOGLE_CLIENT_SECRET   - Google Cloud OAuth 클라이언트 시크릿
  GOOGLE_REFRESH_TOKEN   - 1회 OAuth 동의로 발급받은 refresh token
  BLOGGER_BLOG_ID        - 글을 올릴 Blogger 블로그의 blogId

발행이 실패해도 리포트 생성/커밋 같은 나머지 파이프라인은 절대 실패하면 안
되므로, 이 스크립트 안에서 모든 예외를 잡아서 로그만 남기고 종료 코드는
항상 0으로 반환한다 (main() 안에서 예외를 삼킨다).
"""

import json
import os
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "leaders_report.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_API = "https://www.googleapis.com/blogger/v3/blogs"

SITE_URL = "https://gexoption.com"

TEXT_COLOR = "#1f2937"
MUTED_COLOR = "#64748b"
ACCENT_COLOR = "#d79a00"
PANEL_COLOR = "#f8fafc"
LINE_COLOR = "#e2e8f0"


def get_access_token():
    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    refresh_token = os.environ["GOOGLE_REFRESH_TOKEN"]

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def load_report():
    with open(REPORT_PATH, encoding="utf-8") as f:
        return json.load(f)


def kst_date_str(report):
    generated_at = report.get("generated_at")
    if generated_at:
        dt = datetime.fromisoformat(generated_at).astimezone(ZoneInfo("Asia/Seoul"))
    else:
        dt = datetime.now(ZoneInfo("Asia/Seoul"))
    return dt.strftime("%Y-%m-%d")


def pct_text(v):
    if v is None:
        return "-"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def display(v):
    return escape("-" if v is None else str(v))


def _table(headers, rows_html, empty_text):
    if not rows_html:
        return (
            f'<p style="margin:10px 0 0; padding:14px; background:{PANEL_COLOR}; '
            f'border:1px solid {LINE_COLOR}; border-radius:8px; color:{MUTED_COLOR};">'
            f"{escape(empty_text)}</p>"
        )
    head = "".join(
        f'<th style="padding:10px 8px; background:#0f172a; color:#fff; text-align:left; white-space:nowrap;">{escape(h)}</th>'
        for h in headers
    )
    return (
        f'<div style="overflow-x:auto; margin-top:10px;"><table cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse; width:100%; min-width:660px; font-size:13px; border:1px solid {LINE_COLOR};">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>"
    )


def td(value):
    return f'<td style="padding:10px 8px; border-top:1px solid {LINE_COLOR};">{display(value)}</td>'


def gainers_table_html(rows):
    trs = [
        "<tr>" + td(r.get("ticker")) + td(f"${r.get('spot', '-')}")
        + td(pct_text(r.get("price_change_pct"))) + td(r.get("gamma_flip"))
        + td(r.get("call_wall")) + td(r.get("put_wall"))
        + td(r.get("stage_label")) + "</tr>"
        for r in rows
    ]
    return _table(
        ["티커", "현재가", "등락률", "Gamma Flip", "Call Wall", "Put Wall", "Stage"],
        trs,
        "오늘 조건에 맞는 급등주가 없습니다.",
    )


def gamma_flip_table_html(rows):
    trs = [
        "<tr>" + td(r.get("ticker")) + td(f"${r.get('spot', '-')}")
        + td(r.get("gamma_flip")) + td(r.get("gamma_regime"))
        + td(pct_text(r.get("flip_distance_pct"))) + "</tr>"
        for r in rows
    ]
    return _table(
        ["티커", "현재가", "Gamma Flip", "체제", "Flip까지 거리"],
        trs,
        "데이터 없음",
    )


def buy_signal_table_html(rows):
    trs = [
        "<tr>" + td(r.get("ticker")) + td(f"${r.get('spot', '-')}")
        + td(r.get("stage_label")) + td(r.get("hull21"))
        + td(pct_text(r.get("dev_pct"))) + "</tr>"
        for r in rows
    ]
    return _table(
        ["티커", "현재가", "스테이지", "Hull21", "Dev%"],
        trs,
        "오늘 매수 신호(스테이지1·2)가 없습니다.",
    )


def build_post(report, date_str):
    title = f"[{date_str}] 오늘의 GEX 감마 브리핑 | GEXOPTION"
    gainers = report.get("top_gainers", [])
    flip_rows = report.get("top10_gamma_flip", [])
    signals = report.get("dev_reentry_long", [])

    body = f"""
<div style="max-width:820px; margin:0 auto; color:{TEXT_COLOR}; font-family:Arial,sans-serif; line-height:1.75;">
  <h2 style="margin:0 0 8px; color:#0f172a;">오늘의 GEX 감마 브리핑</h2>
  <p style="margin:0 0 20px; color:{MUTED_COLOR};">{date_str} 장 마감 데이터 · GEXOPTION.COM</p>
  <div style="padding:16px 18px; background:#fff8e1; border-left:4px solid {ACCENT_COLOR}; border-radius:8px; margin-bottom:24px;">
    <b>오늘의 한눈 요약</b><br>급등 후보 {len(gainers)}개, Gamma Flip 근접 종목 {len(flip_rows)}개,
    HULL 매수 관찰 신호 {len(signals)}개가 포착되었습니다.
  </div>
  <p style="margin:0 0 26px;">유동성 상위 미국 주식의 옵션 데이터를 기준으로 Call Wall, Put Wall,
  Gamma Flip과 HULL 되돌림 신호를 정리했습니다. 수치는 매매 지시가 아니라 당일 시장 구조를 확인하기 위한 참고 자료입니다.</p>
  <h3 style="margin:26px 0 6px; color:#0f172a;">🔥 오늘의 급등주 Top 10</h3>
  <p style="margin:0; color:{MUTED_COLOR};">가격 변화와 주요 GEX 레벨을 함께 비교합니다.</p>
  {gainers_table_html(gainers)}
  <h3 style="margin:30px 0 6px; color:#0f172a;">📍 Gamma Flip 근접 Top 10</h3>
  <p style="margin:0; color:{MUTED_COLOR};">현재가가 Gamma Flip에 가까울수록 감마 체제 변화 가능성을 주의해서 봅니다.</p>
  {gamma_flip_table_html(flip_rows)}
  <h3 style="margin:30px 0 6px; color:#0f172a;">🎯 오늘의 HULL 관찰 신호</h3>
  <p style="margin:0; color:{MUTED_COLOR};">Hull21 이동평균과 Dev% 밴드 되돌림을 기준으로 한 스캐너 결과입니다.</p>
  {buy_signal_table_html(signals)}
  <p style="text-align:center; margin:30px 0;"><a href="{SITE_URL}" target="_blank" rel="noopener"
     style="display:inline-block; padding:12px 20px; background:#0f172a; color:#fff; text-decoration:none; border-radius:8px; font-weight:700;">GEXOPTION에서 실시간 데이터 확인하기</a></p>
  <p style="font-size:12px; color:#888; margin-top:26px; padding-top:16px; border-top:1px solid {LINE_COLOR};">
  ⚠️ 이 글은 정보 제공 목적으로 자동 생성되었으며, 특정 종목의 매수·매도를 권유하는 투자자문이 아닙니다. 모든 투자 판단과 책임은 본인에게 있습니다.</p>
</div>
"""
    return title, body


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


def publish_post(access_token, blog_id, title, body):
    # isDraft=True: 자동으로 "발행"하지 않고 초안(Draft) 상태로만 만든다.
    # 본인이 Blogger 편집기에서 내용을 확인한 다음, 직접 "게시" 버튼을 눌러야 실제로 공개된다.
    resp = requests.post(
        f"{BLOGGER_API}/{blog_id}/posts/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        params={"isDraft": "true"},
        json={"kind": "blogger#post", "title": title, "content": body,
              "labels": ["GEX", "미국주식", "옵션", "감마분석", "시장브리핑"]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    try:
        blog_id = os.environ["BLOGGER_BLOG_ID"]
        report = load_report()
        date_str = kst_date_str(report)
        title, body = build_post(report, date_str)

        access_token = get_access_token()

        if already_posted_today(access_token, blog_id, title):
            print(f"[post_gamma_to_blogger] 이미 오늘({date_str}) 글이 있어 건너뜁니다: {title}")
            return

        result = publish_post(access_token, blog_id, title, body)
        post_id = result.get("id")
        edit_url = f"https://www.blogger.com/blog/post/edit/{blog_id}/{post_id}"
        print(f"[post_gamma_to_blogger] 초안 생성 완료 (아직 비공개) — 편집 링크: {edit_url}")
    except Exception as e:
        print(f"[post_gamma_to_blogger] 발행 실패 (파이프라인은 계속 진행): {e}")


if __name__ == "__main__":
    main()
