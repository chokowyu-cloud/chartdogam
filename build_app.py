"""앱 빌드 — 결과물 두 벌.

  public/index.html          배포용. 데이터를 fetch로 읽고, PWA로 설치된다.
  chartdogam-app.html        미리보기용. 데이터를 페이지에 박아 단일 파일로.

배포용이 데이터를 따로 읽는 이유: 배치가 매일 JSON만 새로 쓰면 되고,
앱 페이지 자체는 브라우저·서비스워커 캐시에 그대로 남는다.
"""
from __future__ import annotations

import json
import pathlib

import console

console.setup()

ROOT = pathlib.Path(__file__).resolve().parent
TPL = ROOT / "app_template.html"
SCAN = ROOT / "public" / "data" / "scan.json"
PUBLIC = ROOT / "public"

HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#161A20" media="(prefers-color-scheme: dark)">
<meta name="description" content="한국·미국 주식을 차트 패턴과 카테고리로 훑어 보는 스크리너. 매매를 권유하지 않는 정보 도구입니다.">
<meta name="robots" content="noindex">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icons/favicon.png">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="차트도감">
<style>html,body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
"""

SW_REG = """
<script>
/* 서비스워커는 https(또는 localhost)에서만 등록된다.
   실패해도 앱은 그대로 동작하므로 조용히 넘어간다. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  });
}
</script>
</body>
</html>
"""


def split_template(tpl: str) -> tuple[str, str]:
    """템플릿을 head 부분과 body 부분으로 가른다."""
    marker = '<div class="phone"'
    i = tpl.index(marker)
    return tpl[:i], tpl[i:]


def main() -> int:
    tpl = TPL.read_text(encoding="utf-8")
    if not SCAN.exists():
        print("public/data/scan.json 이 없습니다. 먼저 python scan.py 를 실행하세요.")
        return 1
    raw = SCAN.read_text(encoding="utf-8")

    # --- 배포용: 데이터를 비워두면 앱이 fetch로 읽는다
    head, body = split_template(tpl)
    deploy = HEAD + head + body.replace("__DATA__", "") + SW_REG
    (PUBLIC / "index.html").write_text(deploy, encoding="utf-8")
    (PUBLIC / ".nojekyll").write_text("", encoding="utf-8")

    # --- 미리보기용: 단일 파일 (아티팩트는 외부 fetch가 막혀 있다)
    safe = raw.replace("</", "<\\/")   # </script> 로 조기 종료되는 것 방지
    (ROOT / "chartdogam-app.html").write_text(tpl.replace("__DATA__", safe), encoding="utf-8")

    d = json.loads(raw)
    idx = PUBLIC / "index.html"
    print(f"배포용   {idx.relative_to(ROOT)}  {idx.stat().st_size/1024:.0f} KB  (데이터 분리)")
    print(f"         + manifest.webmanifest, sw.js, icons/, data/scan.json ({SCAN.stat().st_size/1024:.0f} KB)")
    print(f"미리보기 chartdogam-app.html  {(ROOT/'chartdogam-app.html').stat().st_size/1024/1024:.2f} MB  (단일 파일)")
    print(f"종목 {len(d['stocks'])} / 카테고리 {len(d['categories'])} / 패턴 {len(d['patterns'])}"
          f" / 성적표 {'있음' if d.get('backtest') else '없음'}")
    print("\n로컬에서 보려면:  python3 -m http.server 8000 --directory public")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
