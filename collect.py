"""1주차 메인 스크립트 — 유니버스를 만들고 일봉을 쌓는다.

    python collect.py              # 국내 + 미국 전부
    python collect.py --market KR  # 국내만
    python collect.py --limit 20   # 앞 20종목만 (첫 실행 점검용)
    python collect.py --probe      # 소스가 살아 있는지 30초 점검만 하고 종료

이 스크립트가 무사히 끝나면 1주차는 끝난 것이다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time

import pandas as pd

import config
import console
import store
import universe as uni
from sources.base import SourceError
from sources.registry import default_registry

console.setup()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("collect")


# ---------------------------------------------------------------- 소스 점검
def probe(registry) -> int:
    """무료 소스가 지금 살아 있는지 30초 안에 판가름낸다.

    1주차에 가장 먼저 알아야 할 사실이 이것이라, 별도 모드로 뺐다.
    """
    today = dt.date.today()
    start = today - dt.timedelta(days=20)
    ok = True

    for market, sample in (("KR", "005930"), ("US", "AAPL")):
        try:
            cand, src = registry.get_universe(market)
            print(f"  [OK]   {market} 유니버스  {len(cand):4d}종목  ← {src}")
            print(f"         예시: {', '.join(cand['name'].head(3).astype(str))}")
        except SourceError as e:
            ok = False
            print(f"  [FAIL] {market} 유니버스 — {e}")

        try:
            df, src = registry.get_ohlcv(sample, market, start, today)
            last = df.index.max().date()
            print(f"  [OK]   {market} 일봉 {sample}  {len(df):3d}행  최종 {last}  종가 {df['Close'].iloc[-1]:,.0f}  ← {src}")
        except SourceError as e:
            ok = False
            print(f"  [FAIL] {market} 일봉 {sample} — {e}")

    return 0 if ok else 1


# ---------------------------------------------------------------- 수집
def collect_market(registry, market: str, limit: int | None) -> pd.DataFrame:
    today = dt.date.today()
    full_start = today - dt.timedelta(days=config.HISTORY_DAYS)

    candidates, src = registry.get_universe(market)
    if limit:
        candidates = candidates.head(limit)
    log.info("[%s] 후보 %d종목 (%s)", market, len(candidates), src)

    metrics: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []
    t0 = time.time()

    for i, row in enumerate(candidates.to_dict("records"), 1):
        code = row["code"]
        start, cached = store.fetch_start(market, code, full_start)

        try:
            if cached is not None and start >= today:
                merged = cached          # 이미 최신, 네트워크 안 탐
            else:
                fresh, _ = registry.get_ohlcv(code, market, start, today)
                merged = store.merge(cached, fresh)
                store.save(market, code, merged)
            metrics[code] = uni.liquidity_metrics(merged)
        except SourceError as e:
            failures.append((code, str(e)[:90]))
            if cached is not None:       # 캐시라도 있으면 그걸로 버틴다
                metrics[code] = uni.liquidity_metrics(cached)

        if i % 50 == 0 or i == len(candidates):
            log.info("[%s] %d/%d  경과 %.0fs  실패 %d",
                     market, i, len(candidates), time.time() - t0, len(failures))

    if failures:
        log.warning("[%s] 수집 실패 %d종목. 앞 5개:", market, len(failures))
        for code, msg in failures[:5]:
            log.warning("    %s — %s", code, msg)

    fail_rate = len(failures) / max(1, len(candidates))
    if fail_rate > 0.30:
        raise SourceError(
            f"[{market}] 실패율 {fail_rate:.0%} — 소스가 막혔을 가능성이 큽니다. "
            "기존 캐시를 덮어쓰지 않고 중단합니다."
        )

    table = uni.build_table(candidates, metrics)
    top_n = config.KR_TOP_N if market == "KR" else config.US_TOP_N
    min_value = config.KR_MIN_VALUE if market == "KR" else 0.0

    selected, dropped = uni.select(
        table, market, top_n=top_n, min_value=min_value, as_of=today
    )
    print(uni.format_drop_report(market, dropped))
    return selected


# ---------------------------------------------------------------- 산출
def write_universe(frames: list[pd.DataFrame]) -> pd.DataFrame:
    final = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)

    final.to_parquet(config.DATA_DIR / "universe.parquet")

    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "count": int(len(final)),
        "by_market": final["market"].value_counts().to_dict() if len(final) else {},
        "items": [
            {
                "code": r["code"],
                "name": r["name"],
                "market": r["market"],
                "index": r["source_index"],
                "avg_value": round(float(r["avg_value"])),
                "last_close": float(r["last_close"]),
            }
            for r in final.to_dict("records")
        ],
    }
    (config.OUT_DIR / "universe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return final


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["KR", "US"], help="한 시장만 수집")
    ap.add_argument("--limit", type=int, help="후보 앞 N개만 (점검용)")
    ap.add_argument("--probe", action="store_true", help="소스 생존 점검만")
    args = ap.parse_args()

    try:
        registry = default_registry()
    except SourceError as e:
        log.error("소스 초기화 실패: %s", e)
        return 2

    if args.probe:
        print("\n=== 소스 생존 점검 ===")
        rc = probe(registry)
        print("\n" + registry.report())
        return rc

    markets = [args.market] if args.market else ["KR", "US"]
    frames = []
    for m in markets:
        try:
            frames.append(collect_market(registry, m, args.limit))
        except SourceError as e:
            log.error("%s", e)
            return 1

    final = write_universe(frames)
    print(f"\n최종 유니버스 {len(final)}종목 → data/universe.parquet, public/data/universe.json")
    print(registry.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
