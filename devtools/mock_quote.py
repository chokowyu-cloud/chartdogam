"""중계 서버 흉내 — 실제 Worker의 응답 모양 그대로 돌려준다.

Worker를 배포하기 전에 앱 쪽 폴링·화면 갱신·실패 처리가 도는지 확인하기 위한 것.

    python devtools/mock_quote.py 8792
"""
from __future__ import annotations

import datetime
import http.server
import json
import random
import socketserver
import sys
import urllib.parse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
CALLS: list[tuple[str, int]] = []


class Handler(http.server.BaseHTTPRequestHandler):
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
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if u.path == "/health":
            return self._send({"ok": True, "kr": True, "us": True, "mock": True})

        if u.path != "/quote":
            return self._send({"error": "not found"}, 404)

        market = q.get("market", [""])[0]
        codes = [c for c in q.get("codes", [""])[0].split(",") if c]
        CALLS.append((market, len(codes)))
        print(f"  요청 #{len(CALLS)}  {market}  {len(codes)}종목", flush=True)

        # 다섯 번째 호출은 일부러 실패시켜서 화면이 '갱신 실패'를 표시하는지 본다
        if len(CALLS) == 5:
            print("  (일부러 502 응답)", flush=True)
            return self._send({"error": "upstream down"}, 502)

        quotes = {
            c: {
                "price": round(random.uniform(50, 900), 2),
                "change": round(random.uniform(-20, 20), 2),
                "changePct": round(random.uniform(-7, 7), 2),
                "volume": random.randint(10_000, 9_000_000),
            }
            for c in codes
        }
        self._send({
            "asOf": datetime.datetime.now().isoformat(timespec="seconds"),
            "market": market,
            "source": "MOCK",
            "delayed": market == "US",     # 미국은 지연으로 가정
            "count": len(quotes),
            "quotes": quotes,
        })


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as s:
        print(f"모의 중계 서버 http://localhost:{PORT} — Ctrl+C 로 종료", flush=True)
        s.serve_forever()
