"""
scripts/post_gamma_to_blogger.py

gexoption.com이 매일 자동으로 계산한 감마 데이터(public/leaders_report.json)를
요약해서 Google Blogger(블로그스팟)에 "오늘의 감마 브리핑" 초안(Draft)을 하루
1개씩 자동으로 만들어 둔다.

⚠️ 자동으로 "발행"까지 하지는 않는다 (isDraft=True). 데이터 표는 자동으로
채워지지만, 글 맨 위에 직접 쓸 코멘트 자리를 비워 두므로, 본인이 Blogger
편집기에 들어가서 그 부분을 실제 코멘트로 바꾸고 확인한 뒤 직접 "게시"를
눌러야 블로그에 공개된다.

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
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "leaders_report.json")

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_API = "https://www.googleapis.com/blogger/v3/blogs"

SITE_URL = "https://gexoption.com"


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


def _table(headers, rows_html, empty_text):
    if not rows_html:
        return f"<p>{empty_text}</p>"
    head = "".join(f"<th>{h}</th>" for h in headers)
    return (
        "<table border='1' cellpadding='6' cellspacing='0' "
        "style='border-collapse:collapse; width:100%; font-size:13px;'>"
        f"<tr>{head}</tr>{''.join(rows_html)}</table>"
    )


def gainers_table_html(rows):
    trs = [
        f"<tr><td>{r['ticker']}</td><td>${r.get('spot','-')}</td>"
        f"<td>{pct_text(r.get('price_change_pct'))}</td>"
        f"<td>{r.get('gamma_flip','-')}</td><td>{r.get('call_wall','-')}</td>"
        f"<td>{r.get('put_wall','-')}</td><td>{r.get('stage_label','-')}</td></tr>"
        for r in rows
    ]
    return _table(
        ["티커", "현재가", "등락률", "Gamma Flip", "Call Wall", "Put Wall", "Stage"],
        trs,
        "오늘 조건에 맞는 급등주가 없습니다.",
    )


def gamma_flip_table_html(rows):
    trs = [
        f"<tr><td>{r['ticker']}</td><td>${r.get('spot','-')}</td>"
        f"<td>{r.get('gamma_flip','-')}</td><td>{r.get('gamma_regime','-')}</td>"
        f"<td>{pct_text(r.get('flip_distance_pct'))}</td></tr>"
        for r in rows
    ]
    return _table(
        ["티커", "현재가", "Gamma Flip", "체제", "Flip까지 거리"],
        trs,
        "데이터 없음",
    )


def buy_signal_table_html(rows):
    trs = [
        f"<tr><td>{r['ticker']}</td><td>${r.get('spot','-')}</td>"
        f"<td>{r.get('stage_label','-')}</td><td>{r.get('hull21','-')}</td>"
        f"<td>{pct_text(r.get('dev_pct'))}</td></tr>"
        for r in rows
    ]
    return _table(
        ["티커", "현재가", "스테이지", "Hull21", "Dev%"],
        trs,
        "오늘 매수 신호(스테이지1·2)가 없습니다.",
    )


def build_post(report, date_str):
    title = f"오늘의 감마 브리핑 ({date_str}) — GEXOPTION.COM"

    body = f"""
<p style="background:#fff8e1; border:1px dashed #d4a017; padding:10px 14px; color:#7a5c00;">
✏️ <b>[오늘 하고 싶은 말을 여기에 적어주세요 — 이 문단은 초안이라 자동으로 채워지지
않습니다. 발행 전에 이 박스를 지우고 직접 쓴 코멘트로 바꿔주세요.]</b>
</p>

<p>매일 장마감 후 <a href="{SITE_URL}" target="_blank" rel="noopener">GEXOPTION.COM</a>이
자동으로 계산한 옵션 감마 데이터를 요약한 글입니다. 유동성 상위 종목군을 기준으로
계산했습니다.</p>

<h3>🔥 오늘의 급등주 Top 10</h3>
{gainers_table_html(report.get("top_gainers", []))}

<h3>📍 Gamma Flip 근접 Top 10</h3>
<p>주가가 Gamma Flip(감마 체제 전환 기준가)에 가까울수록 변동성 성격이 바뀔 가능성이 큽니다.</p>
{gamma_flip_table_html(report.get("top10_gamma_flip", []))}

<h3>🎯 오늘의 매수 신호 (HULL 스테이지1·2)</h3>
<p>Hull21 이동평균 기준 Dev% 밴드 되돌림 스캐너 결과입니다.</p>
{buy_signal_table_html(report.get("dev_reentry_long", []))}

<p style="font-size:12px; color:#888; margin-top:20px;">
⚠️ 이 글은 정보 제공 목적으로 자동 생성되었으며, 특정 종목의 매수·매도를 권유하는
투자자문이 아닙니다. 모든 투자 판단과 책임은 본인에게 있습니다. 더 자세한 실시간
데이터는 <a href="{SITE_URL}" target="_blank" rel="noopener">GEXOPTION.COM</a>에서
확인하세요.
</p>
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
    # 본인이 Blogger 편집기에서 위쪽 코멘트 박스를 직접 쓴 글로 바꾸고
    # 확인한 다음, 직접 "게시" 버튼을 눌러야 실제로 블로그에 공개된다.
    resp = requests.post(
        f"{BLOGGER_API}/{blog_id}/posts/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        params={"isDraft": "true"},
        json={"kind": "blogger#post", "title": title, "content": body},
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
