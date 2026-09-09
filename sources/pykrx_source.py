"""pykrx 기반 국내 소스.

지수 구성종목(코스피200 / 코스닥150)을 정확히 뽑을 수 있는 게 핵심 이유.
거래대금(Value)도 직접 주기 때문에 국내는 이쪽이 1순위.
"""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd

import config
from sources.base import DataSource, SourceError


class PykrxSource(DataSource):
    name = "pykrx"
    markets = frozenset({"KR"})

    def __init__(self) -> None:
        # pykrx는 임포트할 때 "KRX 로그인 실패: KRX_ID ... 설정되지 않았습니다"를
        # 찍는다. 우리가 쓰는 공개 데이터에는 로그인이 필요 없어서 무해한
        # 안내인데, 처음 실행하는 사람 눈에는 에러로 보인다. 그래서 삼킨다.
        import contextlib
        import io
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                from pykrx import stock  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise SourceError(f"pykrx 미설치: {e}") from e

    # ------------------------------------------------------------------
    def get_universe(self, market: str) -> pd.DataFrame:
        if market != "KR":
            raise SourceError(f"{self.name}는 KR만 지원")

        from pykrx import stock

        ref = _recent_business_day()
        rows = []
        for index_name, index_code in config.KRX_INDEX_CODES.items():
            try:
                codes = stock.get_index_portfolio_deposit_file(index_code, ref)
            except Exception as e:
                raise SourceError(f"{index_name} 구성종목 실패: {e}") from e
            if not codes:
                raise SourceError(f"{index_name} 구성종목이 비어 있음 (기준일 {ref})")
            for code in codes:
                rows.append(
                    {
                        "code": code,
                        "name": _safe_name(stock, code),
                        "market": "KR",
                        "source_index": index_name,
                    }
                )
            time.sleep(config.SLEEP_BETWEEN_CALLS)

        df = pd.DataFrame(rows, columns=config.UNIVERSE_COLUMNS)
        # 두 지수에 동시에 들어가는 일은 없지만 방어적으로.
        return df.drop_duplicates(subset="code").reset_index(drop=True)

    # ------------------------------------------------------------------
    def get_ohlcv(self, code: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        from pykrx import stock

        try:
            df = stock.get_market_ohlcv(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code
            )
        except Exception as e:
            raise SourceError(f"{code} 일봉 실패: {e}") from e

        time.sleep(config.SLEEP_BETWEEN_CALLS)
        return self._normalize_ohlcv(df)


# ---------------------------------------------------------------------- utils
def _safe_name(stock_mod, code: str) -> str:
    try:
        return stock_mod.get_market_ticker_name(code)
    except Exception:
        return code


def _recent_business_day() -> str:
    """구성종목 조회 기준일. 주말이면 직전 금요일로 당긴다."""
    d = dt.date.today()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.strftime("%Y%m%d")
