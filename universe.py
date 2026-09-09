"""유니버스 확정 로직.

지수 구성종목(약 850) → 유동성·이력 필터 → 시장별 상위 N(합계 약 500).

여기 함수들은 전부 순수 함수다. 네트워크를 타지 않으므로 합성 데이터로
단위 테스트가 가능하고, 실제로 tests/test_offline.py 가 그렇게 검증한다.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import config


def liquidity_metrics(ohlcv: pd.DataFrame, lookback: int = config.LOOKBACK_FOR_LIQUIDITY) -> dict:
    """한 종목의 일봉에서 유동성 지표를 뽑는다."""
    if ohlcv is None or len(ohlcv) == 0:
        return {"avg_value": 0.0, "n_days": 0, "last_date": pd.NaT, "last_close": float("nan")}
    tail = ohlcv.tail(lookback)
    return {
        "avg_value": float(tail["Value"].mean()),
        "n_days": int(len(ohlcv)),
        "last_date": ohlcv.index.max(),
        "last_close": float(ohlcv["Close"].iloc[-1]),
    }


def build_table(candidates: pd.DataFrame, metrics: dict[str, dict]) -> pd.DataFrame:
    """후보 목록에 지표를 붙인 전체 표."""
    rows = []
    for row in candidates.to_dict("records"):
        m = metrics.get(row["code"])
        if m is None:
            continue
        rows.append({**row, **m})
    if not rows:
        return pd.DataFrame(columns=list(candidates.columns) + ["avg_value", "n_days", "last_date", "last_close"])
    return pd.DataFrame(rows)


def select(
    table: pd.DataFrame,
    market: str,
    top_n: int,
    min_value: float = 0.0,
    min_history: int = config.MIN_HISTORY_DAYS,
    as_of: dt.date | None = None,
    max_staleness_days: int = 7,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """필터를 순서대로 적용하고, 각 단계에서 몇 개가 떨어졌는지도 함께 돌려준다.

    떨어진 개수를 세는 이유: 어느 날 유니버스가 갑자기 80종목이 되면
    '어느 필터가' 잡아먹었는지 로그만 보고 바로 알 수 있어야 하기 때문.
    """
    dropped: dict[str, int] = {}
    df = table[table["market"] == market].copy()
    dropped["시작"] = len(df)

    before = len(df)
    df = df[df["n_days"] >= min_history]
    dropped[f"이력 {min_history}일 미만"] = before - len(df)

    if min_value > 0:
        before = len(df)
        df = df[df["avg_value"] >= min_value]
        dropped[f"거래대금 {min_value/1e8:.0f}억 미만"] = before - len(df)

    if as_of is not None and len(df):
        cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=max_staleness_days)
        before = len(df)
        df = df[pd.to_datetime(df["last_date"]) >= cutoff]
        dropped[f"최근 {max_staleness_days}일 내 시세 없음"] = before - len(df)

    df = df.sort_values("avg_value", ascending=False)
    before = len(df)
    df = df.head(top_n)
    dropped[f"거래대금 상위 {top_n} 밖"] = max(0, before - len(df))

    dropped["최종"] = len(df)
    return df.reset_index(drop=True), dropped


def format_drop_report(market: str, dropped: dict[str, int]) -> str:
    lines = [f"[{market}] 유니버스 필터"]
    for k, v in dropped.items():
        mark = "→" if k in ("시작", "최종") else " -"
        lines.append(f"   {mark} {k:28s} {v:5d}")
    return "\n".join(lines)
