"""중계 서버 올리기 — 자동화할 수 있는 건 전부 자동으로.

사람이 직접 해야 하는 건 두 가지뿐이다. 둘 다 본인 계정 인증이라 대신 못 한다.
  · Cloudflare 로그인 (브라우저가 열린다)
  · 증권사·Finnhub 키 붙여넣기

키는 이 창에서 바로 wrangler 로 넘어간다. 파일에 적히지도, 화면에 보이지도,
어디로 전송되지도 않는다.

나머지 — 설정 파일 수정, 비밀값 등록, 배포, 주소 받아오기, config.py 반영,
동작 확인 — 는 이 스크립트가 한다.

    python deploy.py
"""
from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import console  # noqa: E402

console.setup()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOML = HERE / "wrangler.toml"
CONFIG = ROOT / "config.py"


# ---------------------------------------------------------------- 표시
def line(ch="─"):
    print(ch * 60)


def step(n, total, title):
    print()
    line()
    print(f"  [{n}/{total}] {title}")
    line()


def die(title, *lines):
    print()
    line("━")
    print(f"  ⚠  {title}")
    line("━")
    for s in lines:
        print(f"  {s}")
    print()
    try:
        input("  Enter 를 누르면 창이 닫힙니다... ")
    except EOFError:
        pass
    sys.exit(1)


def run(cmd, **kw):
    """npx 는 Windows에서 .cmd 라 shell=True 가 필요하다."""
    return subprocess.run(cmd, cwd=HERE, shell=isinstance(cmd, str), **kw)


# ---------------------------------------------------------------- 단계
def check_node():
    step(1, 6, "Node.js 확인")
    if shutil.which("npx") or shutil.which("npx.cmd"):
        print("  준비돼 있습니다.")
        return
    import webbrowser
    webbrowser.open("https://nodejs.org/")
    die("Node.js 가 필요합니다",
        "방금 열린 페이지에서 왼쪽 LTS 버전을 받아 설치하세요.",
        "설치는 계속 '다음'만 누르면 됩니다.",
        "설치 후 이 창을 닫고 다시 실행해 주세요.")


def login():
    step(2, 6, "Cloudflare 로그인")
    print("  브라우저가 열립니다. 계정이 없으면 무료로 가입하시면 됩니다.")
    print("  로그인 후 'Allow' 를 누르고 이 창으로 돌아오세요.\n")
    r = run("npx --yes wrangler login")
    if r.returncode != 0:
        die("로그인에 실패했습니다",
            "브라우저에서 Allow 를 눌렀는지 확인하고 다시 실행해 주세요.")
    print("\n  로그인 완료.")


def ask_origin():
    step(3, 6, "내 페이지 주소")
    print("  이 주소에서만 중계 서버를 쓸 수 있게 잠급니다.")
    print("  비워 두면 아무나 쓸 수 있고, 그만큼 내 증권사 호출 한도가 남에게 소진됩니다.\n")
    print("  예: https://내아이디.github.io")
    print("      http://localhost:8000   (내 PC에서만 볼 거라면)\n")
    origin = input("  주소 (그냥 Enter = localhost:8000) : ").strip() or "http://localhost:8000"

    txt = TOML.read_text(encoding="utf-8")
    txt = re.sub(r'ALLOW_ORIGIN\s*=\s*"[^"]*"', f'ALLOW_ORIGIN = "{origin}"', txt)
    TOML.write_text(txt, encoding="utf-8")
    print(f"\n  설정했습니다 → {origin}")
    return origin


def deploy():
    step(4, 6, "올리기")
    print("  Cloudflare 에 중계 서버를 올립니다. 처음에는 1~2분 걸립니다.\n")
    r = run("npx --yes wrangler deploy", capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip()[-1200:])
    if r.returncode != 0:
        die("배포에 실패했습니다", "위 메시지를 복사해서 물어봐 주세요.")

    m = re.search(r"https://[\w.-]*workers\.dev[^\s]*", out)
    if not m:
        die("배포는 됐는데 주소를 못 찾았습니다",
            "위 출력에서 workers.dev 로 끝나는 주소를 찾아",
            "config.py 의 LIVE_ENDPOINT 에 직접 넣어 주세요.")
    url = m.group(0).rstrip("/")
    print(f"\n  중계 서버 주소: {url}")
    return url


def put_secret(name, prompt, required):
    """키를 받아 wrangler 에 바로 넘긴다. 화면에 찍히지 않는다."""
    while True:
        val = getpass.getpass(f"  {prompt}: ").strip()
        if val:
            break
        if not required:
            print("    건너뜁니다.")
            return False
        print("    비워 둘 수 없습니다. 다시 입력해 주세요.")
    r = run(f"npx --yes wrangler secret put {name}",
            input=val + "\n", text=True, capture_output=True)
    if r.returncode != 0:
        print(f"    등록 실패: {(r.stderr or '')[:200]}")
        return False
    print(f"    {name} 등록 완료")
    return True


def secrets():
    step(5, 6, "키 등록")
    print("  입력한 키는 화면에 보이지 않고, 파일에도 남지 않습니다.")
    print("  Cloudflare 에 바로 저장되어 중계 서버만 꺼내 씁니다.\n")

    print("  ■ 국내 시세 — 한국투자증권 Open API")
    print("    apiportal.koreainvestment.com 에서 발급받은 값\n")
    ok_kr = put_secret("KIS_APP_KEY", "앱키 (appkey)", True)
    ok_kr = put_secret("KIS_APP_SECRET", "앱시크릿 (appsecret)", True) and ok_kr

    print("\n  ■ 미국 시세 — Finnhub (선택)")
    print("    finnhub.io 무료 가입 후 받은 키. 지금은 건너뛰어도 됩니다.\n")
    ok_us = put_secret("FINNHUB_KEY", "Finnhub 키 (건너뛰려면 Enter)", False)

    if not ok_kr:
        die("국내 키 등록에 실패했습니다", "앱키·앱시크릿을 다시 확인해 주세요.")
    return ok_us


def wire(url, expect_us):
    step(6, 6, "앱에 연결하고 확인")

    txt = CONFIG.read_text(encoding="utf-8")
    if re.search(r'LIVE_ENDPOINT\s*=\s*"[^"]*"', txt):
        txt = re.sub(r'LIVE_ENDPOINT\s*=\s*"[^"]*"', f'LIVE_ENDPOINT = "{url}"', txt)
    else:
        txt += f'\nLIVE_ENDPOINT = "{url}"\n'
    CONFIG.write_text(txt, encoding="utf-8")
    print("  config.py 에 반영했습니다.")

    try:
        with urllib.request.urlopen(url + "/health", timeout=15) as r:
            h = json.loads(r.read().decode())
        print(f"  응답 확인 — 국내 {'준비됨' if h.get('kr') else '없음'}"
              f" / 미국 {'준비됨' if h.get('us') else '없음'}")
        if expect_us and not h.get("us"):
            print("  (미국 키를 건너뛰셨다면 정상입니다)")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  응답 확인 실패: {e}")
        print("  배포는 됐을 수 있습니다. 브라우저에서 아래 주소를 직접 열어보세요.")
        print(f"    {url}/health")


def main():
    print()
    print("  차트도감 — 시세 중계 서버 올리기")
    print()
    print("  직접 하실 일은 두 가지뿐입니다.")
    print("    · Cloudflare 로그인 (브라우저가 열립니다)")
    print("    · 증권사 키 붙여넣기")
    print("  나머지는 이 창이 알아서 합니다. 5분쯤 걸립니다.")

    check_node()
    login()
    ask_origin()
    # 키보다 배포가 먼저다. 아직 없는 서버에 secret 을 넣으려 하면 wrangler 가
    # "새로 만들까요?" 를 되묻는데, 그때 붙여넣은 키가 그 질문의 답으로 먹힌다.
    url = deploy()
    expect_us = secrets()
    wire(url, expect_us)

    print()
    line("━")
    print("  끝났습니다.")
    line("━")
    print("  이제 상위 폴더의 '실행하기' 를 다시 더블클릭하면")
    print("  장중에 현재가가 30초마다 갱신됩니다.")
    print()
    print("  화면 맨 위에 '실시간 · 시:분:초' 가 뜨면 연결된 것입니다.")
    print()
    try:
        input("  Enter 를 누르면 창이 닫힙니다... ")
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n  중단했습니다. 다시 실행하면 처음부터 진행됩니다.")
        sys.exit(130)
