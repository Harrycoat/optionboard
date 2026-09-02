"""
fetch_blog_post.py
===================

역할: gexoption.com 홈페이지의 "오늘의 감마 브리핑" 카드는 실제 데이터가
      아니라 블로그(gexoption.blogspot.com)로 가는 링크 박스일 뿐이라서,
      이 스크립트가 블로그의 최신 글을 직접 열어서
        1) 게시글 스크린샷 (슬라이드쇼용 이미지)
        2) 게시글 본문 텍스트 (나레이션 대본 재료)
      둘 다 가져온다.

사용법:
    python scripts/fetch_blog_post.py

결과 (captures/YYYYMMDD/ 안에 저장):
    gamma_briefing.png   <- compose_video.py가 기존과 동일한 파일명으로 그대로 사용
    blog_post.txt        <- build_shorts_script.py에서 나레이션 재료로 사용 가능

참고: Blogger 템플릿마다 CSS 클래스명이 달라서, 자주 쓰이는 후보 셀렉터
      여러 개를 순서대로 시도한다. 만약 전부 실패하면 로그에 어떤 셀렉터가
      안 맞았는지 남기니, 그때 실제 페이지에서 F12로 정확한 class를 확인해서
      TITLE_SELECTORS / BODY_SELECTORS 맨 앞에 추가해주면 된다.
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BLOG_URL = "https://gexoption.blogspot.com/"
VIEWPORT = {"width": 1080, "height": 1920}
OUTPUT_ROOT = Path("./captures")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("blog_fetch")

# Blogger 기본/인기 테마들이 흔히 쓰는 클래스명 후보 (위에서부터 순서대로 시도)
TITLE_SELECTORS = [
    "h3.post-title",
    "h2.post-title",
    ".post-title",
    "article h1",
    "h1.entry-title",
]
BODY_SELECTORS = [
    ".post-body",
    ".entry-content",
    "article .post-body",
    "div.post-body",
]


def find_first_matching(page, selectors: list[str]):
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            return loc, sel
    return None, None


def main() -> int:
    today = datetime.now().strftime("%Y%m%d")
    out_dir = OUTPUT_ROOT / today
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        log.info("블로그 접속 중... (%s)", BLOG_URL)
        page.goto(BLOG_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        title_loc, title_sel = find_first_matching(page, TITLE_SELECTORS)
        body_loc, body_sel = find_first_matching(page, BODY_SELECTORS)

        if body_loc is None:
            log.error(
                "블로그 본문을 못 찾았습니다. 시도한 셀렉터: %s -> 전부 실패. "
                "블로그 페이지에서 F12로 본문 class를 확인해서 BODY_SELECTORS에 추가해주세요.",
                BODY_SELECTORS,
            )
            browser.close()
            return 1

        title_text = title_loc.inner_text().strip() if title_loc else ""
        body_text = body_loc.inner_text().strip()

        log.info("제목 셀렉터 '%s' / 본문 셀렉터 '%s' 로 찾음", title_sel, body_sel)

        # 1) 본문 텍스트 저장 (나레이션 대본 재료)
        text_path = out_dir / "blog_post.txt"
        text_path.write_text(f"{title_text}\n\n{body_text}", encoding="utf-8")
        log.info("블로그 본문 텍스트 저장 완료 -> %s (%d자)", text_path, len(body_text))

        # 2) 스크린샷 저장 (파일명은 compose_video.py가 기대하는 이름과 동일하게 유지)
        shot_target = title_loc if title_loc else body_loc
        shot_target.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(300)

        img_path = out_dir / "gamma_briefing.png"
        # 제목부터 본문 일부까지 자연스럽게 보이도록, 본문 요소를 캡처 대상으로 사용
        body_loc.screenshot(path=str(img_path))
        log.info("블로그 스크린샷 저장 완료 -> %s", img_path)

        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
