"""시세 중계 — 내 PC 안에서만 도는 작은 서버.

Cloudflare 판(worker/)과 하는 일이 똑같다. 다른 건 딱 하나, **아무 계정도
만들지 않는다**는 것. Node.js 도, Cloudflare 로그인도, wrangler 도 필요 없다.
파이썬 기본 기능만 쓴다.

중간에 뭔가 있어야 하는 이유는 그대로다.
  · 브라우저는 증권사 API를 직접 못 부른다 (CORS)
  · API 키를 페이지에 넣으면 소스 보기로 새어 나간다

    python relay.py --setup    키를 넣고 앱에 연결한다 (처음 한 번)
    python relay.py            중계 서버를 띄운다

키는 이 PC 의 keys.json 에만 저장된다. 어디로도 전송되지 않는다.
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import console

console.setup()

ROOT = Path(__file__).resolve().parent
KEYS = ROOT / "keys.json"
TOKEN_CACHE = ROOT / "data" / ".kis_token.json"
CONFIG = ROOT / "config.py"

PORT = 8792
ENDPOINT = f"http://localhost:{PORT}"

KIS_HOST = "https://openapi.koreainvestment.com:9443"
FINNHUB = "https://finnhub.io/api/v1"

MAX_CODES = 20      # 한 번에 물어볼 종목 수 — 증권사 호출 한도 보호
QUOTE_TTL = 20      # 같은 질문은 20초간 재사용
TOKEN_HOURS = 12    # 토큰 발급은 한도가 빡빡하다. 받아두고 오래 쓴다.

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- 표시
def line(ch="─"):
    print(ch * 60)


# ---------------------------------------------------------------- 키
def load_keys() -> dict:
    if not KEYS.exists():
        return {}
    try:
        return json.loads(KEYS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_keys(d: dict) -> None:
    KEYS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    try:
        os.chmod(KEYS, 0o600)      # 윈도우에선 조용히 무시된다
    except Exception:
        pass


# ---------------------------------------------------------------- HTTP
def http_json(url, *, headers=None, data=None, timeout=8):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    if body is not None:
        req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def num(o, *names):
    """증권사마다 필드명이 달라서 후보를 순서대로 본다."""
    for n in names:
        v = (o or {}).get(n)
        if v not in (None, ""):
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------- KIS 토큰
def kis_token(keys) -> str:
    now = time.time()
    try:
        t = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        if t.get("exp", 0) - 600 > now:
            return t["value"]
    except Exception:
        pass

    d = http_json(f"{KIS_HOST}/oauth2/tokenP", data={
        "grant_type": "client_credentials",
        "appkey": keys["KIS_APP_KEY"],
        "appsecret": keys["KIS_APP_SECRET"],
    }, timeout=15)
    tok = d.get("access_token")
    if not tok:
        raise RuntimeError("KIS 토큰 응답에 access_token 이 없음")

    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(
        json.dumps({"value": tok, "exp": now + TOKEN_HOURS * 3600}), encoding="utf-8")
    try:
        os.chmod(TOKEN_CACHE, 0o600)
    except Exception:
        pass
    return tok


# ---------------------------------------------------------------- 어댑터
def fetch_kr(codes, keys):
    token = kis_token(keys)
    quotes = {}
    for code in codes:
        url = (f"{KIS_HOST}/uapi/domestic-stock/v1/quotations/inquire-price"
               f"?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={code}")
        try:
            o = http_json(url, headers={
                "authorization": f"Bearer {token}",
                "appkey": keys["KIS_APP_KEY"],
                "appsecret": keys["KIS_APP_SECRET"],
                "tr_id": "FHKST01010100",
                "custtype": "P",
            }).get("output")
        except Exception:
            continue                       # 못 받은 종목은 그냥 빠진다
        price = num(o, "stck_prpr")
        if price is None:
            continue
        quotes[code] = {
            "price": price,
            "change": num(o, "prdy_vrss"),
            "changePct": num(o, "prdy_ctrt"),
            "volume": num(o, "acml_vol"),
        }
    return {"source": "KIS", "delayed": False, "quotes": quotes}


def fetch_us(codes, keys):
    key = keys.get("FINNHUB_KEY")
    if not key:
        raise RuntimeError("미국 시세 키(FINNHUB_KEY)가 없습니다")
    quotes = {}
    for code in codes:
        try:
            q = http_json(f"{FINNHUB}/quote?symbol={urllib.parse.quote(code)}&token={key}")
        except Exception:
            continue
        c = q.get("c")
        if not isinstance(c, (int, float)) or not c:
            continue
        quotes[code] = {
            "price": float(c),
            "change": q.get("d"),
            "changePct": q.get("dp"),
            "volume": None,
        }
    # 무료 티어는 지연될 수 있다. 확실하지 않은 건 확실하지 않다고 표시한다.
    return {"source": "Finnhub", "delayed": not keys.get("FINNHUB_REALTIME"),
            "quotes": quotes}


# ---------------------------------------------------------------- 서버
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(b)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET,OPTIONS")
        self.send_header("content-length", "0")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        keys = self.server.keys

        if u.path == "/health":
            return self._send({
                "ok": True,
                "kr": bool(keys.get("KIS_APP_KEY")),
                "us": bool(keys.get("FINNHUB_KEY")),
                "local": True,
            })
        if u.path != "/quote":
            return self._send({"error": "not found"}, 404)

        market = (q.get("market", [""])[0] or "").upper()
        if market not in ("KR", "US"):
            return self._send({"error": "market 은 KR 또는 US"}, 400)

        codes, seen = [], set()
        for c in (q.get("codes", [""])[0] or "").split(","):
            c = c.strip()
            if c and c not in seen and len(c) <= 12 and c.replace(".", "").replace("-", "").isalnum():
                seen.add(c)
                codes.append(c)
        codes = codes[:MAX_CODES]
        if not codes:
            return self._send({"error": "codes 가 비어 있음"}, 400)

        ck = market + "|" + ",".join(sorted(codes))
        with _lock:
            hit = _cache.get(ck)
            if hit and time.time() - hit[0] < QUOTE_TTL:
                return self._send({**hit[1], "cached": True})

        try:
            out = fetch_kr(codes, keys) if market == "KR" else fetch_us(codes, keys)
        except Exception as e:
            # 실패를 조용히 삼키지 않는다. 화면이 '갱신 실패'를 표시할 수 있어야 한다.
            print(f"  [{market}] 실패: {e}", flush=True)
            return self._send({"error": str(e)}, 502)

        body = {
            "asOf": datetime.datetime.now().isoformat(timespec="seconds"),
            "market": market,
            "source": out["source"],
            "delayed": out["delayed"],
            "count": len(out["quotes"]),
            "quotes": out["quotes"],
        }
        with _lock:
            _cache[ck] = (time.time(), body)
        print(f"  [{market}] {len(out['quotes'])}/{len(codes)}종목", flush=True)
        self._send(body)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(background=False):
    keys = load_keys()
    if not keys.get("KIS_APP_KEY") and not keys.get("FINNHUB_KEY"):
        print("  키가 없습니다. 먼저 '실시간연결.bat' 을 실행해 주세요.")
        return None
    srv = Server(("127.0.0.1", PORT), Handler)
    srv.keys = keys
    if background:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
    print(f"  시세 중계 중 — {ENDPOINT}   (Ctrl+C 로 종료)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  종료했습니다.")
    return srv


# ---------------------------------------------------------------- 설정
def wire_config():
    txt = CONFIG.read_text(encoding="utf-8")
    import re
    if re.search(r'LIVE_ENDPOINT\s*=\s*"[^"]*"', txt):
        txt = re.sub(r'LIVE_ENDPOINT\s*=\s*"[^"]*"', f'LIVE_ENDPOINT = "{ENDPOINT}"', txt)
    else:
        txt += f'\nLIVE_ENDPOINT = "{ENDPOINT}"\n'
    CONFIG.write_text(txt, encoding="utf-8")


def ask(prompt, current):
    got = input(f"  {prompt}\n  (그냥 Enter = {'그대로 둠' if current else '건너뜀'}) : ").strip()
    return got or current


def setup():
    import getpass

    print()
    print("  차트도감 — 실시간 시세 연결")
    print()
    print("  이 PC 안에서만 도는 작은 중계기를 씁니다.")
    print("  가입할 계정도, 설치할 프로그램도 없습니다.")
    print()
    print("  키는 이 폴더의 keys.json 에만 저장되고 어디로도 전송되지 않습니다.")
    print()
    line()
    print("  ■ 국내 시세 — 한국투자증권 Open API")
    print("    apiportal.koreainvestment.com 에서 앱키·앱시크릿을 발급받으세요.")
    print("    (계좌가 있어야 합니다. 발급은 무료)")
    line()

    keys = load_keys()
    k = getpass.getpass("  앱키 (appkey, 붙여넣기 — 화면에 안 보입니다) : ").strip()
    if k:
        keys["KIS_APP_KEY"] = k
    s = getpass.getpass("  앱시크릿 (appsecret) : ").strip()
    if s:
        keys["KIS_APP_SECRET"] = s

    print()
    line()
    print("  ■ 미국 시세 — Finnhub (선택)")
    print("    finnhub.io 무료 가입 후 키 하나. 지금은 Enter 로 건너뛰어도 됩니다.")
    line()
    f = getpass.getpass("  Finnhub 키 : ").strip()
    if f:
        keys["FINNHUB_KEY"] = f

    if not keys.get("KIS_APP_KEY") and not keys.get("FINNHUB_KEY"):
        print("\n  키가 하나도 없습니다. 아무것도 저장하지 않았습니다.")
        return 1

    save_keys(keys)
    wire_config()
    print("\n  저장했습니다 → keys.json")
    print(f"  앱에 연결했습니다 → {ENDPOINT}")

    # 실제로 도는지 여기서 확인한다. 나중에 화면에서 조용히 실패하면 원인을 못 찾는다.
    print("\n  실제로 시세가 들어오는지 확인합니다...")
    srv = serve(background=True)
    if srv is None:
        return 1
    time.sleep(0.4)
    ok = False
    try:
        if keys.get("KIS_APP_KEY"):
            d = http_json(f"{ENDPOINT}/quote?market=KR&codes=005930", timeout=25)
            q = (d.get("quotes") or {}).get("005930")
            if q:
                print(f"  ✓ 삼성전자 {q['price']:,.0f}원  ({q.get('changePct')}%)")
                ok = True
            else:
                print(f"  ✗ 국내 시세를 못 받았습니다: {d.get('error') or '응답이 비어 있음'}")
        if keys.get("FINNHUB_KEY"):
            d = http_json(f"{ENDPOINT}/quote?market=US&codes=AAPL", timeout=25)
            q = (d.get("quotes") or {}).get("AAPL")
            if q:
                print(f"  ✓ AAPL ${q['price']:,.2f}  ({q.get('changePct')}%)")
                ok = True
            else:
                print(f"  ✗ 미국 시세를 못 받았습니다: {d.get('error') or '응답이 비어 있음'}")
    except Exception as e:
        print(f"  ✗ 확인 실패: {e}")
    finally:
        srv.shutdown()

    print()
    line("━")
    if ok:
        print("  연결됐습니다.")
        line("━")
        print("  이제 '실행하기' 를 더블클릭하면 됩니다.")
        print("  장중이면 화면 맨 위에 '실시간 · 시:분:초' 가 뜹니다.")
        print()
        print("  ※ 장이 닫힌 시간에도 위 숫자가 나왔다면 정상입니다.")
    else:
        print("  키는 저장했지만 시세가 안 들어옵니다.")
        line("━")
        print("  자주 있는 원인:")
        print("   · 앱키/앱시크릿이 바뀌어 붙여넣어졌다 (둘을 서로 바꿔 넣기 쉽습니다)")
        print("   · 모의투자용 키를 넣었다 (실전투자용이어야 합니다)")
        print("   · 앞뒤에 공백이나 줄바꿈이 딸려 들어갔다")
        print()
        print("  '실시간연결' 을 다시 실행해서 붙여넣으면 덮어씁니다.")
    print()
    try:
        input("  Enter 를 누르면 창이 닫힙니다... ")
    except EOFError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    if "--setup" in sys.argv:
        sys.exit(setup())
    serve()
