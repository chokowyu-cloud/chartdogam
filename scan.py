"""스캔 실행 → 앱이 읽을 JSON 산출.

    python scan.py            # 데모 데이터로 (시세 소스 없이도 화면 검증 가능)
    python scan.py --real     # data/ohlcv/ 의 실제 캐시로

산출: public/data/scan.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import categories as cat

import console

console.setup()
import indicators as ind
import patterns as pat

TOP_CATEGORY = 20
TOP_PATTERN = 15
CHART_DAYS = 130
SPARK_DAYS = 40
CHART_WEEKS = 120        # 주봉으로 볼 때 보여줄 주 수 (약 2년 반)
INTRADAY_MIN = 5         # 분봉 간격(분). 1분봉은 파일이 너무 커진다.


# ------------------------------------------------------------------ 봉 만들기
def rd(x, digits: int):
    """원화는 소수점이 없다. 34600.0 대신 34600 으로 적어 파일을 줄인다."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return int(round(float(x))) if digits == 0 else round(float(x), digits)


def weekly(d: pd.DataFrame, digits: int) -> dict:
    """일봉을 주봉으로 묶는다. 주의 마지막 거래일 기준(금요일 라벨).

    이동평균은 주봉 기준으로 다시 계산한다 — 주봉 화면의 MA20은 20'주'다.
    일봉 MA를 그대로 얹으면 선이 화면과 맞지 않는다."""
    w = d.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    ma20 = w["Close"].rolling(20).mean()
    ma60 = w["Close"].rolling(60).mean()
    t = w.tail(CHART_WEEKS)
    r = lambda s: [rd(x, digits) for x in s.tail(CHART_WEEKS)]
    return {
        "dates": [i.strftime("%y/%m/%d") for i in t.index],
        "o": r(w["Open"]), "h": r(w["High"]), "l": r(w["Low"]), "c": r(w["Close"]),
        "v": [int(x) for x in t["Volume"]],
        "ma20": r(ma20), "ma60": r(ma60),
    }


def intraday_demo(row, market: str, digits: int, rng) -> dict:
    """데모용 분봉. 마지막 일봉의 시·고·저·종 안에서 하루치를 만들어 낸다.

    실제 분봉은 증권사 API에서만 나온다. 여기서 만드는 건 화면 확인용이고,
    실제 데이터로 돌 때는 이 함수를 부르지 않는다 — 그때 분봉 탭은 '없음'으로
    표시된다. 있는 척하지 않는다."""
    n = 390 // INTRADAY_MIN                      # 정규장 6시간 30분
    o, h, l, c = (float(row[k]) for k in ("Open", "High", "Low", "Close"))

    # 시가에서 종가로 가는 브라운 다리를 놓고, 그 뒤 고가·저가에 닿도록 늘린다
    steps = rng.normal(0, 1, n).cumsum()
    steps -= steps[-1] * np.arange(1, n + 1) / n
    path = o + (c - o) * np.arange(1, n + 1) / n + steps * (h - l) * 0.06
    path[-1] = c
    lo, hi = path.min(), path.max()
    if hi > lo:
        path = l + (path - lo) * (h - l) / (hi - lo)
        path[-1] = c
    closes = np.round(path, digits)
    opens = np.concatenate([[round(o, digits)], closes[:-1]])
    # 꼬리는 그날의 고가·저가를 넘지 않는다. 넘으면 일봉과 앞뒤가 안 맞는다.
    wig = (h - l) * 0.04
    highs = np.round(np.minimum(np.maximum(opens, closes) + rng.random(n) * wig, h), digits)
    lows = np.round(np.maximum(np.minimum(opens, closes) - rng.random(n) * wig, l), digits)

    # 거래량은 장 시작·마감에 몰린다
    u = np.linspace(-1, 1, n)
    vol = (0.4 + u ** 2) * (0.7 + rng.random(n) * 0.6)
    vol = vol / vol.sum() * float(row["Volume"])

    start = 9 * 60 if market == "KR" else 9 * 60 + 30
    times = [f"{(start + i * INTRADAY_MIN) // 60:02d}:{(start + i * INTRADAY_MIN) % 60:02d}"
             for i in range(n)]

    # 분봉 화면의 MA도 분봉 기준이다 — 20봉·60봉
    cs = pd.Series(closes)
    ma = lambda k: [rd(x, digits) for x in cs.rolling(k).mean()]
    return {
        "step": INTRADAY_MIN, "demo": True,
        "dates": times,
        "o": [rd(x, digits) for x in opens], "h": [rd(x, digits) for x in highs],
        "l": [rd(x, digits) for x in lows], "c": [rd(x, digits) for x in closes],
        "v": [int(x) for x in vol],
        "ma20": ma(20), "ma60": ma(60),
    }


# ------------------------------------------------------------------ 데이터 적재
def load_demo():
    import make_demo
    return make_demo.build()


def load_real():
    import config
    import store
    up = config.DATA_DIR / "universe.parquet"
    if not up.exists():
        raise SystemExit("data/universe.parquet 이 없습니다. 먼저 python collect.py 를 실행하세요.")

    uni = pd.read_parquet(up)
    out, short, missing = {}, [], []
    for r in uni.to_dict("records"):
        df = store.load(r["market"], r["code"])
        if df is None:
            missing.append(r["code"]); continue
        if len(df) <= 130:
            short.append(r["code"]); continue
        out[r["code"]] = {"name": r["name"], "market": r["market"],
                          "df": df, "regime": "real"}

    print(f"유니버스 {len(uni)}종목 → 사용 {len(out)}  "
          f"(캐시 없음 {len(missing)} / 이력 부족 {len(short)})")
    if not out:
        raise SystemExit("쓸 수 있는 일봉이 없습니다. python collect.py 를 먼저 끝내주세요.")
    return out


# ------------------------------------------------------------------ 스캔
def load_or_run_backtest(enriched: dict, refresh: bool) -> dict:
    """성적표는 무거우니 캐시한다. 패턴 로직을 고치면 --refresh-bt 로 다시 돌린다."""
    import backtest
    cache = Path("data/backtest.json")
    if cache.exists() and not refresh:
        print("성적표: 캐시 사용 (data/backtest.json)")
        return json.loads(cache.read_text(encoding="utf-8"))
    print("성적표 계산 중 — 기간 3종 워크포워드, 2~3분 걸립니다")
    bt = backtest.run_all(enriched, verbose=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(bt, ensure_ascii=False), encoding="utf-8")
    return bt


def run(data: dict, demo: bool, backtest_data: dict | None = None,
        enriched: dict | None = None) -> dict:
    if enriched is None:
        enriched = {c: {**v, "d": ind.enrich(v["df"])} for c, v in data.items()}

    # ---------------- 카테고리
    cat_out = []
    for key, (name, desc, tone, fn) in cat.CATEGORIES.items():
        hits = []
        for code, v in enriched.items():
            try:
                ok, sortkey, label = fn(v["d"], v["market"])
            except Exception:
                continue
            if ok:
                hits.append({"code": code, "sort": float(sortkey), "label": label})
        hits.sort(key=lambda h: h["sort"], reverse=True)
        cat_out.append({
            "key": key, "name": name, "desc": desc, "tone": tone,
            "count": len(hits),
            "items": [{"code": h["code"], "label": h["label"]} for h in hits[:TOP_CATEGORY]],
        })

    # ---------------- 패턴 × 기간
    pat_out = []
    for key, (name, desc, fn) in pat.PATTERNS.items():
        periods = {}
        for pkey, (plabel, lookback) in pat.PERIODS.items():
            hits = []
            for code, v in enriched.items():
                try:
                    hit = fn(v["d"], lookback)
                except Exception:
                    continue
                if hit.matched and hit.score >= 40:
                    hits.append({"code": code, "score": round(hit.score, 1),
                                 "span": list(hit.span) if hit.span else None,
                                 "detail": hit.detail})
            hits.sort(key=lambda h: h["score"], reverse=True)
            periods[pkey] = {"label": plabel, "lookback": lookback,
                             "count": len(hits), "items": hits[:TOP_PATTERN]}
        pat_out.append({"key": key, "name": name, "desc": desc, "periods": periods})

    # ---------------- 차트 데이터 임베드
    # 검색이 생기면서 유니버스 전체를 담는다. 오늘 아무 신호에도 안 걸린 종목도
    # 이름으로 찾아 들어가 차트를 볼 수 있어야 한다 — 검색 결과가 막다른 길이면
    # 검색이 아니다.
    used = set(enriched)

    stocks = {}
    rng = np.random.default_rng(20260908)
    for code in sorted(used):
        v = enriched[code]
        d = v["d"]
        tail = d.tail(CHART_DAYS)
        last, prev = d.iloc[-1], d.iloc[-2]
        digits = 0 if v["market"] == "KR" else 2

        stocks[code] = {
            "name": v["name"],
            "market": v["market"],
            "close": round(float(last["Close"]), digits),
            "chg": round(float(last["chg"]) * 100, 2),
            "value": int(last["Value"]),
            "volRatio": round(float(last["Volume"] / max(last["vol_ma20"], 1)), 2),
            "dates": [d.strftime("%m/%d") for d in tail.index],
            "o": [round(float(x), digits) for x in tail["Open"]],
            "h": [round(float(x), digits) for x in tail["High"]],
            "l": [round(float(x), digits) for x in tail["Low"]],
            "c": [round(float(x), digits) for x in tail["Close"]],
            "v": [int(x) for x in tail["Volume"]],
            "ma20": [None if pd.isna(x) else round(float(x), digits) for x in tail["ma20"]],
            "ma60": [None if pd.isna(x) else round(float(x), digits) for x in tail["ma60"]],
            "spark": [round(float(x), digits) for x in d["Close"].tail(SPARK_DAYS)],
            "offset": len(d) - CHART_DAYS,   # span 인덱스를 차트 좌표로 옮길 때 사용
            "W": weekly(d, digits),
        }
        # 분봉은 데모에서만 만든다. 실제 데이터에는 분봉 소스가 없어서,
        # 그때는 키가 아예 없고 앱이 '분봉 없음'을 그대로 표시한다.
        if demo:
            stocks[code]["M"] = intraday_demo(last, v["market"], digits, rng)

    # ---------------- 정세 (뉴스 · 지표 · 일정)
    macro = dday = news = None
    if not demo:
        try:
            import news as news_mod
            g = news_mod.gather()
            macro, dday, news = g["macro"], g["dday"], g["news"]
            print(f"정세 — 뉴스 {len(news or [])}건 / 지표 {len(macro or [])}개 / 일정 {len(dday or [])}건")
        except Exception as e:
            print(f"정세 수집 실패 (앱에서는 해당 영역이 숨겨집니다): {e}")

    if demo:
        # 데모에서는 실존 기사·실제 금리를 지어내지 않는다. 슬롯 설명만 넣는다.
        macro = [
            {"label": "한국 기준금리", "value": "—"},
            {"label": "미국 기준금리", "value": "—"},
            {"label": "원/달러", "value": "—"},
            {"label": "미 10년물", "value": "—"},
        ]
        dday = None
        news = [
            {"slot": "국내 금융·정책",
             "title": "이 자리에 한국은행·금리·환율·정책 관련 헤드라인이 들어갑니다",
             "source": "(언론사 RSS)", "placeholder": True},
            {"slot": "해외 금융",
             "title": "연준·미국 지표·국채금리 관련 헤드라인이 들어갑니다",
             "source": "(언론사 RSS)", "placeholder": True},
            {"slot": "지정학·정치",
             "title": "선거·관세·분쟁 등 시장에 닿는 정치 헤드라인이 들어갑니다",
             "source": "(언론사 RSS)", "placeholder": True},
        ]

    # 기준일은 데이터에서 뽑는다. 하드코딩하면 어느 순간 조용히 거짓말이 된다.
    last_dates = [v["d"].index.max() for v in enriched.values() if len(v["d"])]
    as_of = max(last_dates).strftime("%Y-%m-%d") if last_dates else "—"

    try:
        import config as _cfg
        live_endpoint = (_cfg.LIVE_ENDPOINT or "").rstrip("/")
    except Exception:
        live_endpoint = ""

    kr = sum(1 for v in data.values() if v["market"] == "KR")
    return {
        "is_demo": demo,
        "live_endpoint": live_endpoint,
        "backtest": backtest_data,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": as_of,
        "universe": {"total": len(data), "KR": kr, "US": len(data) - kr},
        "macro": macro, "dday": dday, "news": news,
        "categories": cat_out, "patterns": pat_out, "stocks": stocks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--refresh-bt", action="store_true", help="성적표 다시 계산")
    ap.add_argument("--no-bt", action="store_true", help="성적표 생략")
    args = ap.parse_args()

    data = load_real() if args.real else load_demo()
    print(f"스캔 대상 {len(data)}종목")

    enriched = {c: {**v, "d": ind.enrich(v["df"])} for c, v in data.items()}
    bt = None if args.no_bt else load_or_run_backtest(enriched, args.refresh_bt)

    result = run(data, demo=not args.real, backtest_data=bt, enriched=enriched)

    out = Path("public/data")
    out.mkdir(parents=True, exist_ok=True)
    p = out / "scan.json"
    p.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"\n차트 임베드 종목 {len(result['stocks'])}개")
    print(f"{p} — {p.stat().st_size/1024:.0f} KB\n")

    print("카테고리")
    for c in result["categories"]:
        print(f"  {c['name']:18s} {c['count']:4d}종목")
    print("\n패턴 (기간별 발견 수)")
    for pp in result["patterns"]:
        counts = " / ".join(f"{v['label']} {v['count']}" for v in pp["periods"].values())
        print(f"  {pp['name']:18s} {counts}")

    if bt:
        m = bt["mid"]
        b5, b10 = m["baseline"]["h5"], m["baseline"]["h10"]
        yrs = f"{m['years']}년" if m.get("years") else "전 구간"
        print(f"\n성적표 (중기 창 · {yrs} · 기준선 1주 {b5['win']}% / 2주 {b10['win']}%)")
        for k, r in m["patterns"].items():
            if r["h10"]["n"] == 0:
                continue
            h5, h10 = r["h5"], r["h10"]
            mark = "기준선 상회" if r["beats"] else "기준선 미달"
            print(f"  {r['name']:18s} 1주 {h5['win']:5.1f}% ({h5['win_edge']:+5.1f}p)"
                  f"  2주 {h10['win']:5.1f}% ({h10['win_edge']:+5.1f}p)  {mark}  n={r['n']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
