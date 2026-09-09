"""Parquet 증분 저장.

전체 재수집은 하지 않는다. 마지막 저장일 다음날부터만 받아서 붙인다.
매일 250일치를 500번 다시 받는 건 소스에 대한 민폐이자, 차단당하는 지름길이다.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

import config

log = logging.getLogger("store")


def _path(market: str, code: str):
    d = config.OHLCV_DIR / market
    d.mkdir(parents=True, exist_ok=True)
    # 미국 티커에 들어갈 수 있는 문자를 파일명에서 걷어낸다 (BRK.B, BF.B 등)
    safe = code.replace("/", "_").replace(".", "-")
    return d / f"{safe}.parquet"


def load(market: str, code: str) -> pd.DataFrame | None:
    p = _path(market, code)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"
        return df.sort_index()
    except Exception as e:
        log.warning("%s/%s 캐시 손상 → 재수집: %s", market, code, e)
        p.unlink(missing_ok=True)
        return None


def save(market: str, code: str, df: pd.DataFrame) -> None:
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(_path(market, code), compression="snappy")


def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """새 데이터가 이긴다. 소스가 과거 수치를 정정하는 경우가 있기 때문."""
    if old is None or len(old) == 0:
        return new
    merged = pd.concat([old, new])
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def fetch_start(market: str, code: str, full_start: dt.date) -> tuple[dt.date, pd.DataFrame | None]:
    """이번에 어디서부터 받아야 하는지 계산한다.

    캐시가 있으면 마지막 날짜에서 3거래일 겹쳐서 받는다. 겹치는 구간은 정정
    반영용이고, merge가 알아서 최신값으로 덮는다.
    """
    cached = load(market, code)
    if cached is None or len(cached) == 0:
        return full_start, None
    last = cached.index.max().date()
    start = max(full_start, last - dt.timedelta(days=5))
    return start, cached


def prune(market: str, code: str, keep_from: dt.date) -> None:
    """오래된 구간을 잘라 리포지토리가 무한히 커지는 걸 막는다."""
    df = load(market, code)
    if df is None:
        return
    trimmed = df[df.index >= pd.Timestamp(keep_from)]
    if len(trimmed) != len(df):
        save(market, code, trimmed)
