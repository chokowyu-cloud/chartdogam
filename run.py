"""차트도감 원클릭 실행기.

'실행하기.bat' 이 이 파일을 부른다. 사용자는 명령어를 한 줄도 치지 않는다.

준비 → 소스 점검 → 수집 → 분석 → 빌드 → 브라우저 열기 를 순서대로 하고,
어디서 멈췄는지와 그래서 무엇을 하면 되는지를 한국어로 알려준다.

배치 파일(.bat)에 한글을 넣으면 cmd 인코딩 때문에 깨지기 쉬워서,
안내 문구는 전부 여기(파이썬)에서 낸다. bat 은 얇은 껍데기다.
"""
from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import console

console.setup()

ROOT = Path(__file__).resolve().parent
PY = sys.executable
PORT = 8000


# ---------------------------------------------------------------- 표시
def line(ch: str = "─") -> None:
    print(ch * 60)


def step(n: int, total: int, title: str, note: str = "") -> None:
    print()
    line()
    print(f"  [{n}/{total}] {title}")
    if note:
        print(f"        {note}")
    line()


def die(title: str, *lines: str) -> None:
    print()
    line("━")
    print(f"  ⚠  {title}")
    line("━")
    for s in lines:
        print(f"  {s}")
    print()
    print("  이 창의 내용을 복사해서 물어보시면 원인을 짚어드릴 수 있습니다.")
    print("  (창에서 드래그 → Enter 를 누르면 복사됩니다)")
    print()
    try:
        input("  Enter 를 누르면 창이 닫힙니다... ")
    except EOFError:
        pass          # 콘솔이 아닌 곳에서 돌 때
    sys.exit(1)


def run(args: list[str], what: str) -> subprocess.CompletedProcess:
    """하위 명령을 그대로 흘려보낸다 — 진행 상황이 실시간으로 보여야 한다."""
    try:
        return subprocess.run([PY, *args], cwd=ROOT)
    except Exception as e:
        die(f"{what} 실행 자체가 안 됐습니다", str(e))


# ---------------------------------------------------------------- 단계
def ensure_packages() -> None:
    step(1, 5, "필요한 도구 준비", "처음 한 번만 오래 걸립니다 (1~3분)")
    try:
        import pandas, pyarrow, FinanceDataReader  # noqa: F401
        print("  이미 준비돼 있습니다. 건너뜁니다.")
        return
    except ImportError:
        pass

    r = subprocess.run([PY, "-m", "pip", "install", "-r", "requirements.txt"], cwd=ROOT)
    if r.returncode != 0:
        die("도구 설치에 실패했습니다",
            "인터넷 연결을 확인해 주세요.",
            "회사 네트워크라면 방화벽에 막혔을 수 있습니다.")


def probe() -> None:
    step(2, 5, "데이터 소스가 살아 있는지 확인", "30초면 끝납니다")
    r = run(["collect.py", "--probe"], "소스 점검")
    if r.returncode != 0:
        die("무료 데이터 소스에 연결하지 못했습니다",
            "위에 [FAIL] 로 표시된 줄이 원인입니다.",
            "소스가 일시적으로 막혔거나 네트워크 문제일 수 있습니다.",
            "",
            "지금은 여기서 멈추는 게 맞습니다 — 그냥 진행하면",
            "30분을 쓰고도 빈 결과가 나옵니다.")
    print("\n  ✓ 데이터가 잘 들어옵니다.")


def collect() -> None:
    step(3, 5, "시세 받기", "처음이면 15~40분. 창을 그대로 두고 다른 일 하셔도 됩니다")
    t0 = time.time()
    r = run(["collect.py"], "수집")
    if r.returncode != 0:
        die("수집 도중 멈췄습니다",
            "실패율이 너무 높으면 기존 데이터를 지키기 위해 일부러 중단합니다.",
            "잠시 뒤 다시 실행해 보세요. 받아둔 데이터는 남아 있어서 이어서 받습니다.")
    print(f"\n  ✓ 수집 완료 ({(time.time()-t0)/60:.0f}분)")


def analyze() -> None:
    has_bt = (ROOT / "data" / "backtest.json").exists()
    step(4, 5, "패턴 분석 + 성적표 계산",
         "6~12분 (성적표는 처음 한 번만)" if not has_bt else "1~2분")

    args = ["scan.py", "--real"]
    if not has_bt:
        args.append("--refresh-bt")
    r = run(args, "분석")
    if r.returncode != 0:
        die("분석 도중 멈췄습니다", "위 오류 내용을 확인해 주세요.")

    r = run(["build_app.py"], "화면 빌드")
    if r.returncode != 0:
        die("화면 만들기에 실패했습니다", "위 오류 내용을 확인해 주세요.")


def start_relay() -> bool:
    """키를 넣어뒀으면 시세 중계기를 같이 띄운다. 창을 하나 더 열 필요가 없다."""
    if not (ROOT / "keys.json").exists():
        return False
    try:
        import relay
        return relay.serve(background=True) is not None
    except Exception as e:
        print(f"  (실시간 시세 중계기를 못 띄웠습니다: {e} — 종가 기준으로 계속합니다)")
        return False


def serve() -> None:
    step(5, 5, "브라우저에서 열기", f"주소창에 localhost:{PORT}")
    live = start_relay()
    print()
    if live:
        print("  실시간 시세 중계기도 같이 띄웠습니다. 장중이면 30초마다 갱신됩니다.")
    print("  잠시 뒤 브라우저가 자동으로 열립니다.")
    print("  상단 띠가 회색 '○○○○-○○-○○ 종가 기준' 이면 실제 시세가 붙은 것입니다.")
    print()
    print("  ※ 끝내실 때는 이 창에서 Ctrl + C 를 누르거나 창을 닫으세요.")
    print("  ※ 방화벽 창이 뜨면 '취소'를 눌러도 보는 데 지장 없습니다.")
    print()
    line()

    def open_later():
        time.sleep(2)
        webbrowser.open(f"http://localhost:{PORT}")

    import threading
    threading.Thread(target=open_later, daemon=True).start()

    try:
        subprocess.run([PY, "-m", "http.server", str(PORT), "--directory", "public"], cwd=ROOT)
    except KeyboardInterrupt:
        pass
    print("\n  종료했습니다.")


def main() -> int:
    print()
    print("  차트도감 — 실제 시세 붙이기")
    print("  주식 차트 패턴 스크리너 · made by 유진아빠")
    print()
    print("  이 창을 닫지 마시고 끝날 때까지 두세요.")
    print("  전체 20~50분 정도 걸립니다 (처음 한 번만).")

    ensure_packages()
    probe()
    collect()
    analyze()
    serve()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n  중단했습니다. 다시 실행하면 이어서 진행됩니다.")
        sys.exit(130)
