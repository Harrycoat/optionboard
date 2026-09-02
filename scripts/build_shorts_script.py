"""
build_shorts_script.py
=======================

역할: options_engine.analyze_ticker_cached()가 만드는 감마봇 텍스트
      (build_narrative()의 결과, HTML 태그 포함)를 가져와서
      TTS(음성합성)와 자막에 바로 쓸 수 있는 "순수 텍스트" 대본으로 변환.

입력: 티커 1개 (오늘 쇼츠에서 다룰 종목 — 예: 오늘의 주도주 1위 종목)
출력: captures/YYYYMMDD/{ticker}_script.txt        <- TTS에 그대로 넣을 전체 대본
      captures/YYYYMMDD/{ticker}_script_lines.json  <- 줄 단위 (자막 타이밍/캡션용)

사용법:
    python scripts/build_shorts_script.py AAPL

전제:
    - 로컬 실행 시 MASSIVE_API_KEY 환경변수가 설정되어 있어야 함
      (Windows: set MASSIVE_API_KEY=본인키 / Mac-Linux: export MASSIVE_API_KEY=본인키)
    - api/options_engine.py가 같은 저장소 안에 있어야 함 (경로 자동 인식)
"""

import sys
import re
import json
import html
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# api/options_engine.py를 import하기 위한 경로 설정
# (scripts/ 폴더에서 실행해도, 저장소 루트에서 실행해도 둘 다 되도록)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "api"))

try:
    from api.options_engine import analyze_ticker_cached
except ImportError:
    # api/ 폴더가 패키지(__init__.py)가 아닌 경우를 대비한 폴백
    from options_engine import analyze_ticker_cached  # type: ignore


# ---------------------------------------------------------------------------
# 채널 인트로 / 아웃트로 (감마봇 텍스트 앞뒤에 붙임)
# ---------------------------------------------------------------------------

INTRO_TEMPLATE = "안녕하세요, GEX옵션 감마봇 데일리입니다. 오늘은 {ticker} 감마 브리핑으로 시작할게요."
OUTRO_TEXT = "더 자세한 종목별 데이터는 gexoption.com에서 실시간으로 확인하실 수 있습니다."


def strip_html_to_plain_text(raw: str) -> str:
    """
    build_narrative()가 만드는 문장 하나에서 HTML 태그/엔티티를 제거하고
    TTS가 자연스럽게 읽을 수 있는 순수 텍스트로 바꾼다.

    처리하는 것들:
      - <b>...</b> 같은 태그 전부 제거 (내용은 남김)
      - &nbsp; 같은 HTML 엔티티를 실제 문자(공백 등)로 변환
      - 글머리 기호(•)는 TTS에서 어색하니 쉼표로 치환
      - 연속 공백 정리
    """
    text = html.unescape(raw)          # &nbsp; -> 실제 공백, &amp; -> & 등
    text = re.sub(r"<[^>]+>", "", text)  # <b>, </b> 등 태그 제거
    text = text.replace("•", "")          # 글머리 기호 제거 (음성으로 읽기 어색함)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_script_lines(ticker: str) -> list[str]:
    """
    티커 하나를 분석해서, 인트로 -> 감마봇 나레이션(정리된 문장들) -> 아웃트로
    순서의 대본 줄 리스트를 반환한다.
    """
    result = analyze_ticker_cached(ticker, skip_stage=True)
    raw_narrative = result.get("narrative", [])

    lines: list[str] = [INTRO_TEMPLATE.format(ticker=ticker)]
    for raw_line in raw_narrative:
        clean = strip_html_to_plain_text(raw_line)
        if clean:
            lines.append(clean)
    lines.append(OUTRO_TEXT)

    return lines


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/build_shorts_script.py 티커  (예: AAPL)")
        return 1

    ticker = sys.argv[1].upper().strip()
    today = datetime.now().strftime("%Y%m%d")
    out_dir = REPO_ROOT / "captures" / today
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        lines = build_script_lines(ticker)
    except Exception as e:  # noqa: BLE001
        print(f"[오류] {ticker} 분석 실패: {e}")
        print("MASSIVE_API_KEY 환경변수가 설정되어 있는지 확인해주세요.")
        return 1

    # 1) TTS용 전체 대본 (한 파일에 줄바꿈으로 이어붙임 -> TTS 엔진에 그대로 붙여넣기)
    script_txt_path = out_dir / f"{ticker}_script.txt"
    script_txt_path.write_text("\n".join(lines), encoding="utf-8")

    # 2) 자막/타이밍용 줄 단위 JSON (나중에 FFmpeg 자막 타이밍 맞출 때 사용)
    script_json_path = out_dir / f"{ticker}_script_lines.json"
    script_json_path.write_text(
        json.dumps({"ticker": ticker, "lines": lines}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[완료] {ticker} 대본 생성:")
    print(f"  - {script_txt_path}")
    print(f"  - {script_json_path}")
    print("\n--- 대본 미리보기 ---")
    for line in lines:
        print(f"  {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
