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
SECTIONS = [
    {
        "name": "gamma_briefing",
        "text_anchor": "오늘의 감마 브리핑",
        "css_selector": None,
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


# 카드 전체로 인정하기 위한 최소 높이(px). 제목 줄만 있는 상태는 보통
# 30~50px 밖에 안 되고, 실제 데이터가 들어간 카드는 이보다 훨씬 크다.
MIN_CARD_HEIGHT_PX = 250
# 너무 위로 올라가서 페이지 전체를 잡아버리는 것을 막기 위한 안전장치
MAX_CLIMB_LEVELS = 10


def find_section_locator(page, section: dict):
    """
    css_selector가 채워져 있으면 그걸 우선 사용하고,
    없으면 text_anchor 텍스트가 있는 위치에서 시작해서, 실제 카드 크기
    (MIN_CARD_HEIGHT_PX 이상)가 될 때까지 부모 요소로 계속 올라가며 찾는다.
    """
    if section.get("css_selector"):
        loc = page.locator(section["css_selector"]).first
        if loc.count() > 0:
            return loc

    heading = page.get_by_text(section["text_anchor"], exact=False).first
    if heading.count() == 0:
        return None

    handle = heading.element_handle()
    if handle is None:
        return heading

    grown_handle = page.evaluate_handle(
        """([el, minHeight, maxLevels]) => {
            let node = el;
            let levels = 0;
            while (
                node &&
                node.parentElement &&
                node.offsetHeight < minHeight &&
                levels < maxLevels
            ) {
                node = node.parentElement;
                levels += 1;
            }
            return node;
        }""",
        [handle, MIN_CARD_HEIGHT_PX, MAX_CLIMB_LEVELS],
    )
    element = grown_handle.as_element()
    return element if element is not None else heading


def capture_section(page, section: dict, out_dir: Path) -> bool:
    name = section["name"]
    try:
        loc = find_section_locator(page, section)
        if loc is None:
            log.error("[%s] 섹션을 찾지 못했습니다 (텍스트: %s)", name, section["text_anchor"])
            return False

        loc.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(400)

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
