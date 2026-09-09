"""FinanceDataReader 기반 소스. 국내·미국 모두 커버하며 폴백으로도 쓴다."""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd

import config
from sources.base import DataSource, SourceError


class FdrSource(DataSource):
    name = "fdr"
    markets = frozenset({"KR", "US"})

    def __init__(self) -> None:
        try:
            import FinanceDataReader  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise SourceError(f"finance-datareader 미설치: {e}") from e

    # ------------------------------------------------------------------
    def get_universe(self, market: str) -> pd.DataFrame:
        import FinanceDataReader as fdr

        if market == "US":
            try:
                df = fdr.StockListing("S&P500")
            except Exception as e:
                raise SourceError(f"S&P500 목록 실패: {e}") from e
            if df is None or len(df) == 0:
                raise SourceError("S&P500 목록이 비어 있음")
            code_col = _first_present(df, ["Symbol", "Code", "Ticker"])
            name_col = _first_present(df, ["Name", "Security", "Company"])
            out = pd.DataFrame(
                {
                    "code": df[code_col].astype(str).str.strip(),
                    "name": df[name_col].astype(str).str.strip(),
                    "market": "US",
                    "source_index": "S&P500",
                }
            )
        elif market == "KR":
            # pykrx가 죽었을 때의 폴백. 지수 구성종목이 아니라 전체 상장사가
            # 나오므로, 뒤에서 거래대금 필터가 훨씬 무겁게 일하게 된다.
            try:
                df = fdr.StockListing("KRX")
            except Exception as e:
                raise SourceError(f"KRX 목록 실패: {e}") from e
            code_col = _first_present(df, ["Code", "Symbol"])
            name_col = _first_present(df, ["Name"])
            out = pd.DataFrame(
                {
                    "code": df[code_col].astype(str).str.zfill(6),
                    "name": df[name_col].astype(str).str.strip(),
                    "market": "KR",
                    "source_index": "KRX-ALL",
                }
            )
        else:
            raise SourceError(f"알 수 없는 시장: {market}")

        out = out[out["code"].str.len() > 0]
        return out.drop_duplicates(subset="code").reset_index(drop=True)

    # ------------------------------------------------------------------
    def get_ohlcv(self, code: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        import FinanceDataReader as fdr

        try:
            df = fdr.DataReader(code, start.isoformat(), end.isoformat())
        except Exception as e:
            raise SourceError(f"{code} 일봉 실패: {e}") from e

        time.sleep(config.SLEEP_BETWEEN_CALLS)
        return self._normalize_ohlcv(df)


class SeedListSource(DataSource):
    """최후의 보루. 리포지토리에 번들된 정적 종목 리스트.

    네트워크가 통째로 막혀도 유니버스는 만들어진다. 구성종목이 조금 낡을 수는
    있어도, 파이프라인 전체가 멈추는 것보다는 낫다.
    """

    name = "seed"
    markets = frozenset({"US", "KR"})

    def get_universe(self, market: str) -> pd.DataFrame:
        if market == "US":
            path, code_col, name_col, label = config.SEED_SP500, "Symbol", "Security", "S&P500(seed)"
        elif market == "KR":
            # 국내는 종목코드가 0으로 시작한다(005930). 문자열로 읽지 않으면 5930이 된다.
            path, code_col, name_col, label = config.SEED_KRX, "code", "name", "KRX주요(seed)"
        else:
            raise SourceError(f"지원하지 않는 시장: {market}")
        if not path.exists():
            raise SourceError(f"번들 리스트 없음: {path.name}")

        df = pd.read_csv(path, dtype=str)
        return pd.DataFrame(
            {
                "code": df[code_col].astype(str).str.strip(),
                "name": df[name_col].astype(str).str.strip(),
                "market": market,
                "source_index": label,
            }
        ).drop_duplicates(subset="code").reset_index(drop=True)

    def get_ohlcv(self, code: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        raise SourceError("seed 소스는 시세를 제공하지 않음")


def _first_present(df: pd.DataFrame, candidates: list[str]) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise SourceError(f"컬럼 후보 {candidates} 중 아무것도 없음. 실제: {list(df.columns)[:12]}")
