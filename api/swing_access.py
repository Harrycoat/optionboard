"""Fail-closed personal radar access. Configure secrets in Vercel, never in HTML."""
import base64
import hashlib
import hmac
import os

def authorized(headers):
    user=os.environ.get('SWING_RADAR_USER','')
    digest=os.environ.get('SWING_RADAR_PASSWORD_SHA256','')
    if not user or len(digest)!=64:
        return False,503
    try:
        kind,token=headers.get('Authorization','').split(' ',1)
        name,password=base64.b64decode(token,validate=True).decode().split(':',1)
        ok=kind.lower()=='basic' and hmac.compare_digest(name.encode(),user.encode()) and hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(),digest.lower())
        return ok,200 if ok else 401
    except (ValueError,UnicodeError):
        return False,401

def require_access(handler):
    ok,status=authorized(handler.headers)
    if ok:return True
    handler.send_response(status)
    handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Cache-Control','no-store')
    if status==401:handler.send_header('WWW-Authenticate','Basic realm="GEXOption Personal Radar", charset="UTF-8"')
    handler.end_headers()
    handler.wfile.write(b'{"error":"Personal radar authentication required or not configured"}')
    return False
