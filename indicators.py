"""기술 지표. 전부 순수 함수 — 일봉 DataFrame 하나만 받는다."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI. period=2면 단기 과매도 판별용."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """일봉에 지표 컬럼을 붙인다."""
    d = df.copy()
    c, v = d["Close"], d["Volume"]

    for n in (5, 20, 60, 120):
        d[f"ma{n}"] = c.rolling(n, min_periods=n).mean()

    d["vol_ma20"] = v.rolling(20, min_periods=5).mean()
    d["value_ma20"] = d["Value"].rolling(20, min_periods=5).mean()

    d["rsi2"] = rsi(c, 2)
    d["rsi14"] = rsi(c, 14)

    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std()
    d["bb_mid"] = mid
    d["bb_up"] = mid + 2 * sd
    d["bb_low"] = mid - 2 * sd
    # 밴드폭: 스퀴즈(수축) 판별용. 중심선 대비 비율이라 종목 간 비교가 된다.
    d["bb_width"] = (d["bb_up"] - d["bb_low"]) / mid

    d["high52"] = c.rolling(250, min_periods=60).max()
    d["low52"] = c.rolling(250, min_periods=60).min()
    d["high60"] = c.rolling(60, min_periods=30).max()

    d["chg"] = c.pct_change()
    return d


# --------------------------------------------------------------- 형태 유사도
def normalize_curve(values: np.ndarray) -> np.ndarray:
    """0~1로 정규화. 가격대가 다른 종목끼리 형태만 비교하기 위한 전처리."""
    lo, hi = float(np.min(values)), float(np.max(values))
    if hi - lo < 1e-9:
        return np.full_like(values, 0.5, dtype=float)
    return (values - lo) / (hi - lo)


def resample_curve(values: np.ndarray, n: int) -> np.ndarray:
    """길이가 다른 구간을 n개 점으로 리샘플링 (선형 보간)."""
    if len(values) == n:
        return values.astype(float)
    src = np.linspace(0, 1, len(values))
    dst = np.linspace(0, 1, n)
    return np.interp(dst, src, values.astype(float))


def shape_similarity(window: np.ndarray, template: np.ndarray) -> float:
    """정규화된 두 곡선의 유사도 → 0~100.

    거리 기반(1 - 평균절대오차)이라 해석이 직관적이다. 형태가 완전히 겹치면
    100, 평균적으로 정규화 범위의 절반씩 어긋나면 50.
    """
    if len(window) < 4:
        return 0.0
    w = normalize_curve(resample_curve(window, len(template)))
    t = normalize_curve(np.asarray(template, dtype=float))
    mae = float(np.mean(np.abs(w - t)))
    return float(np.clip((1 - mae * 2) * 100, 0, 100))


def local_extrema(values: np.ndarray, order: int = 5) -> tuple[list[int], list[int]]:
    """국소 저점·고점 인덱스. order일 좌우에서 가장 낮/높으면 극점으로 본다."""
    lows, highs = [], []
    n = len(values)
    for i in range(order, n - order):
        seg = values[i - order : i + order + 1]
        if values[i] == seg.min() and np.argmin(seg) == order:
            lows.append(i)
        if values[i] == seg.max() and np.argmax(seg) == order:
            highs.append(i)
    return lows, highs
