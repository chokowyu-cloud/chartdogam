"""네트워크 없이 파이프라인 로직을 검증한다.

실제 시세 소스는 언제든 막힐 수 있으므로, 로직의 정합성만큼은 소스와 무관하게
확인할 수 있어야 한다. 합성 데이터로 정규화·증분병합·필터·폴백을 전부 돌린다.

    python -m tests.test_offline
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import console  # noqa: E402

console.setup()

_TMP = Path(tempfile.mkdtemp(prefix="chartdogam-test-"))
config.OHLCV_DIR = _TMP / "ohlcv"
config.DATA_DIR = _TMP
config.OUT_DIR = _TMP / "public"

import store  # noqa: E402
import universe as uni  # noqa: E402
from sources.base import DataSource, SourceError  # noqa: E402
from sources.registry import SourceRegistry  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


# ------------------------------------------------------------------ 합성 데이터
def synth(days: int, seed: int, start: dt.date, price: float = 50_000, vol: int = 300_000,
          korean_cols: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=days)
    close = price * np.cumprod(1 + rng.normal(0.0004, 0.018, days))
    high = close * (1 + abs(rng.normal(0, 0.008, days)))
    low = close * (1 - abs(rng.normal(0, 0.008, days)))
    open_ = (high + low) / 2
    volume = rng.integers(vol // 2, vol * 2, days)
    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )
    if korean_cols:   # pykrx 스타일 컬럼 + 거래대금 제공
        df["거래대금"] = df["Close"] * df["Volume"]
        df = df.rename(columns={"Open": "시가", "High": "고가", "Low": "저가",
                                "Close": "종가", "Volume": "거래량"})
    return df


class FakeSource(DataSource):
    name = "fake"
    markets = frozenset({"KR", "US"})

    def __init__(self, tickers, korean_cols=False, fail_codes=frozenset()):
        self.tickers = tickers
        self.korean_cols = korean_cols
        self.fail_codes = fail_codes
        self.calls = 0

    def get_universe(self, market):
        return pd.DataFrame(
            [{"code": c, "name": f"종목{c}", "market": market, "source_index": "TEST"}
             for c in self.tickers],
            columns=config.UNIVERSE_COLUMNS,
        )

    def get_ohlcv(self, code, start, end):
        self.calls += 1
        if code in self.fail_codes:
            raise SourceError("의도된 실패")
        n = max(1, np.busday_count(start, end))
        seed = abs(hash(code)) % 10_000
        # 유동성에 차등을 줘서 상위 N 선별이 실제로 작동하는지 본다
        vol = 100_000 * (1 + self.tickers.index(code))
        return self._normalize_ohlcv(
            synth(int(n), seed, start, vol=vol, korean_cols=self.korean_cols)
        )


class DeadSource(DataSource):
    name = "dead"
    markets = frozenset({"KR", "US"})

    def get_universe(self, market):
        raise SourceError("소스 다운")

    def get_ohlcv(self, code, start, end):
        raise SourceError("소스 다운")


# ------------------------------------------------------------------ 테스트
def test_normalize():
    print("\n[1] 컬럼 정규화 — 한글/영문 소스가 같은 형태로 나오는가")
    src = FakeSource(["005930"], korean_cols=True)
    df = src.get_ohlcv("005930", dt.date(2026, 1, 1), dt.date(2026, 3, 1))
    check("규약 컬럼과 정확히 일치", list(df.columns) == config.OHLCV_COLUMNS, str(list(df.columns)))
    check("인덱스가 DatetimeIndex", isinstance(df.index, pd.DatetimeIndex))
    check("인덱스명 Date", df.index.name == "Date")
    check("오름차순 정렬", df.index.is_monotonic_increasing)
    check("거래대금 보존(계산값 아님)", np.allclose(df["Value"], df["Close"] * df["Volume"]))

    us = FakeSource(["AAPL"]).get_ohlcv("AAPL", dt.date(2026, 1, 1), dt.date(2026, 3, 1))
    check("Value 없는 소스는 자동 계산", np.allclose(us["Value"], us["Close"] * us["Volume"]))


def test_normalize_rejects_garbage():
    print("\n[2] 쓰레기 응답 거부")
    src = FakeSource([])
    for label, bad in [
        ("빈 DataFrame", pd.DataFrame()),
        ("컬럼 누락", pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2026-01-02"]))),
    ]:
        try:
            src._normalize_ohlcv(bad)
            check(f"{label} → SourceError", False, "예외가 안 남")
        except SourceError:
            check(f"{label} → SourceError", True)

    dirty = pd.DataFrame(
        {"Open": [1, 1, 1], "High": [1, 1, 1], "Low": [1, 1, 1],
         "Close": [100.0, 0.0, 102.0], "Volume": [10, 0, 12]},
        index=pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
    )
    out = src._normalize_ohlcv(dirty)
    check("거래정지(종가 0) 행 제거", len(out) == 2 and 0 not in out["Close"].values)


def test_store_roundtrip_and_merge():
    print("\n[3] 저장 · 증분 병합")
    base = dt.date(2026, 1, 1)
    a = FakeSource(["AAPL"])._normalize_ohlcv(synth(60, 1, base))
    store.save("US", "AAPL", a)
    back = store.load("US", "AAPL")
    check("parquet 왕복 후 동일", back is not None and len(back) == len(a))

    # 겹치지 않는 구간을 이어붙이면 행수가 정확히 합쳐져야 한다
    gap_start = (a.index.max() + pd.Timedelta(days=4)).date()
    later = FakeSource(["AAPL"])._normalize_ohlcv(synth(20, 2, gap_start))
    merged = store.merge(a, later)
    check("증분 병합 행수", len(merged) == len(a) + len(later), f"{len(merged)}")
    check("중복 인덱스 없음", not merged.index.has_duplicates)
    check("병합 후에도 정렬 유지", merged.index.is_monotonic_increasing)

    # 겹치는 구간은 새 값이 이겨야 한다 (소스의 사후 정정 반영)
    overlap = a.tail(3).copy()
    overlap["Close"] = 99_999.0
    m2 = store.merge(a, overlap)
    check("겹침 구간은 새 값 우선", float(m2["Close"].iloc[-1]) == 99_999.0)
    check("겹쳐도 행수 안 늘어남", len(m2) == len(a))

    start, cached = store.fetch_start("US", "AAPL", base)
    check("증분 시작일이 뒤로 밀림", start > base and cached is not None, f"start={start}")
    s2, c2 = store.fetch_start("US", "NOPE", base)
    check("캐시 없으면 전체 기간", s2 == base and c2 is None)


def test_dotted_ticker():
    print("\n[4] 점 있는 미국 티커 (BRK.B)")
    df = FakeSource(["BRK.B"])._normalize_ohlcv(synth(30, 7, dt.date(2026, 1, 1)))
    store.save("US", "BRK.B", df)
    back = store.load("US", "BRK.B")
    check("BRK.B 저장·로드", back is not None and len(back) == 30)
    check("파일명에 점 없음", (config.OHLCV_DIR / "US" / "BRK-B.parquet").exists())


def test_fallback():
    print("\n[5] 폴백 체인")
    alive = FakeSource(["AAA", "BBB"])
    reg = SourceRegistry([DeadSource(), alive])
    df, used = reg.get_universe("US")
    check("죽은 소스를 건너뛰고 성공", used == "fake" and len(df) == 2)
    check("실패 카운트 기록", reg.stats["dead"]["fail"] == 1)

    reg2 = SourceRegistry([DeadSource()])
    try:
        reg2.get_universe("US")
        check("전부 죽으면 예외", False)
    except SourceError as e:
        check("전부 죽으면 예외", True, str(e).splitlines()[0][:50])

    # 시장을 지원하지 않는 소스는 후보에서 아예 빠져야 한다
    class KrOnly(FakeSource):
        name = "kronly"
        markets = frozenset({"KR"})
    reg3 = SourceRegistry([KrOnly(["X"]), FakeSource(["AAA"])])
    _, used3 = reg3.get_universe("US")
    check("시장 미지원 소스는 후보 제외", used3 == "fake")


def test_universe_filters():
    print("\n[6] 유니버스 필터")
    today = dt.date(2026, 9, 7)
    rows, metrics = [], {}
    for i in range(40):
        code = f"{i:06d}"
        rows.append({"code": code, "name": f"종목{i}", "market": "KR", "source_index": "KOSPI200"})
        metrics[code] = {
            "avg_value": float((i + 1) * 1e9),          # 10억 ~ 400억
            "n_days": 200 if i >= 5 else 40,             # 앞 5개는 이력 부족
            "last_date": pd.Timestamp(today) - pd.Timedelta(days=1 if i != 9 else 30),
            "last_close": 10_000.0,
        }
    table = uni.build_table(pd.DataFrame(rows), metrics)
    check("표 생성", len(table) == 40)

    sel, dropped = uni.select(table, "KR", top_n=10, min_value=3e9, as_of=today)
    check("이력 부족 5종목 탈락", dropped["이력 150일 미만"] == 5, str(dropped))
    check("거래대금 미달 탈락", dropped["거래대금 30억 미만"] == 0)
    check("시세 정체 종목 탈락", dropped["최근 7일 내 시세 없음"] == 1)
    check("상위 N 컷", dropped["최종"] == 10)
    check("거래대금 내림차순", sel["avg_value"].is_monotonic_decreasing)
    check("최상위가 가장 큰 종목", sel["code"].iloc[0] == "000039")

    empty, d2 = uni.select(table, "US", top_n=10, as_of=today)
    check("없는 시장은 빈 결과", len(empty) == 0 and d2["시작"] == 0)

    lowvol = uni.select(table, "KR", top_n=10, min_value=1e12, as_of=today)[1]
    check("문턱이 높으면 전멸이 보고됨", lowvol["최종"] == 0)


def test_end_to_end():
    print("\n[7] 통합 — 수집부터 유니버스 확정까지")
    tickers = [f"T{i:03d}" for i in range(12)]
    reg = SourceRegistry([FakeSource(tickers, fail_codes={"T003"})])
    today, full_start = dt.date(2026, 9, 7), dt.date(2026, 9, 7) - dt.timedelta(days=420)

    cand, _ = reg.get_universe("US")
    metrics, failures = {}, []
    for code in cand["code"]:
        start, cached = store.fetch_start("US", code, full_start)
        try:
            fresh, _ = reg.get_ohlcv(code, "US", start, today)
            merged = store.merge(cached, fresh)
            store.save("US", code, merged)
            metrics[code] = uni.liquidity_metrics(merged)
        except SourceError:
            failures.append(code)

    check("의도된 1건만 실패", failures == ["T003"], str(failures))
    sel, dropped = uni.select(uni.build_table(cand, metrics), "US", top_n=5, as_of=today)
    check("상위 5종목 선별", len(sel) == 5)
    check("실패 종목은 유니버스에 없음", "T003" not in set(sel["code"]))
    check("유동성 큰 순", sel["avg_value"].is_monotonic_decreasing)

    # 두 번째 실행은 캐시를 타야 한다 — 증분이 실제로 동작하는지
    calls_after_first = reg.sources[0].calls
    incremental, from_scratch = [], []
    for code in cand["code"]:
        start, cached = store.fetch_start("US", code, full_start)
        (incremental if (cached is not None and start > full_start) else from_scratch).append(code)

    check("재실행 시 수집 성공분은 증분 구간만 요청",
          set(incremental) == set(tickers) - {"T003"}, f"증분 {len(incremental)}종목")
    check("실패해서 캐시 없는 종목만 전체 기간 재요청",
          from_scratch == ["T003"], str(from_scratch))
    check("1회차 호출 수 = 종목 수", calls_after_first == len(tickers), f"{calls_after_first}")


def main() -> int:
    print("=" * 62)
    print("차트도감 — 오프라인 파이프라인 검증")
    print("=" * 62)
    for fn in (test_normalize, test_normalize_rejects_garbage, test_store_roundtrip_and_merge,
               test_dotted_ticker, test_fallback, test_universe_filters, test_end_to_end):
        fn()
    print("\n" + "=" * 62)
    print(f"통과 {len(PASS)} / 실패 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  실패:", f)
    print("=" * 62)
    shutil.rmtree(_TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
