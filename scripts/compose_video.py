"""
compose_video.py
=================

역할: 캡처 이미지(capture_gexoption.py) + 나레이션 음성(tts_convert.py) +
      대본 줄(build_shorts_script.py의 *_script_lines.json)을 합쳐서
      9:16 세로 쇼츠 영상 하나로 렌더링.

전제 파일 (같은 captures/YYYYMMDD/ 폴더 안에 이미 있어야 함):
  - gamma_briefing.png, top_movers.png, buy_signal.png,
    leading_stocks.png, etf_leaders.png   (capture_gexoption.py 결과)
  - {ticker}_narration.mp3                (tts_convert.py 결과)
  - {ticker}_script_lines.json            (build_shorts_script.py 결과)

사용법:
    python scripts/compose_video.py AAPL

동작:
  1. 나레이션 mp3 길이를 ffprobe로 측정
  2. 대본 줄들을 글자수 비율로 나눠서 자막 타이밍(.srt) 생성
  3. 캡처 이미지 5장을 순서대로 동일한 길이씩 이어붙인 세로(1080x1920) 슬라이드쇼 제작
     (원본 캡처 비율을 유지한 채 레터박스 처리 — 배경은 채널 브랜딩과 맞춘 남색)
  4. 슬라이드쇼 + 나레이션 음성 + 자막을 하나의 mp4로 합성
  5. captures/YYYYMMDD/{ticker}_short.mp4 로 저장

필요: 시스템에 ffmpeg / ffprobe 설치되어 있어야 함 (brew install ffmpeg 또는
      https://ffmpeg.org/download.html 에서 설치, Windows는 PATH 등록 필요)
"""

import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent

# 캡처 이미지 순서 (capture_gexoption.py의 SECTIONS 이름과 일치해야 함)
IMAGE_ORDER = [
    "gamma_briefing.png",
    "top_movers.png",
    "buy_signal.png",
    "leading_stocks.png",
    "etf_leaders.png",
]

# 영상 규격 (유튜브 쇼츠 기본)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

# 레터박스 배경색 (채널 브랜딩 남색, #0B1220 -> ffmpeg는 0xRRGGBB 형식)
BG_COLOR = "0x0B1220"


def run(cmd: list[str]) -> None:
    """subprocess로 외부 명령 실행, 실패하면 에러 메시지와 함께 예외 발생."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"명령 실패: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def get_audio_duration(mp3_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(mp3_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"오디오 길이 측정 실패: {result.stderr}")
    return float(result.stdout.strip())


def format_srt_timestamp(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(lines: list[str], total_duration: float, out_path: Path) -> None:
    """
    대본 줄들의 글자수 비율로 자막 타이밍을 나눠서 .srt 파일을 만든다.
    (실제 TTS 발화 속도와 100% 일치하진 않지만, 문장 길이에 비례하므로
     대부분의 경우 충분히 자연스럽게 맞아떨어진다.)
    """
    total_chars = sum(len(line) for line in lines) or 1
    entries = []
    cursor = 0.0
    for line in lines:
        share = len(line) / total_chars
        duration = total_duration * share
        start, end = cursor, cursor + duration
        entries.append((start, end, line))
        cursor = end
    # 마지막 자막이 오디오 끝과 정확히 맞도록 보정
    if entries:
        last_start, _, last_text = entries[-1]
        entries[-1] = (last_start, total_duration, last_text)

    srt_lines = []
    for i, (start, end, text) in enumerate(entries, start=1):
        srt_lines.append(str(i))
        srt_lines.append(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}")
        srt_lines.append(text)
        srt_lines.append("")
    out_path.write_text("\n".join(srt_lines), encoding="utf-8")


def build_slideshow(images: list[Path], total_duration: float, out_path: Path) -> None:
    """
    이미지들을 동일한 길이씩 이어붙인 무음 세로 슬라이드쇼(mp4)를 만든다.
    각 이미지는 원본 비율 유지 + 레터박스로 1080x1920에 맞춘다.
    """
    per_image = total_duration / len(images)

    concat_list_path = out_path.parent / "_concat_list.txt"
    lines = []
    for img in images:
        lines.append(f"file '{img.as_posix()}'")
        lines.append(f"duration {per_image:.3f}")
    # concat demuxer는 마지막 파일의 duration을 무시하므로, 마지막 파일을 한 번 더 반복
    lines.append(f"file '{images[-1].as_posix()}'")
    concat_list_path.write_text("\n".join(lines), encoding="utf-8")

    vf = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color={BG_COLOR},"
        f"format=yuv420p"
    )

    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
        "-vf", vf,
        "-r", str(FPS),
        str(out_path),
    ])

    concat_list_path.unlink(missing_ok=True)


def mux_final_video_safe(slideshow_path: Path, audio_path: Path, srt_path: Path, out_path: Path) -> None:
    """무음 슬라이드쇼 + 나레이션 음성 + 자막(번인)을 최종 mp4로 합성.

    ffmpeg subtitles 필터는 콜론(:)이 포함된 경로(Windows의 C:\\...)에서
    이스케이프가 까다로워서, 작업 디렉터리를 srt 파일 위치로 옮기고
    파일명만 상대경로로 넘기는 방식으로 우회한다.
    """
    cwd = srt_path.parent
    srt_name = srt_path.name
    cmd = [
        "ffmpeg", "-y",
        "-i", str(slideshow_path.resolve()),
        "-i", str(audio_path.resolve()),
        "-vf", (
            f"subtitles={srt_name}:force_style="
            "'FontSize=15,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,"
            "BorderStyle=3,Outline=2,Alignment=2,MarginV=120'"
        ),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-shortest",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd))
    if result.returncode != 0:
        raise RuntimeError(f"최종 합성 실패:\n{result.stderr[-2000:]}")


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/compose_video.py 티커  (예: AAPL)")
        return 1

    ticker = sys.argv[1].upper().strip()
    today = datetime.now().strftime("%Y%m%d")
    out_dir = REPO_ROOT / "captures" / today

    # 1) 필요한 파일들 확인
    images = [out_dir / name for name in IMAGE_ORDER]
    missing_images = [str(p) for p in images if not p.exists()]
    if missing_images:
        print("[오류] 캡처 이미지가 없습니다:")
        for m in missing_images:
            print(f"  - {m}")
        print("먼저 capture_gexoption.py를 실행해주세요.")
        return 1

    audio_path = out_dir / f"{ticker}_narration.mp3"
    if not audio_path.exists():
        print(f"[오류] 나레이션 파일이 없습니다: {audio_path}")
        print("먼저 tts_convert.py를 실행해주세요.")
        return 1

    lines_json_path = out_dir / f"{ticker}_script_lines.json"
    if not lines_json_path.exists():
        print(f"[오류] 대본 줄 파일이 없습니다: {lines_json_path}")
        print("먼저 build_shorts_script.py를 실행해주세요.")
        return 1

    lines_data = json.loads(lines_json_path.read_text(encoding="utf-8"))
    lines = lines_data.get("lines", [])
    if not lines:
        print(f"[오류] {lines_json_path}에 대본 줄이 없습니다.")
        return 1

    # 2) 오디오 길이 측정
    print("[진행] 나레이션 길이 측정 중...")
    duration = get_audio_duration(audio_path)
    print(f"  나레이션 길이: {duration:.1f}초")

    # 3) 자막(.srt) 생성
    srt_path = out_dir / f"{ticker}_subtitles.srt"
    build_srt(lines, duration, srt_path)
    print(f"[진행] 자막 생성 완료: {srt_path}")

    # 4) 무음 슬라이드쇼 생성
    slideshow_path = out_dir / f"_{ticker}_slideshow.mp4"
    print("[진행] 슬라이드쇼(무음) 생성 중...")
    build_slideshow(images, duration, slideshow_path)

    # 5) 최종 합성 (영상 + 음성 + 자막)
    final_path = out_dir / f"{ticker}_short.mp4"
    print("[진행] 최종 영상 합성 중 (음성 + 자막)...")
    mux_final_video_safe(slideshow_path, audio_path, srt_path, final_path)

    slideshow_path.unlink(missing_ok=True)

    print(f"\n[완료] 최종 쇼츠 영상: {final_path}")
    print("업로드 전에 반드시 재생해서 확인하세요 (숫자/티커 오류, 자막 타이밍 등).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
