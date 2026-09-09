"""폴백 체인.

한 소스가 죽어도 다음 소스로 넘어가고, 어느 소스가 실제로 답했는지 기록한다.
"어제는 되던 게 오늘 안 된다"는 이 프로젝트의 기본 전제라서, 무엇이 실패했는지
로그로 남는 게 중요하다.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from sources.base import DataSource, SourceError

log = logging.getLogger("sources")


class SourceRegistry:
    def __init__(self, sources: list[DataSource]) -> None:
        self.sources = sources
        self.stats: dict[str, dict[str, int]] = {
            s.name: {"ok": 0, "fail": 0} for s in sources
        }

    # ------------------------------------------------------------------
    def _candidates(self, market: str) -> list[DataSource]:
        return [s for s in self.sources if market in s.markets]

    def get_universe(self, market: str) -> tuple[pd.DataFrame, str]:
        errors = []
        for src in self._candidates(market):
            try:
                df = src.get_universe(market)
                if len(df) == 0:
                    raise SourceError("빈 유니버스")
                self.stats[src.name]["ok"] += 1
                log.info("universe[%s] ← %s (%d종목)", market, src.name, len(df))
                return df, src.name
            except Exception as e:
                self.stats[src.name]["fail"] += 1
                errors.append(f"{src.name}: {e}")
                log.warning("universe[%s] %s 실패 → 다음 소스", market, src.name)
        raise SourceError(f"{market} 유니버스를 어떤 소스로도 못 만듦\n  " + "\n  ".join(errors))

    def get_ohlcv(
        self, code: str, market: str, start: dt.date, end: dt.date
    ) -> tuple[pd.DataFrame, str]:
        errors = []
        for src in self._candidates(market):
            try:
                df = src.get_ohlcv(code, start, end)
                self.stats[src.name]["ok"] += 1
                return df, src.name
            except Exception as e:
                self.stats[src.name]["fail"] += 1
                errors.append(f"{src.name}: {e}")
        raise SourceError(f"{code} 일봉 실패 — " + " | ".join(errors))

    # ------------------------------------------------------------------
    def report(self) -> str:
        lines = ["소스별 성공/실패:"]
        for name, s in self.stats.items():
            total = s["ok"] + s["fail"]
            if total:
                lines.append(f"  {name:10s} ok={s['ok']:5d}  fail={s['fail']:5d}")
        return "\n".join(lines)


def default_registry() -> SourceRegistry:
    """국내는 pykrx 우선, 미국은 fdr 우선, 그 뒤로 폴백."""
    from sources.fdr_source import FdrSource, SeedListSource
    from sources.pykrx_source import PykrxSource

    sources: list[DataSource] = []
    for factory in (PykrxSource, FdrSource, SeedListSource):
        try:
            sources.append(factory())
        except SourceError as e:
            log.warning("소스 초기화 실패 (%s): %s", factory.__name__, e)
    if not sources:
        raise SourceError("사용 가능한 소스가 하나도 없음")
    return SourceRegistry(sources)
