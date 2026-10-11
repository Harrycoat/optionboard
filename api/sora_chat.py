"""Private educational Sora chat: server-verified delayed OHLCV and one versioned lesson."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime, timezone
import json, os, re, sys
import requests

sys.path.insert(0, os.path.dirname(__file__))
from swing_access import require_access

LESSON = {
    "title": "눌림목의 세 단계: 멈춤 → 저점 유지 확인 → 재상승",
    "version": "2026-10-10.v1",
    "source": "GEXOption 볼륨 발자국 트레이딩 교육 초안 (검증 필요)",
    "text": (
        "1. 멈춤: 이전 하락이 둔화하며 기준봉을 식별한다. 기준봉 정의와 확정은 실전 차트에서 확인한다. "
        "2. 확인: 다음 봉이 기준봉 저점을 지키는지 관찰한다. 저점 유지 만으로는 매수 신호가 아니다. "
        "3. 재상승: 다음 봉이 앞 기준봉 고점을 돌파하는지, 완료봉인지 확인한다. "
        "돌파 실패 또는 기준 저점 이탈 시 시나리오가 무효가 될 수 있다. "
        "손절 위치는 기준봉/확인봉의 실제 저점과 위험 한도를 종합해 결정하며 자동 주문하지 않는다. "
        "Smooth 상승은 수급 확인의 보조 조건이지만 이 앱에서는 TOS 원본 Smooth가 제공되지 않는다. "
        "EVP 추정 지표는 TOS Net Delta/Smooth로 대체할 수 없다. "
        "확정되지 않은 수치 공식, 성공률 및 수익률을 단정하지 않는다."
    ),
}

SYSTEM = """당신은 GEXOption의 교육용 주식 분석 선생님 '소라'입니다. 사용자가 선택한 응답 언어에 따라 한국어 또는 영어로 설명합니다.
사용자에게 실제 주문 또는 수익 보장을 제안하지 않습니다.
서버가 제공한 봉 데이터와 교육 자료만 사실 근거로 사용합니다.
TradingView 내장 차트와 서버의 지연 봉 데이터는 반드시 같은 시점이 아닙니다.
봉 시간은 시작 시각이고, 실시간 현재가가 아니라 마지막 완료봉 종가입니다.
데이터가 없거나 오래되면 가격·거래량·Smooth·GEX Call/Put Wall을 추측하지 않습니다.
EVP는 추정 지표이며 TOS Net Delta/Smooth와 동등하지 않습니다.
'성립 조건', '실패 조건', '확인되지 않은 사실'을 구별합니다.
교육 자료에 없는 정확한 공식·승률은 모른다고 답합니다.
답변에는 종목, 시간봉, 최신 봉 시작(UTC)과 데이터 상태, 참고 교육자료 제목 및 버전을 표시합니다.
"""

def market_context(ticker, tf):
    # Server-side acquisition prevents users from supplying invented chart figures.
    from swing_radar_chart import VALID_TF, aggregates, indicators
    if tf not in VALID_TF:
        raise ValueError("UNSUPPORTED_TIMEFRAME")
    rows = aggregates(ticker, tf, True)
    done = [r for r in rows if r.get("complete") and r.get("coverage_ok", True)]
    bars = indicators(done)
    if not bars:
        raise ValueError("NO_COMPLETED_BARS")
    safe = []
    for b in bars[-12:]:
        row = {k: b.get(k) for k in ("t", "o", "h", "l", "c", "v", "hull20", "evp") if b.get(k) is not None}
        safe.append(row)
    last = safe[-1]
    stamp = datetime.fromtimestamp(last["t"] / 1000, timezone.utc)
    age_minutes = max(0, (datetime.now(timezone.utc) - stamp).total_seconds() / 60)
    return {
        "ticker": ticker, "tf": tf, "bars": safe,
        "last_bar_utc": stamp.isoformat(),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "timezone": "America/New_York (차트 기준), UTC (전달시각)",
        "price_label": "마지막 완료봉 종가 (실시간 현재가 아님)",
        "data_status": "STALE" if age_minutes > (4320 if tf == "1d" else 120) else "DELAYED",
        "source": "Massive 15분 지연 OHLCV 집계",
        "unavailable": ["실시간 현재가", "TOS Smooth", "TOS Net Delta", "Call Wall", "Put Wall"],
    }

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if urlparse(self.path).path != "/api/sora_chat":
            self.send_error(404)
            return
        if not require_access(self):
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 1 <= n <= 12000:
                raise ValueError("SIZE")
            req = json.loads(self.rfile.read(n))
            ticker = str(req.get("ticker", "")).upper()
            tf = str(req.get("tf", "")).lower()
            question = req.get("message")
            if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", ticker):
                raise ValueError("TICKER")
            if tf not in ("1d", "30m", "15m", "5m"):
                raise ValueError("TF")
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1200:
                raise ValueError("QUESTION")
            language = str(req.get("language", "auto")).lower()
            if language not in ("auto", "ko", "en"):
                raise ValueError("LANGUAGE")
            history = req.get("history", [])
            if not isinstance(history, list) or len(history) > 8:
                raise ValueError("HISTORY")
            turns = []
            for h in history:
                if not isinstance(h, dict) or h.get("role") not in ("user", "assistant") or not isinstance(h.get("content"), str) or len(h["content"]) > 1200:
                    raise ValueError("HISTORY_TURN")
                turns.append({"role": h["role"], "content": h["content"]})
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            self.reply(400, {"error": "종목·시간봉·질문 형식을 확인해 주세요.", "kind": "INVALID_INPUT"})
            return
        key = os.getenv("SORA_GEMINI_API_KEY", "").strip()
        model = os.getenv("SORA_GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
        if not key:
            self.reply(503, {"error": "AI 서버 설정이 없습니다. SORA_GEMINI_API_KEY를 서버에서 확인해 주세요.", "kind": "MODEL_NOT_CONFIGURED"})
            return
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,60}", model):
            self.reply(503, {"error": "AI 모델 설정이 올바르지 않습니다.", "kind": "MODEL_INVALID"})
            return
        try:
            market = market_context(ticker, tf)
        except Exception:
            self.reply(503, {"error": ticker + " " + tf + " 완료봉 데이터를 확인하지 못했습니다. 제공자 연결·인증·지연을 점검하세요.", "kind": "MARKET_DATA_UNAVAILABLE", "ticker": ticker, "tf": tf})
            return
        context = json.dumps({"market": market, "lesson": LESSON}, ensure_ascii=False, separators=(",", ":"))
        dialogue = "\n".join(("사용자" if h["role"] == "user" else "소라") + ": " + h["content"] for h in turns)
        language_instruction = {"ko": "Respond in Korean.", "en": "Respond in English.", "auto": "Reply in the predominant language of the latest user question (Korean or English)."}[language]
        prompt = language_instruction + "\nVerified context: " + context + "\nConversation:\n" + dialogue + "\nUser question: " + question
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent"
        try:
            r = requests.post(
                url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                      "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": 900, "temperature": 0.2}},
                timeout=(4, 22),
            )
            if r.status_code in (401, 403):
                self.reply(502, {"error": "AI 모델 인증 또는 권한 오류입니다.", "kind": "MODEL_AUTH"})
                return
            if r.status_code == 429:
                self.reply(429, {"error": "AI 사용량 한도에 도달했습니다. 잠시 뒤 다시 요청하세요.", "kind": "MODEL_RATE_LIMIT"})
                return
            if r.status_code != 200:
                self.reply(502, {"error": "AI 모델 제공자 응답 오류 (" + str(r.status_code) + ").", "kind": "MODEL_PROVIDER_ERROR"})
                return
            result = r.json()
            answer = "".join(p.get("text", "") for item in result.get("candidates", [])[:1] for p in item.get("content", {}).get("parts", []) if isinstance(p, dict)).strip()
            if not answer:
                self.reply(502, {"error": "AI에서 빈 답변이 반환됐습니다.", "kind": "MODEL_EMPTY"})
                return
            self.reply(200, {"ticker": ticker, "tf": tf, "answer": answer,
                             "market": {k: market[k] for k in ("ticker", "tf", "last_bar_utc", "retrieved_at_utc", "timezone", "price_label", "data_status", "source")},
                             "lesson": {"title": LESSON["title"], "version": LESSON["version"]}, "language": language})
        except requests.exceptions.Timeout:
            self.reply(504, {"error": "AI 응답 시간이 초과되었습니다.", "kind": "MODEL_TIMEOUT"})
        except requests.exceptions.RequestException:
            self.reply(502, {"error": "AI 제공자 네트워크 연결에 실패했습니다.", "kind": "MODEL_NETWORK"})
        except (ValueError, TypeError, KeyError):
            self.reply(502, {"error": "AI 응답 형식을 해석하지 못했습니다.", "kind": "MODEL_FORMAT"})

    def reply(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
