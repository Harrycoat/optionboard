"""
gexoption.com -> YouTube Shorts 캡처 스크립트
==============================================

역할: gexoption.com의 특정 섹션들을 세로(9:16) 스크린샷으로 자동 캡처해서
      /captures/YYYYMMDD/ 폴더에 저장.
      나중에 FFmpeg 합성 단계에서 이 이미지들을 그대로 사용.

실행 전 설치 (로컬/서버에서 한 번만):
    pip install playwright
    playwright install chromium --with-deps

실행:
    python capture_gexoption.py

cron 등록 예 (매일 장마감 후, 예: 오후 4:10 PT):
    10 16 * * 1-5 /usr/bin/python3 /path/to/capture_gexoption.py >> /path/to/capture.log 2>&1
"""

import sys
import time
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

SITE_URL = "https://gexoption.com"

# 세로 영상용 뷰포트 (9:16 비율에 맞춤)
VIEWPORT = {"width": 1080, "height": 1920}

# 캡처할 섹션들.
# selector는 실제 gexoption.com의 개발자도구(F12 > Elements)에서
# 각 카드를 감싸는 실제 div의 class/id를 확인해서 채워 넣어야 합니다.
# 지금은 "헤딩 텍스트로 찾기" 방식(TEXT_ANCHOR)을 기본값으로 두었고,
# 실제 class를 알게 되면 CSS_SELECTOR 쪽을 채우고 그걸 우선 사용하도록 되어 있습니다.
SECTIONS = [
    {
        "name": "gamma_briefing",       # 파일명에 쓰일 식별자
        "text_anchor": "오늘의 감마 브리핑",  # 이 텍스트가 포함된 카드를 찾음
        "css_selector": None,            # 알게 되면 여기에 넣기 (우선순위 높음)
    },
    {
        "name": "top_movers",
        "text_anchor": "오늘의 급등주 Top 10",
        "css_selector": None,
    },
    {
        "name": "buy_signal",
        "text_anchor": "오늘의 매수 신호",
        "css_selector": None,
    },
    {
        "name": "leading_stocks",
        "text_anchor": "오늘의 주도주",
        "css_selector": None,
    },
    {
        "name": "etf_leaders",
        "text_anchor": "ETF 마켓 리더",
        "css_selector": None,
    },
]

# "불러오는 중" 같은 로딩 텍스트가 사라질 때까지 최대 대기 시간(초)
MAX_WAIT_SECONDS = 25

OUTPUT_ROOT = Path("./captures")

# ---------------------------------------------------------------------------
# 로깅
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("gex_capture")


def wait_for_data_loaded(page, timeout_seconds: int = MAX_WAIT_SECONDS) -> None:
    """
    페이지 전체에 '불러오는 중' 텍스트가 하나도 안 남을 때까지 대기.
    비동기 위젯이 많은 사이트라 단순 networkidle보다 이 방식이 더 안정적.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        remaining = page.locator("text=불러오는 중").count()
        if remaining == 0:
            return
        time.sleep(0.5)
    log.warning("일부 섹션이 %d초 안에 로딩을 못 마쳤어요. 남은 상태로 진행합니다.", timeout_seconds)


def find_section_locator(page, section: dict):
    """
    css_selector가 채워져 있으면 그걸 우선 사용하고,
    없으면 text_anchor 텍스트를 포함하는 카드의 '조상 컨테이너'를 추정해서 반환.
    """
    if section.get("css_selector"):
        loc = page.locator(section["css_selector"]).first
        if loc.count() > 0:
            return loc

    # 텍스트 기반 fallback: 헤딩 텍스트를 찾고, 카드 전체가 보이도록
    # 조상 요소 중 적당히 큰 블록(section/div)을 선택.
    # xpath로 "이 텍스트를 포함하는 가장 가까운 section 또는 div 3단계 위"를 시도.
    heading = page.get_by_text(section["text_anchor"], exact=False).first
    if heading.count() == 0:
        return None

    # 후보 컨테이너: section > div > div 순으로 올라가며 가장 먼저 잡히는 큰 블록 사용
    container = heading.locator(
        "xpath=ancestor::section[1] | ancestor::div[contains(@class,'card')][1] | ancestor::div[3]"
    ).first
    return container if container.count() > 0 else heading


def capture_section(page, section: dict, out_dir: Path) -> bool:
    name = section["name"]
    try:
        loc = find_section_locator(page, section)
        if loc is None:
            log.error("[%s] 섹션을 찾지 못했습니다 (텍스트: %s)", name, section["text_anchor"])
            return False

        loc.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(400)  # 스크롤 후 렌더링 안정화 대기

        out_path = out_dir / f"{name}.png"
        loc.screenshot(path=str(out_path))
        log.info("[%s] 캡처 완료 -> %s", name, out_path)
        return True

    except PWTimeout:
        log.error("[%s] 타임아웃으로 캡처 실패", name)
        return False
    except Exception as e:  # noqa: BLE001
        log.error("[%s] 캡처 중 오류: %s", name, e)
        return False


def main() -> int:
    today = datetime.now().strftime("%Y%m%d")
    out_dir = OUTPUT_ROOT / today
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        log.info("gexoption.com 접속 중...")
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=30000)

        # 검색창 등 최초 레이아웃이 자리 잡을 시간
        page.wait_for_timeout(1500)

        log.info("비동기 데이터 로딩 대기 중...")
        wait_for_data_loaded(page)

        for section in SECTIONS:
            results[section["name"]] = capture_section(page, section, out_dir)

        browser.close()

    ok = sum(1 for v in results.values() if v)
    fail = len(results) - ok
    log.info("완료: 성공 %d개 / 실패 %d개 (저장 위치: %s)", ok, fail, out_dir)

    if fail:
        failed_names = [k for k, v in results.items() if not v]
        log.warning("실패한 섹션: %s -> 오늘 영상 제작 전에 수동 확인 필요", ", ".join(failed_names))

    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
