"""카테고리 스캐너 — 9종.

패턴이 '모양'을 본다면 카테고리는 '어제 무슨 일이 있었나'를 본다.
각 함수는 (통과여부, 정렬키, 표시값)을 돌려준다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KR_MIN_VALUE = 5_000_000_000     # 50억
US_MIN_VALUE = 30_000_000        # $30M


def _min_value(market: str) -> float:
    return KR_MIN_VALUE if market == "KR" else US_MIN_VALUE


def surge_up(d: pd.DataFrame, market: str):
    last = d.iloc[-1]
    chg = float(last["chg"])
    if chg >= 0.08 and last["Value"] >= _min_value(market):
        return True, chg, f"{chg:+.1%}"
    return False, 0, ""


def surge_down(d: pd.DataFrame, market: str):
    last = d.iloc[-1]
    chg = float(last["chg"])
    if chg <= -0.08 and last["Value"] >= _min_value(market):
        return True, -chg, f"{chg:+.1%}"
    return False, 0, ""


def volume_burst(d: pd.DataFrame, market: str):
    last = d.iloc[-1]
    if pd.isna(last["vol_ma20"]) or last["vol_ma20"] <= 0:
        return False, 0, ""
    ratio = float(last["Volume"] / last["vol_ma20"])
    if ratio >= 3.0 and last["Value"] >= _min_value(market) * 0.5:
        return True, ratio, f"{ratio:.1f}배"
    return False, 0, ""


def near_52w_high(d: pd.DataFrame, market: str):
    last = d.iloc[-1]
    if pd.isna(last["high52"]) or last["high52"] <= 0:
        return False, 0, ""
    r = float(last["Close"] / last["high52"])
    if r >= 0.97:
        return True, r, f"고점의 {r:.1%}"
    return False, 0, ""


def near_52w_low(d: pd.DataFrame, market: str):
    last = d.iloc[-1]
    if pd.isna(last["low52"]) or last["low52"] <= 0:
        return False, 0, ""
    r = float(last["Close"] / last["low52"])
    if r <= 1.03:
        return True, -r, f"저점의 {r:.1%}"
    return False, 0, ""


def golden_cross_today(d: pd.DataFrame, market: str):
    if len(d) < 25 or d[["ma5", "ma20"]].iloc[-2:].isna().any().any():
        return False, 0, ""
    a, b = d.iloc[-2], d.iloc[-1]
    if b["ma5"] > b["ma20"] and a["ma5"] <= a["ma20"]:
        return True, float(b["Value"]), "어제 발생"
    return False, 0, ""


def ma_align(d: pd.DataFrame, market: str):
    """이평 정배열로 '전환된' 날만. 이미 정배열인 종목은 흔해서 의미가 없다."""
    if len(d) < 65 or d[["ma5", "ma20", "ma60"]].iloc[-2:].isna().any().any():
        return False, 0, ""
    a, b = d.iloc[-2], d.iloc[-1]
    now = b["ma5"] > b["ma20"] > b["ma60"]
    before = a["ma5"] > a["ma20"] > a["ma60"]
    if now and not before:
        return True, float(b["Value"]), "어제 전환"
    return False, 0, ""


def pullback_bounce(d: pd.DataFrame, market: str):
    """MA20 위에 있으면서 3일 내 MA20을 터치한 뒤 양봉 마감."""
    if len(d) < 25 or pd.isna(d["ma20"].iloc[-1]):
        return False, 0, ""
    last = d.iloc[-1]
    if last["Close"] <= last["ma20"] or last["Close"] <= last["Open"]:
        return False, 0, ""
    tail = d.iloc[-4:-1]
    touched = (tail["Low"] <= tail["ma20"] * 1.01).any()
    if touched:
        return True, float(last["Value"]), "MA20 터치 후 반등"
    return False, 0, ""


def box_breakout_today(d: pd.DataFrame, market: str):
    if len(d) < 65 or pd.isna(d["vol_ma20"].iloc[-1]):
        return False, 0, ""
    last = d.iloc[-1]
    prev_high = float(d["Close"].iloc[-61:-1].max())
    if last["Close"] > prev_high and last["Volume"] >= last["vol_ma20"] * 1.5:
        margin = float(last["Close"] / prev_high - 1)
        return True, margin, f"+{margin:.1%} 돌파"
    return False, 0, ""


CATEGORIES = {
    "surge-up":       ("전일 급등",      "등락률 +8% 이상, 거래대금 충족",        "up",   surge_up),
    "surge-down":     ("전일 급락",      "등락률 −8% 이하, 거래대금 충족",        "down", surge_down),
    "volume-burst":   ("거래량 폭발",    "20일 평균 거래량의 3배 이상",           "up",   volume_burst),
    "near-52w-high":  ("52주 신고가 근접", "52주 최고가의 97% 이상",              "up",   near_52w_high),
    "near-52w-low":   ("52주 신저가 근접", "52주 최저가의 103% 이하",             "down", near_52w_low),
    "golden-cross":   ("골든크로스 발생", "MA5가 MA20을 어제 상향 돌파",           "up",   golden_cross_today),
    "ma-align":       ("이평 정배열 전환", "MA5 > MA20 > MA60 이 어제 성립",       "up",   ma_align),
    "pullback":       ("눌림목 후 반등",  "MA20 터치 후 양봉 마감",               "up",   pullback_bounce),
    "box-breakout":   ("박스권 돌파",     "60일 고점 돌파 + 거래량 1.5배",         "up",   box_breakout_today),
}
