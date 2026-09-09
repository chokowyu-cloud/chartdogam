"""콘솔 출력 인코딩 고정.

한국어 Windows의 기본 콘솔 코드페이지는 cp949라서, 진행 로그에 쓰는
'→', '·', '←', '–' 같은 문자에서 UnicodeEncodeError 로 스크립트가
그대로 죽는다. 실행 시작점마다 이걸 한 번 부르면 해결된다.
macOS·리눅스에서는 아무 일도 하지 않는다.
"""
from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass       # 리다이렉트된 스트림 등 — 조용히 넘어간다
