"""Read-only SORA 30 robot dashboard: evaluations, never executes paper trades."""
from http.server import BaseHTTPRequestHandler
from urllib.request import urlopen,Request
from urllib.error import URLError
import json,os,sys
sys.path.insert(0,os.path.dirname(__file__))
from sora_robots import evaluate_all

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  try:
   # 30 identifiers are deterministic; avoid filesystem reliance in Vercel serverless.
   # Strategy mapping is defined in sora_robots.py, and trades remain separate.
   config={"accounts":[{"id":f"SORA-{i:02d}"} for i in range(1,31)]}
   result=evaluate_all(config)
   result["status"]="WAITING_FOR_VERIFIED_SIGNALS"
   result["message"]="30개 독립 전략 판정기가 준비됐습니다. 데이터 입력·스케줄·체결 저장 미연결. 자동 가상매수 0건."
   self.reply(200,result)
  except Exception as exc:
   self.reply(503,{"status":"UNAVAILABLE","message":"로봇 엔진 초기화 실패. 자동매매 실행 없음.","error_type":type(exc).__name__})
 def reply(self,code,payload):
  body=json.dumps(payload,ensure_ascii=False).encode()
  self.send_response(code);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
