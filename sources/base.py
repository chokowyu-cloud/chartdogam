"""데이터 소스 인터페이스.

수집 코드 전체가 알아야 하는 건 이 두 개의 메서드뿐이다.
나중에 유료 API로 갈아탈 때 이 인터페이스를 구현한 파일 하나만 새로 쓰면 된다.
"""
from __future__ import annotations

import abc
import datetime as dt

import pandas as pd

import config


class SourceError(RuntimeError):
    """이 소스로는 못 가져왔다는 뜻. 상위에서 다음 소스로 폴백한다."""


class DataSource(abc.ABC):
    name: str = "base"
    #: 이 소스가 담당하는 시장. {"KR"}, {"US"}, 또는 둘 다.
    markets: frozenset[str] = frozenset()

    @abc.abstractmethod
    def get_universe(self, market: str) -> pd.DataFrame:
        """지수 구성종목 목록.

        Returns
        -------
        DataFrame[code, name, market, source_index]
        """

    @abc.abstractmethod
    def get_ohlcv(self, code: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """일봉.

        Returns
        -------
        DatetimeIndex(name="Date") + [Open, High, Low, Close, Volume, Value]
        Value(거래대금)를 소스가 안 주면 Close * Volume 으로 채운다.
        """

    # ------------------------------------------------------------ 공통 정규화
    @staticmethod
    def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        """소스마다 다른 컬럼명을 규약대로 맞추고 타입을 정리한다."""
        if df is None or len(df) == 0:
            raise SourceError("빈 응답")

        rename = {
            "시가": "Open", "고가": "High", "저가": "Low",
            "종가": "Close", "거래량": "Volume", "거래대금": "Value",
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
            "Adj Close": "AdjClose",
        }
        df = df.rename(columns=rename)

        missing = {"Open", "High", "Low", "Close", "Volume"} - set(df.columns)
        if missing:
            raise SourceError(f"필수 컬럼 누락: {sorted(missing)}")

        if "Value" not in df.columns:
            df["Value"] = df["Close"] * df["Volume"]

        df = df[config.OHLCV_COLUMNS].copy()
        df = df.apply(pd.to_numeric, errors="coerce")

        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"
        df = df[~df.index.duplicated(keep="last")].sort_index()

        # 거래정지·휴장으로 생긴 0/결측 행은 패턴 판별을 망가뜨리므로 버린다.
        df = df[(df["Close"] > 0) & df["Close"].notna()]
        return df
