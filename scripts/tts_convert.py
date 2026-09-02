"""
tts_convert.py
==============

역할: build_shorts_script.py가 만든 나레이션 대본(captures/YYYYMMDD/{ticker}_script.txt)을
      Google Cloud Text-to-Speech REST API로 mp3 음성 파일로 변환.

인증 방식: 서비스 계정 JSON 키가 아니라 "API 키" 방식 사용
  (조직 정책으로 서비스 계정 키 생성이 막혀 있어서 더 간단한 API 키로 전환함)

필요 환경변수:
  GOOGLE_TTS_API_KEY   <- Google Cloud Console에서 발급받은 API 키
    Windows(cmd):        set GOOGLE_TTS_API_KEY=본인키
    Windows(PowerShell):  $env:GOOGLE_TTS_API_KEY="본인키"
    Mac/Linux:            export GOOGLE_TTS_API_KEY=본인키

  ⚠️ 절대 이 키를 코드에 직접 적어넣거나, GitHub에 올리거나, 채팅/메신저로
     공유하지 마세요. 환경변수로만 전달합니다.

사용법:
    python scripts/tts_convert.py AAPL

동작:
  1. captures/오늘날짜/AAPL_script.txt 를 읽음
  2. Google Cloud TTS REST API(text:synthesize)를 호출해서 음성 생성
  3. captures/오늘날짜/AAPL_narration.mp3 로 저장

목소리 커스터마이징:
  VOICE_NAME 값을 바꾸면 다른 목소리로 바뀝니다. 예:
    ko-KR-Neural2-A (여성, 자연스러움)   ko-KR-Neural2-C (남성)
    ko-KR-Wavenet-A (여성)               ko-KR-Wavenet-C (남성)
  Google Cloud 콘솔의 Text-to-Speech 데모 페이지에서 먼저 들어보고 고르세요.
"""

import os
import sys
import json
import base64
from pathlib import Path
from datetime import datetime

import requests

TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

# ---------------------------------------------------------------------------
# 목소리 설정 (원하는 대로 바꿔서 쓰세요)
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "ko-KR"
VOICE_NAME = "ko-KR-Neural2-A"   # 자연스러운 여성 목소리 (원하면 교체 가능)
SPEAKING_RATE = 1.0               # 1.0 = 기본 속도, 0.9면 살짝 느리게
PITCH = 0.0                       # 음높이 조절 (보통 0.0 그대로 둠

REPO_ROOT = Path(__file__).resolve().parent.parent


def synthesize_speech(text: str, api_key: str) -> bytes:
    """
    Google Cloud Text-to-Speech REST API를 호출해서 mp3 바이트를 반환한다.
    긴 텍스트(약 5000바이트 이상)는 API 제한에 걸릴 수 있는데,
    쇼츠 나레이션 분량(수백 자 수준)이면 문제없다.
    """
    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": LANGUAGE_CODE,
            "name": VOICE_NAME,
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": SPEAKING_RATE,
            "pitch": PITCH,
        },
    }

    resp = requests.post(
        TTS_ENDPOINT,
        params={"key": api_key},
        json=payload,
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"TTS API 오류 ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    audio_b64 = data.get("audioContent")
    if not audio_b64:
        raise RuntimeError(f"TTS 응답에 audioContent가 없습니다: {data}")

    return base64.b64decode(audio_b64)


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/tts_convert.py 티커  (예: AAPL)")
        return 1

    ticker = sys.argv[1].upper().strip()

    api_key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not api_key:
        print("[오류] GOOGLE_TTS_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  Windows(cmd):       set GOOGLE_TTS_API_KEY=본인키")
        print("  Windows(PowerShell): $env:GOOGLE_TTS_API_KEY=\"본인키\"")
        return 1

    today = datetime.now().strftime("%Y%m%d")
    out_dir = REPO_ROOT / "captures" / today

    script_path = out_dir / f"{ticker}_script.txt"
    if not script_path.exists():
        print(f"[오류] 대본 파일을 찾을 수 없습니다: {script_path}")
        print("먼저 build_shorts_script.py를 실행해서 대본을 만들어주세요.")
        return 1

    text = script_path.read_text(encoding="utf-8")
    # 파일 안 줄바꿈은 문장 구분용이라, TTS에 넣을 땐 자연스러운 끊어읽기를 위해
    # 줄바꿈을 마침표+공백 정도의 pause로 남겨둔 채 하나의 문단으로 합친다.
    joined_text = " ".join(line.strip() for line in text.splitlines() if line.strip())

    print(f"[진행] {ticker} 나레이션 음성 생성 중... (목소리: {VOICE_NAME})")

    try:
        audio_bytes = synthesize_speech(joined_text, api_key)
    except Exception as e:  # noqa: BLE001
        print(f"[오류] TTS 변환 실패: {e}")
        return 1

    mp3_path = out_dir / f"{ticker}_narration.mp3"
    mp3_path.write_bytes(audio_bytes)

    print(f"[완료] 음성 파일 저장: {mp3_path}")
    print(f"  파일 크기: {len(audio_bytes):,} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
