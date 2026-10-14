"""Read-only overview of the TEN active strategy evaluators (not 30 legacy bots)."""
from http.server import BaseHTTPRequestHandler
import os,sys,json
sys.path.insert(0,os.path.dirname(__file__))
from sora_ten_opportunity_engine import STRATEGIES
class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  robots=[{"account_id":id,"strategy_code":strategy,"state":"WAITING_FOR_MARKET_FEED",
           "automatic_paper_execution":False} for id,strategy in STRATEGIES.items()]
  obj={"mode":"SIMULATION_ONLY","active_robot_count":len(robots),"robots":robots,
     "execution":"NOT_ENABLED","trades_executed_by_robots":0,
     "next_integration":"verified daily + completed 30m market data, index membership, verified TOS Smooth, 10 separate durable ledgers, scheduled executor",
     "note":"This is a read-only engine registry. It is not proof of live autonomous execution."}
  body=json.dumps(obj,ensure_ascii=False).encode()
  self.send_response(200);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
