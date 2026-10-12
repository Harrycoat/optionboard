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

CORE_CRITERIA = {
    "title": "소라 핵심 교육 기준 초안",
    "version": "v0.1",
    "status": "DRAFT_NOT_APPROVED",
    "reference": "docs/sora-core-education-v0.1.md",
    "candidate": "상대강도 약 90(지표 미정), 상승하는 일봉 50MA 위, 선행 상승 후 눌림",
    "stock_entry": "스윙 30m/단타 5m: Hull21 밴드 재진입 → Put Wall 지지, 매수세 > 50, Smooth 상방 전환을 각각 확인",
    "protective_put": "Call Wall 저항, 매수세 < 50, Smooth 하방 전환, 마지막 상승봉 정체 → 음봉 장악 → 다음 음봉 Hull21 밴드 진입",
    "unavailable_in_current_feed": ["상대강도 공식", "일봉 50MA 기울기 판정", "Hull21 밴드", "매수세", "TOS Smooth", "Put Wall", "Call Wall", "옵션 체인과 실제 보호 풋 손익"],
    "note": "미확정 계산식, 장악 범위, 손절/옵션 계약 선택을 임의로 확정하지 않음. Hull20과 EVP는 Hull21 밴드와 TOS Smooth가 아님."
}

VF02 = {
    "title": "볼륨 발자국 교육 기준",
    "version": "VF-0.2",
    "status": "OWNER_APPROVED_EDUCATION_REFERENCE",
    "reference": "docs/sora-volume-footprint-v0.2.md",
    "rules": """R01 거래량 우선, 매수/매도 힘의 7단계 순환(고정 법칙 아님).
R02 발자국=행사가별 Call/Put OI 집중; OI는 옵션 거래량과 다름, 보유자 방향 추정 금지.
R03 후보 RS 약90, 85~89 허용 가능(85 절대 하한 아님); 상승하는 일봉 SMA50 위, 선행 상승 후 눌림.
R04 매도세 진정→매수 유입→Smooth 상방→Put Wall 지지+매수압력 >50→마지막 Hull21 밴드 재진입. 스윙30m/단타5m.
R05 Call Wall 저항에서 매수세 약화·매도세 심화 등 조건 시 보호 PUT BUY TO OPEN, 주식 유지. 3봉/Hull 재진입 의무 대기 폐기. Smooth 하락과 압력 <50을 새 필수 조건으로 만들지 않음.
R06 큰 거래량 Call Wall 상향 돌파 시 보유 PUT SELL TO CLOSE, 주식 유지.
R07 상승 전환 조건 회복 시 보유 PUT SELL TO CLOSE; 비용 차감 실현 순이익이 있다면 증주 검토.
R08 섹터 리더 동반 약세+매수 약화+매도 확대+Smooth 노랑→빨강이면 주식 청산/익절 검토; SMA50 이탈 의무 아님.
R09 델타 0.30 표준 숏콜1계약 단독 가상헤지 약30주(고정 100주 아님), 순딜러포지션 미확인.
R10 미확정 공식·시간·지표·계약 조건은 단정 금지.""",
    "note": "기초 v0.1은 보존. 현재 실전 판단에서는 충돌하는 v0.1 규칙 대신 VF-0.2 우선. 교관용 정답표는 별도 보관."
}

GUIDED_CASE = {
    "id": "foundation-v0.1-smooth-unknown-01",
    "kind": "SYNTHETIC",
    "timeframe": "30m",
    "source": "가상 사례, 실제 시장 데이터 아님",
    "facts": "가상 종목: 일봉 사전 조건 충족, 30분봉 Hull21 밴드 재진입, Put Wall 지지, 매수세 53, Smooth 방향 미확인.",
    "rubric": "이 사례에서 Smooth가 미확인이므로 진입 충족을 단정하지 않는다. VF-0.2의 최종 Hull21 밴드 순서도 별도로 학습한다.",
}
R01_FIRST = """가상 사례 (R01, SYNTHETIC): 매수 우위는 유지되지만 증가 속도가 감소하고 있습니다. 매도 우위로의 전환은 아직 관찰되지 않았습니다.
질문: 이것은 매수세 둔화인가요, 매도세 발생인가요? 이유는 무엇인가요?"""
R01_RULE = """VF-0.2 R01 전용 수업. 첫 응답은 지정된 가상 사례와 질문 한 개만 제시하고 정답·힌트·판정을 노출하지 않는다.
R04, Hull21, SMA50, 진입/대기/무효 질문, AMZN 등 실제 선택 종목을 절대 섞지 않는다.
학생 답변이 들어오면 맞는 부분을 확인하고, 오답에는 정답 대신 짧은 힌트와 재질문을 제시한다.
재답변이 나온 다음에만 2~5줄 피드백 후 새로운 가상 사례 하나로 재시험한다.
이 수업은 VF-0.2 R01에 관한 것이며 장중 시세 판정이 아니다."""
GUIDED_SYSTEM = """교육 모드입니다. 한국어 또는 영어로, 답변의 주 언어를 따릅니다.
메인 교육 기준은 소유자 승인 VF-0.2입니다. 기초 눌림목 v0.1 자료도 보존하되 규칙 충돌 시 VF-0.2를 따릅니다. 이는 실제 매매 검증 또는 주문 승인이 아닙니다.
사용자가 명시적으로 연습 문제를 요청할 때만 가상 사례와 질문 한 개를 제시합니다.
개념 질문에는 질문만 간단히 답하고 수업이나 퀴즈를 덧붙이지 않습니다.
사용자가 특정 규칙을 요청하면 그 규칙만 다룹니다. R01 수업에서 진입/대기/무효를 묻지 마세요.
답변을 평가할 때는 현재 질문 및 학습 단계에 맞는 짧은 피드백만 제공합니다.
수치와 사실은 제공된 사례에 한정하며, 가격 결과로 판단을 사후 정당화하지 마세요.
Smooth, RS, 매수세, Put Wall 및 Hull21 밴드의 산식을 발명하지 마세요.
보호 풋은 매수이고 비용과 손실 위험이 있습니다.
실제 데이터, 캔들 기준시각과 AI가 볼 수 없는 차트 내용은 확인했다고 말하지 마세요.
"""

SYSTEM = """당신은 GEXOption의 교육용 주식 분석 선생님 '소라'입니다. 사용자가 선택한 응답 언어에 따라 한국어 또는 영어로 설명합니다.
사용자에게 실제 주문 또는 수익 보장을 제안하지 않습니다. 답변은 기본적으로 한국어/영어 모두 4~5줄 안으로 짧게 작성하고, 사용자가 자세히 요청할 때만 늘립니다. 질문에 바로 답하고 불필요한 긴 반복과 마크다운 장식을 피합니다.
서버가 제공한 봉 데이터와 교육 자료만 사실 근거로 사용합니다. 메인 교육 기준은 소유자 승인 VF-0.2이며 실제 거래 검증은 미완료입니다. 기초 v0.1은 보존하되 충돌 시 VF-0.2를 우선합니다. 보호 풋은 BUY TO OPEN, 청산은 SELL TO CLOSE이며 3봉 의무 대기는 폐기했습니다. 스윙은 30분봉, 단타는 5분봉이며 50일 이동평균은 일봉입니다. Hull20을 Hull21 밴드로 혼동하지 마세요. 제공 자료가 없는 조건은 충족/미충족 판정 대신 확인 불가로 분류합니다.
TradingView 내장 차트와 서버의 지연 봉 데이터는 반드시 같은 시점이 아닙니다.
봉 시간은 시작 시각이고, 실시간 현재가가 아니라 마지막 완료봉 종가입니다.
데이터가 없거나 오래되면 가격·거래량·Smooth·GEX Call/Put Wall을 추측하지 않습니다.
EVP는 추정 지표이며 TOS Net Delta/Smooth와 동등하지 않습니다.
'성립 조건', '실패 조건', '확인되지 않은 사실'을 구별합니다.
교육 자료에 없는 정확한 공식·승률은 모른다고 답합니다. 시장 데이터 상태가 UNAVAILABLE이면 현재 충족/미충족을 판정하지 않고 전부 확인 불가로 처리하되, 교육용 원리 설명은 계속합니다. TradingView 차트 화면을 직접 읽었다고 말하지 않습니다.
차트·시세의 구체적 분석을 요청한 경우에만 종목/시간봉/데이터 상태를 표시하고, 일반 대화나 가상 연습에는 붙이지 않습니다.
매 답변마다 자기소개, 고정 면책 문구, 자동 수업 및 퀴즈를 반복하지 마세요.
"""

def market_context(ticker, tf):
    # Server-side acquisition prevents users from supplying invented chart figures.
    try:
        from swing_radar_chart import VALID_TF, aggregates, indicators
    except Exception:
        raise ValueError("DATA_IMPORT_FAILURE") from None
    if tf not in VALID_TF:
        raise ValueError("UNSUPPORTED_TIMEFRAME")
    try:
        rows = aggregates(ticker, tf, True)
    except ValueError:
        raise
    except Exception:
        raise ValueError("DATA_AGGREGATION_FAILURE") from None
    done = [r for r in rows if r.get("complete") and r.get("coverage_ok", True)]
    try:
        bars = indicators(done)
    except Exception:
        raise ValueError("DATA_INDICATOR_FAILURE") from None
    if not bars:
        raise ValueError("NO_COMPLETED_BARS")
    # indicators() returns descriptive OHLCV keys; previous short keys silently
    # dropped ALL OHLCV values from Gemini's historical chart context.
    safe = []
    for bar in bars[-60:]:
        row = {key: bar.get(key) for key in
               ("t", "open", "high", "low", "close", "volume", "hull20", "evp")
               if bar.get(key) is not None}
        if not all(key in row for key in ("t", "open", "high", "low", "close", "volume")):
            raise ValueError("DATA_INCOMPLETE")
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
        "available_fields": ["open", "high", "low", "close", "volume", "hull20", "evp_estimate"],
        "bars_sent": len(safe),
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
            mode = str(req.get("mode", "chart")).lower()
            if mode not in ("chart", "learn"):
                raise ValueError("MODE")
            # An explicit request for real historical bars must not silently use
            # synthetic teaching mode. Route this request to server-verified data.
            explicit_chart_request = bool(re.search(
                r"(?i)(?:실제|과거|최근|완료된).{0,30}(?:봉|차트|데이터)|(?:봉|차트|데이터).{0,30}(?:실제|과거|최근)|(?:AMZN|NVDA|TSLA|SPY|QQQ).{0,25}(?:분석|봉)",
                question))
            requested_mode = mode
            if mode == "learn" and explicit_chart_request:
                mode = "chart"
            rule_id = "R01" if mode == "learn" and re.search(r"(?i)\bR01\b|매수세\s*둔화|매도세\s*발생|buying\s+pressure\s+weakening|selling\s+pressure\s+emerg", question) else None
            history = req.get("history", [])
            if not isinstance(history, list) or len(history) > 8:
                raise ValueError("HISTORY")
            turns = []
            for h in history:
                if not isinstance(h, dict) or h.get("role") not in ("user", "assistant") or not isinstance(h.get("content"), str):
                    raise ValueError("HISTORY_TURN")
                turns.append({"role": h["role"], "content": h["content"][-1200:]})
            # Follow-up student answers often omit "R01". Recover the current
            # exercise from the recent user/assistant turns, not just this message.
            active_r01_drill = mode == "learn" and any(
                re.search(r"(?i)\bR01\b", t["content"]) and
                re.search(r"연습|문제|퀴즈|practice|quiz", t["content"], re.I)
                for t in turns if t["role"] == "user"
            )
            if active_r01_drill:
                rule_id = "R01"
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            self.reply(400, {"error": "종목·시간봉·질문 형식을 확인해 주세요.", "kind": "INVALID_INPUT"})
            return
        # A standalone greeting is not a request to start the default lesson.
        # Respond deterministically before contacting the model or market provider.
        greeting = re.sub(r"[\\s!?.,。！？~～ㅎㅎㅋ😊🙂👋]+", "", question.strip().lower())
        greetings = {"안녕", "안녕하세요", "하이", "반가워", "반갑습니다", "hi", "hello", "hey"}
        if greeting in greetings:
            reply = "안녕하세요! 😊" if language != "en" and greeting not in {"hi", "hello", "hey"} else "Hello! 😊"
            market = {"ticker": "SYNTHETIC" if mode == "learn" else ticker,
                      "tf": "30m" if mode == "learn" else tf,
                      "last_bar_utc": None, "retrieved_at_utc": None,
                      "timezone": "N/A", "price_label": "미조회",
                      "data_status": "NOT_REQUESTED", "source": "인사말 — 시세 조회 없음"}
            self.reply(200, {"ticker": ticker, "tf": tf, "answer": reply,
                             "market": market, "lesson": {"title": "인사", "version": "v1"},
                             "core_criteria": {"title": VF02["title"], "version": VF02["version"], "status": VF02["status"]},
                             "language": language, "mode": mode, "requested_mode": requested_mode, "case_id": None})
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
            market = market_context(ticker, tf) if mode == "chart" else {
                "ticker": "SYNTHETIC", "tf": "30m", "bars": [], "last_bar_utc": None,
                "retrieved_at_utc": None, "timezone": "N/A",
                "price_label": "가상 사례", "data_status": "SYNTHETIC",
                "source": "교육용 가상 사례", "unavailable": ["실제 시세 및 모든 실제 지표"]
            }
        except Exception as exc:
            # Do not expose provider credentials or response bodies.
            reason = str(exc).split(":", 1)[0]
            allowed = {
                "DATA_NOT_CONFIGURED", "DATA_NETWORK_ERROR", "DATA_INVALID_JSON",
                "DATA_PROVIDER_ERROR", "DATA_TRUNCATED", "DATA_INVALID",
                "DATA_STALE", "DATA_GAP", "DATA_INCOMPLETE", "NO_COMPLETED_BARS",
                "CALENDAR_UNAVAILABLE", "DATA_CALENDAR_FAILURE", "DATA_CALENDAR_DEPENDENCY", "DATA_CALENDAR_INIT", "DATA_CALENDAR_SESSION", "DATA_PARSE_FAILURE", "DATA_RESAMPLE_FAILURE", "DATA_IMPORT_FAILURE",
                "DATA_AGGREGATION_FAILURE", "DATA_INDICATOR_FAILURE",
            }
            if reason.startswith("DATA_PROVIDER_HTTP_") and re.fullmatch(r"DATA_PROVIDER_HTTP_[0-9]{3}", reason):
                code = reason
            else:
                code = reason if reason in allowed else "DATA_UNKNOWN_ERROR"
            market = {
                "ticker": ticker, "tf": tf, "bars": [],
                "last_bar_utc": None,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "timezone": "America/New_York",
                "price_label": "조회 불가", "data_status": "UNAVAILABLE",
                "source": "Massive OHLCV 조회 실패 (" + code + ")",
                "unavailable": ["완료봉", "현재가", "거래량", "Hull21 밴드", "TOS Smooth", "매수세", "Put Wall", "Call Wall"],
                "failure_kind": code,
            }
        wants_practice = bool(re.search(r"연습|문제|퀴즈|수업 시작|학습 시작|practice|quiz", question, re.I)) or active_r01_drill
        context = json.dumps(
            {"market": market if mode == "chart" else {"data_status": "SYNTHETIC", "source": "학습용; 실제 종목·시세 없음"},
             "core_criteria": VF02,
             "selected_lesson": {"rule_id": "R01", "first_case": R01_FIRST} if rule_id == "R01" and wants_practice else None},
            ensure_ascii=False, separators=(",", ":"))
        dialogue = "\n".join(("사용자" if h["role"] == "user" else "소라") + ": " + h["content"] for h in turns)
        language_instruction = {"ko": "Respond in Korean.", "en": "Respond in English.", "auto": "Reply in the predominant language of the latest user question (Korean or English)."}[language]
        prompt = (language_instruction + "\nVerified context: " + context +
                  "\nConversation:\n" + dialogue + "\nUser question: " + question +
                  "\nAnswer the specific question directly in 2-5 short lines. "
                  "Do not introduce yourself or repeat generic disclaimers. "
                  "Do not add a scenario, lesson, quiz or follow-up question unless explicitly asked. "
                  "Only use observations available in the supplied context; do not invent measurements.")
        if rule_id == "R01" and wants_practice:
            prompt += "\nACTIVE RULE: VF-0.2 R01 ONLY. Do not ask about buying, entry, Hull21 or R04."
            drill_replies = [
                t for t in turns if t["role"] == "assistant"
                and ("가상 사례" in t["content"] or "매수세" in t["content"])
            ]
            if not drill_replies:
                prompt += ("\nPresent one short SYNTHETIC example of buying pressure weakening "
                           "without proof of increasing selling pressure. Ask exactly one question "
                           "that distinguishes the two. Wait. Do not disclose the answer.")
            elif len(drill_replies) == 1:
                prompt += ("\nThis is the student's FIRST attempt at the R01 drill. "
                           "When it is wrong, give only one observation-based HINT about the "
                           "selling-pressure measurement, then ask one short retry question. "
                           "Do NOT state the correct label or explain the full answer. Wait. "
                           "If correct, give short confirmation without an unsolicited new quiz.")
            else:
                prompt += ("\nThis is a follow-up attempt. Give concise feedback based only on "
                           "the student's actual answer. Do not start a new scenario unless requested.")
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent"
        try:
            r = requests.post(
                url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"systemInstruction": {"parts": [{"text": SYSTEM + ("\\n" + GUIDED_SYSTEM if mode == "learn" else "")}]},
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
            if r.status_code == 404:
                self.reply(502, {"error": "현재 설정된 Gemini 모델을 사용할 수 없습니다 (404). 모델 접근 권한 또는 모델 이름을 확인해 주세요. 기존 API 키를 공개하거나 교체하지 마세요.", "kind": "MODEL_NOT_AVAILABLE", "model": model})
                return
            if r.status_code != 200:
                self.reply(502, {"error": "AI 모델 제공자 응답 오류 (" + str(r.status_code) + ").", "kind": "MODEL_PROVIDER_ERROR"})
                return
            result = r.json()
            answer = "".join(p.get("text", "") for item in result.get("candidates", [])[:1] for p in item.get("content", {}).get("parts", []) if isinstance(p, dict)).strip()
            if not answer:
                self.reply(502, {"error": "AI에서 빈 답변이 반환됐습니다.", "kind": "MODEL_EMPTY"})
                return
            # Mandatory source-independent notice for specific-stock / chart analysis.
            # Do not repeat it for simple greetings or synthetic teaching drills.
            is_stock_query = bool(re.search(r"(?i)\b" + re.escape(ticker) + r"\b|현재가|매수해|매도해|콜월|풋월|종목 분석|주가|차트 분석", question))
            if mode == "chart" or is_stock_query:
                notice = "본 자료는 교육 및 정보 제공을 목적으로 하며, 투자 권유는 아닙니다. 최종 투자 판단과 책임은 투자자 본인에게 있습니다."
                if notice not in answer:
                    answer = answer.rstrip() + "\n\n" + notice
            self.reply(200, {"ticker": ticker, "tf": tf, "answer": answer,
                             "market": {k: market[k] for k in ("ticker", "tf", "last_bar_utc", "retrieved_at_utc", "timezone", "price_label", "data_status", "source")},
                             "lesson": {"title": "VF-0.2 R01 수급 순환" if rule_id == "R01" else LESSON["title"], "version": "VF-0.2" if rule_id == "R01" else LESSON["version"]}, "rule_id": rule_id, "core_criteria": {"title": VF02["title"], "version": VF02["version"], "status": VF02["status"]}, "language": language, "mode": mode, "case_id": "vf02-r01-first" if rule_id == "R01" else (GUIDED_CASE["id"] if mode == "learn" else None)})
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
