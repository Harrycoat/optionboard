"""SORA administrator credential verification only. Never returns or logs secrets."""
from http.server import BaseHTTPRequestHandler
import os,re,hmac,hashlib,base64,json
class handler(BaseHTTPRequestHandler):
 def do_POST(self):
  user=os.getenv("SORA_ADMIN_USER","")
  digest=os.getenv("SORA_ADMIN_PASSWORD_SHA256","")
  if not user or not re.fullmatch(r"[0-9a-fA-F]{64}",digest):
   return self.reply(503,"ADMIN_CONFIG_MISSING")
  try:
   kind,data=self.headers.get("Authorization","").split(" ",1)
   u,p=base64.b64decode(data,validate=True).decode("utf-8").split(":",1)
   valid=(kind.lower()=="basic" and hmac.compare_digest(u,user) and hmac.compare_digest(hashlib.sha256(p.encode("utf-8")).hexdigest(),digest.lower()))
  except (ValueError,UnicodeError):
   valid=False
  if not valid:return self.reply(401,"INVALID_ADMIN_CREDENTIALS")
  return self.reply(200,"AUTH_OK")
 def reply(self,status,code):
  b=json.dumps({"code":code},ensure_ascii=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
