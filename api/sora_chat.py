"""Private Sora analysis chat beta. No order execution or cross-tenant data."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
import json,os,re,sys
import requests
sys.path.insert(0,os.path.dirname(__file__))
from swing_access import require_access

SYSTEM = """당신은 GEXOption의 주식 분석 상담사 소라입니다. 한국어로 간결하고 친절하게 설명하세요.
목적은 교육과 시장 분석이며 개인화된 매수/매도 지시나 주문은 하지 않습니다.
SWING: 일봉부터 분석합니다. 일봉 20일선 기울기가 하락이면 상승 추세 눌림목으로 분류하지 않습니다.
상승 기울기와 눌림이 확인되면 30분봉 Hull20 기울기, 가격 안착, 봉 마감, 거래량을 점검합니다.
CORE HEDGE: 보유 주식은 별도 관리하며 Call Wall 저항 및 5분봉 Hull20 하향 이탈과 매도세로 풋 헤지의 가능성과 보험 비용을 설명합니다. 5분봉 Hull20 회복 시 풋 청산 조건을 분석합니다.
Put Wall이나 Call Wall은 실제 옵션 데이터가 확인되지 않으면 미확인이라 답하세요. 차트 수치가 없으면 가격, Hull, 거래량, Smooth를 꾸며내지 마세요.
EVP는 OHLCV에서 계산한 추정치일 뿐 TOS Net Delta/Smooth가 아닙니다.
상승/하락과 헤지 효과는 보장되지 않습니다. 사용자 제공 사례는 현재 실시간 사실로 취급하지 마세요.
선택한 종목이 바뀌면 다른 종목 대화를 섞지 마세요. 분석 근거와 미확인 정보를 구분하세요.
"""

class handler(BaseHTTPRequestHandler):
 def do_POST(self):
  if urlparse(self.path).path!="/api/sora_chat":
   self.send_error(404);return
  if not require_access(self):return
  limit=12000
  try:
   n=int(self.headers.get("Content-Length","0"))
   if n<1 or n>limit:raise ValueError("invalid request size")
   req=json.loads(self.rfile.read(n))
   ticker=str(req.get("ticker","")).upper()
   question=req.get("message")
   if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",ticker) or not isinstance(question,str) or not 1<=len(question.strip())<=1200:raise ValueError("invalid ticker or question")
   history=req.get("history",[])
   if not isinstance(history,list) or len(history)>8:raise ValueError("invalid history")
   turns=[]
   for h in history:
    if not isinstance(h,dict) or h.get("role") not in ("user","assistant") or not isinstance(h.get("content"),str) or len(h["content"])>1200:raise ValueError("invalid history turn")
    turns.append({"role":h["role"],"content":h["content"]})
  except (ValueError,TypeError,KeyError,UnicodeDecodeError,json.JSONDecodeError):
   self.reply(400,{"error":"입력 형식을 확인해 주세요."});return
  key=os.environ.get("SORA_GEMINI_API_KEY","").strip()
  model=os.environ.get("SORA_GEMINI_MODEL","gemini-2.5-flash-lite").strip()
  if not key:
   self.reply(503,{"error":"소라 AI 모델이 아직 연결되지 않았습니다. 서버 환경 변수 SORA_GEMINI_API_KEY가 필요합니다."});return
  if not re.fullmatch(r"[a-zA-Z0-9_.-]{2,60}",model):
   self.reply(503,{"error":"모델 설정을 확인해 주세요."});return
  # No market data or verified wall values are provided in this initial integration.
  context="선택 종목: "+ticker+". 검증된 시세나 차트가 이 요청에 포함되지 않았습니다. 현재 수치나 Wall을 주장하지 마세요."
  dialogue="\\n".join(("사용자" if h["role"]=="user" else "소라")+": "+h["content"] for h in turns)
  prompt=context+"\\n"+dialogue+"\\n사용자: "+question
  try:
   url="https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent"
   r=requests.post(url,headers={"x-goog-api-key":key,"Content-Type":"application/json"},
      json={"systemInstruction":{"parts":[{"text":SYSTEM}]},"contents":[{"role":"user","parts":[{"text":prompt}]}],
            "generationConfig":{"maxOutputTokens":650,"temperature":0.25}},timeout=(4,22))
   if r.status_code!=200:raise RuntimeError("provider unavailable")
   data=r.json()
   answer="".join(p.get("text","") for item in data.get("candidates",[])[:1] for p in item.get("content",{}).get("parts",[]) if isinstance(p,dict))
   if not answer.strip():raise RuntimeError("empty response")
   self.reply(200,{"ticker":ticker,"answer":answer.strip(),"data_status":"UNVERIFIED","notice":"시세·옵션 Wall 데이터 미연결. 교육용 분석입니다."})
  except Exception:
   self.reply(502,{"error":"AI 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요."})
 def reply(self,code,obj):
  b=json.dumps(obj,ensure_ascii=False).encode("utf-8")
  self.send_response(code);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff")
  self.end_headers();self.wfile.write(b)
