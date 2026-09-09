"""패턴 성적표 — 워크포워드 백테스트.

핵심 원칙 세 가지.

1. **미래를 보지 않는다.** 시점 t의 판정에는 t까지의 데이터만 넘긴다.
   지표(MA·RSI·볼린저)는 전부 후행 계산이라 슬라이스만으로 충분하다.

2. **기준선을 함께 잰다.** "승률 54%"는 그 자체로 아무 의미가 없다.
   같은 기간 아무 종목이나 아무 날 샀을 때의 승률이 55%라면 그 패턴은
   오히려 해로운 것이다. 그래서 평가한 모든 (종목, 날짜)의 성적을
   기준선으로 같이 계산하고, 앱에는 항상 둘을 나란히 보여준다.

3. **점수가 의미 있는지 검증한다.** 유사도 점수가 높을수록 실제로 성적이
   좋아야 점수를 표시할 자격이 있다. 구간별로 갈리지 않으면 그 점수는
   장식일 뿐이므로, 그 사실도 그대로 표시한다.
"""
from __future__ import annotations

import time

import numpy as np

import indicators as ind

import console

console.setup()
import patterns as pat

# 1주 · 2주 · 4주 (거래일 기준). 앱에서 앞의 둘을 주로 쓴다 —
# 패턴을 보고 실제로 궁금한 건 "그래서 다음 한두 주 어떻게 됐나"이기 때문.
HORIZONS = (5, 10, 20)
HORIZON_LABEL = {5: "1주", 10: "2주", 20: "4주"}
HEADLINE = 10         # 점수 유효성 판정 기준 구간 (2주)

STEP = 5              # 5거래일마다 평가. 3년치에서 종목당 약 120개 시점
MIN_HISTORY = 150     # 지표가 안정화되는 최소 구간
SCORE_MIN = 40
BUCKETS = [(40, 60, "40–60"), (60, 80, "60–80"), (80, 101, "80–100")]


def _span_years(enriched: dict) -> float | None:
    """백테스트가 실제로 훑은 기간. 화면에 '지난 N년'으로 쓴다 —
    적당히 '3년'이라고 적어두면 데이터가 짧아졌을 때 거짓말이 된다."""
    spans = []
    for v in enriched.values():
        idx = v["d"].index
        if len(idx) > 1:
            spans.append((idx.max() - idx.min()).days / 365.25)
    return max(spans) if spans else None


def _stats(rows: list[dict], h: int) -> dict:
    """수익률 리스트 → 승률·평균·중앙값."""
    v = np.array([r[f"r{h}"] for r in rows], dtype=float)
    if len(v) == 0:
        return {"n": 0, "win": None, "mean": None, "median": None}
    return {
        "n": int(len(v)),
        "win": round(float((v > 0).mean()) * 100, 1),
        "mean": round(float(v.mean()) * 100, 2),
        "median": round(float(np.median(v)) * 100, 2),
    }


def run(enriched: dict, period: str = "mid", verbose: bool = True) -> dict:
    lookback = pat.PERIODS[period][1]
    maxh = max(HORIZONS)
    signals: dict[str, list[dict]] = {k: [] for k in pat.PATTERNS}
    baseline: list[dict] = []

    t0 = time.time()
    for si, (code, v) in enumerate(enriched.items(), 1):
        d = v["d"]
        n = len(d)
        close = d["Close"].to_numpy()
        if n < MIN_HISTORY + maxh + 5:
            continue

        for t in range(MIN_HISTORY, n - maxh, STEP):
            fwd = {f"r{h}": float(close[t + h] / close[t] - 1) for h in HORIZONS}
            # 기준선: 이 종목을 이 날 그냥 샀다면
            baseline.append(fwd)

            sub = d.iloc[: t + 1]          # t 시점까지만 — 미래 없음
            for key, (_, _, fn) in pat.PATTERNS.items():
                try:
                    hit = fn(sub, lookback)
                except Exception:
                    continue
                if hit.matched and hit.score >= SCORE_MIN:
                    signals[key].append({"score": hit.score, **fwd})

        if verbose and si % 60 == 0:
            print(f"  {si}/{len(enriched)}종목  경과 {time.time()-t0:.0f}s")

    base = {f"h{h}": _stats(baseline, h) for h in HORIZONS}

    out = {}
    for key, (name, _, _) in pat.PATTERNS.items():
        rows = signals[key]
        rec = {"name": name, "n": len(rows)}
        for h in HORIZONS:
            s = _stats(rows, h)
            b = base[f"h{h}"]
            rec[f"h{h}"] = {
                **s,
                # 기준선 대비가 이 표의 핵심 숫자다
                "win_edge": None if s["win"] is None else round(s["win"] - b["win"], 1),
                "mean_edge": None if s["mean"] is None else round(s["mean"] - b["mean"], 2),
            }
        # 점수 구간별 — 점수가 실제로 성적을 가르는지
        rec["buckets"] = []
        for lo, hi, label in BUCKETS:
            sub = [r for r in rows if lo <= r["score"] < hi]
            st = _stats(sub, HEADLINE)
            rec["buckets"].append({"label": label, **st})

        # 점수 유효성: 표본이 충분한 구간들 사이에서 높은 점수가 실제로 더 나은가.
        # 아니라면 그 점수는 장식이므로, 앱에 그렇게 표시한다.
        usable = [b for b in rec["buckets"] if b["n"] >= 30]
        if len(usable) < 2:
            rec["score_valid"] = "unknown"
        else:
            lo_b, hi_b = usable[0], usable[-1]
            diff = hi_b["win"] - lo_b["win"]
            rec["score_valid"] = "yes" if diff >= 2 else ("no" if diff <= -2 else "flat")
            rec["score_gap"] = round(diff, 1)

        # 기준선을 넘는가 (2주 기준)
        hk = f"h{HEADLINE}"
        rec["beats"] = bool(rec[hk]["win_edge"] is not None and rec[hk]["win_edge"] > 0)
        out[key] = rec

    return {
        "period": period,
        "lookback": lookback,
        "horizons": list(HORIZONS),
        "horizon_labels": {str(k): v for k, v in HORIZON_LABEL.items()},
        "headline": HEADLINE,
        "years": round(bt_span_years, 1) if (bt_span_years := _span_years(enriched)) else None,
        "step": STEP,
        "baseline": base,
        "patterns": out,
        "eval_points": len(baseline),
        "elapsed": round(time.time() - t0, 1),
    }


def report(bt: dict) -> str:
    L = []
    b5, b20 = bt["baseline"]["h5"], bt["baseline"]["h20"]
    b10 = bt["baseline"]["h10"]
    L.append(f"평가 시점 {bt['eval_points']:,}개 · {bt['period']} 창 {bt['lookback']}일 · {bt['elapsed']}초")
    L.append("")
    L.append(f"{'기준선 (아무 종목·아무 날)':30s}  "
             f"1주 {b5['win']:.1f}%  2주 {b10['win']:.1f}%  4주 {b20['win']:.1f}%")
    L.append("-" * 112)
    for key, r in bt["patterns"].items():
        h5, h10, h20 = r["h5"], r["h10"], r["h20"]
        if h10["n"] == 0:
            L.append(f"{r['name']:30s}  신호 없음")
            continue
        L.append(f"{r['name']:30s}  "
                 f"1주 {h5['win']:.1f}% ({h5['win_edge']:+.1f})  "
                 f"2주 {h10['win']:.1f}% ({h10['win_edge']:+.1f})  "
                 f"4주 {h20['win']:.1f}% ({h20['win_edge']:+.1f})   n={r['n']:,}")
    L.append("")
    L.append("점수 구간별 2주 성적 (점수가 의미 있으면 오른쪽으로 갈수록 좋아야 함)")
    L.append("-" * 112)
    for key, r in bt["patterns"].items():
        cells = "  ".join(
            f"{b['label']}: " + ("―――" if b["n"] < 20 else f"승률 {b['win']:4.1f}% 평균 {b['mean']:+5.2f}% (n={b['n']:,})")
            for b in r["buckets"])
        L.append(f"{r['name']:22s}  {cells}")
    return "\n".join(L)


def run_all(enriched: dict, verbose: bool = True) -> dict:
    """기간별로 따로 잰다. 단기 창에서 잡힌 신호의 성적을 중기 성적으로
    보여주면 그건 거짓말이 되므로, 화면에서 고른 기간의 숫자를 그대로 쓴다."""
    out = {}
    for period in pat.PERIODS:
        if verbose:
            print(f"\n--- {pat.PERIODS[period][0]} ({pat.PERIODS[period][1]}일 창) ---")
        out[period] = run(enriched, period, verbose)
    return out


if __name__ == "__main__":
    import make_demo
    data = make_demo.build()
    print(f"백테스트 대상 {len(data)}종목")
    enriched = {c: {**v, "d": ind.enrich(v["df"])} for c, v in data.items()}
    bt = run(enriched)
    print("\n" + report(bt))
