"""차트 패턴 스캐너 — 8종 (1차 5종 + 2차 3종).

두 층 구조:
  1층  규칙 필터   — 명시적 조건으로 후보를 추린다 (통과/탈락)
  2층  유사도 점수 — 통과한 것들을 0~100으로 줄 세운다

앱에서 사용자가 보는 "가장 가까운 순"이 2층의 점수다. 패턴이 완성됐느냐를
O/X로 찍는 게 아니라 얼마나 근접했는지를 재는 게 핵심이라, 규칙은 다소
느슨하게 잡고 점수로 변별한다.

각 스캐너는 (matched, score, span, detail)을 돌려준다.
span은 차트에 음영으로 표시할 (시작, 끝) 인덱스.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import indicators as ind


@dataclass
class Hit:
    matched: bool
    score: float = 0.0
    span: tuple[int, int] | None = None
    detail: dict = field(default_factory=dict)


NO = Hit(False)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(np.clip(x, lo, hi))


def _ramp(x: float, lo: float, hi: float) -> float:
    """lo에서 0점, hi에서 100점으로 선형 환산."""
    if hi == lo:
        return 0.0
    return _clip((x - lo) / (hi - lo) * 100)


def max_age(lookback: int) -> int:
    """'아직 유효한' 신호로 볼 최대 경과일.

    기간(lookback)은 차트를 보는 창의 길이지, 아무 때나 일어난 신호를 다
    긁어모으라는 뜻이 아니다. 창의 15% 안에서 발생한 것만 현재 진행형으로 본다.
    이 캡이 없으면 장기 창에서 거의 모든 종목이 잡혀 결과가 무의미해진다.
      단기 40일 → 6일 / 중기 90일 → 13일 / 장기 200일 → 30일
    """
    return max(5, int(lookback * 0.15))


# ===================================================================== 1. 골든크로스
def golden_cross(d: pd.DataFrame, lookback: int) -> Hit:
    """MA5가 MA20을 상향 돌파. 갓 발생했을수록, 거래량이 실렸을수록 높은 점수."""
    if len(d) < 60 or d[["ma5", "ma20", "ma60"]].iloc[-1].isna().any():
        return NO

    win = min(lookback, len(d) - 1)
    ma5, ma20 = d["ma5"].to_numpy(), d["ma20"].to_numpy()
    cross_at = None
    for i in range(len(d) - 1, len(d) - win - 1, -1):
        if i < 1 or np.isnan(ma5[i - 1]) or np.isnan(ma20[i - 1]):
            break
        if ma5[i] > ma20[i] and ma5[i - 1] <= ma20[i - 1]:
            cross_at = i
            break

    if cross_at is None:
        return NO

    last = d.iloc[-1]
    # 하루 폭등이 MA5를 기계적으로 밀어올린 것은 추세 전환이 아니다.
    # 이 가드가 없으면 상위가 전부 '어제 20% 오른 종목'으로 채워진다.
    move = d["Close"].pct_change().abs()
    if float(move.iloc[-3:].max()) > 0.12:
        return NO

    days_since = len(d) - 1 - cross_at
    cap = max_age(lookback)
    if days_since > cap:              # 이미 지나간 신호는 제외
        return NO

    # 갓 발생 = 만점, 캡에 가까울수록 감점
    freshness = 100 - _ramp(days_since, 0, cap)
    # 크로스 당시 거래량이 실렸는가
    vol_at = d["Volume"].iloc[cross_at] / max(d["vol_ma20"].iloc[cross_at], 1)
    vol_pt = _ramp(vol_at, 0.8, 2.2)
    # MA60 위에 있으면 큰 흐름도 우호적
    trend_pt = 100.0 if last["Close"] > last["ma60"] else 35.0
    # 벌어지는 중인가 (크로스 후 이격 확대)
    spread = (last["ma5"] - last["ma20"]) / last["ma20"]
    spread_pt = _ramp(spread, 0, 0.05)

    score = freshness * 0.40 + vol_pt * 0.20 + trend_pt * 0.25 + spread_pt * 0.15
    return Hit(True, _clip(score), (max(0, cross_at - 3), len(d) - 1),
               {"발생": f"{days_since}일 전", "거래량": f"평균의 {vol_at:.1f}배"})


# ===================================================================== 2. 박스권 돌파
def box_breakout(d: pd.DataFrame, lookback: int) -> Hit:
    """직전 60일 고점을 거래량 동반해 뚫었는가."""
    if len(d) < 70 or pd.isna(d["high60"].iloc[-1]):
        return NO

    win = min(lookback, len(d) - 61)
    if win < 2:
        return NO

    close = d["Close"].to_numpy()
    at = None
    for i in range(len(d) - 1, len(d) - win - 1, -1):
        prev_high = float(np.max(close[max(0, i - 60): i]))
        if close[i] > prev_high:
            at = i
            box_high = prev_high
            break
    if at is None:
        return NO

    box_start = max(0, at - 60)
    box_low = float(np.min(close[box_start:at]))
    days_since = len(d) - 1 - at
    cap = max_age(lookback)
    if days_since > cap:
        return NO
    if abs(float(close[at] / close[at - 1] - 1)) > 0.12:   # 갭성 급등은 제외
        return NO
    last = d.iloc[-1]

    freshness = 100 - _ramp(days_since, 0, cap)
    vol_at = d["Volume"].iloc[at] / max(d["vol_ma20"].iloc[at], 1)
    vol_pt = _ramp(vol_at, 1.0, 2.5)
    # 박스가 좁고 길수록(오래 눌린 뒤 돌파) 의미가 크다
    tightness = 100 - _ramp((box_high - box_low) / box_low, 0.08, 0.45)
    # 돌파 후 되밀리지 않고 위에 머무는가
    hold = 100.0 if last["Close"] >= box_high else _ramp(last["Close"] / box_high, 0.94, 1.0)

    score = freshness * 0.30 + vol_pt * 0.25 + tightness * 0.20 + hold * 0.25
    return Hit(True, _clip(score), (box_start, len(d) - 1),
               {"박스 상단": f"{box_high:,.0f}", "돌파": f"{days_since}일 전",
                "거래량": f"평균의 {vol_at:.1f}배"})


# ===================================================================== 3. 볼린저 스퀴즈
def bb_squeeze(d: pd.DataFrame, lookback: int) -> Hit:
    """밴드가 충분히 수축했다가 위로 벌어지며 상단을 이탈."""
    if len(d) < 80 or pd.isna(d["bb_width"].iloc[-1]):
        return NO

    win = min(lookback, len(d) - 40)
    w = d["bb_width"].to_numpy()
    close = d["Close"].to_numpy()
    up = d["bb_up"].to_numpy()

    at = None
    for i in range(len(d) - 1, len(d) - win - 1, -1):
        if i < 30 or np.isnan(w[i]):
            break
        recent = w[max(0, i - 40): i]
        if len(recent) < 20:
            break
        # 돌파 직전 밴드폭이 최근 40일 하위 25% 안이었는가
        was_squeezed = w[i - 1] <= np.nanpercentile(recent, 25)
        # 하루 급등으로 밴드를 뚫은 건 스퀴즈 돌파가 아니라 뉴스다.
        day_move = abs(close[i] / close[i - 1] - 1)
        if (was_squeezed and close[i] > up[i] and close[i - 1] <= up[i - 1]
                and day_move <= 0.12):
            at = i
            break
    if at is None:
        return NO

    days_since = len(d) - 1 - at
    cap = max_age(lookback)
    if days_since > cap:
        return NO
    last = d.iloc[-1]
    squeeze_ratio = w[at - 1] / max(np.nanmedian(w[max(0, at - 60): at]), 1e-9)
    # 자기 평소 밴드폭 대비 확실히 좁아진 적이 있어야 '스퀴즈'다
    if squeeze_ratio > 0.80:
        return NO

    freshness = 100 - _ramp(days_since, 0, cap)
    tight_pt = 100 - _ramp(squeeze_ratio, 0.4, 1.0)   # 더 수축했을수록 고득점
    vol_at = d["Volume"].iloc[at] / max(d["vol_ma20"].iloc[at], 1)
    vol_pt = _ramp(vol_at, 1.0, 2.2)
    trend_pt = 100.0 if last["Close"] > last["ma60"] else 40.0

    score = freshness * 0.35 + tight_pt * 0.30 + vol_pt * 0.15 + trend_pt * 0.20
    return Hit(True, _clip(score), (max(0, at - 40), len(d) - 1),
               {"수축도": f"직전 대비 {squeeze_ratio:.0%}", "돌파": f"{days_since}일 전"})


# ===================================================================== 4. 이중바닥
_W_TEMPLATE = np.array([1.00, 0.62, 0.18, 0.04, 0.16, 0.52, 0.70, 0.50, 0.14,
                        0.05, 0.22, 0.60, 0.88, 1.00])


def double_bottom(d: pd.DataFrame, lookback: int) -> Hit:
    """비슷한 높이의 저점 2개 + 사이 반등 + 넥라인 접근/돌파."""
    win = min(lookback, len(d))
    if win < 30:
        return NO
    cap = max_age(lookback)
    min_gap = max(7, win // 10)

    seg = d.iloc[-win:]
    close = seg["Close"].to_numpy()
    lows, _ = ind.local_extrema(close, order=max(3, win // 25))
    if len(lows) < 2:
        return NO

    # 창 안에서의 가격 위치. 두 저점은 아래쪽 30% 안에 있어야 '바닥'이다.
    w_lo, w_hi = float(close.min()), float(close.max())
    if w_hi - w_lo < 1e-9:
        return NO
    def _pos(px):
        return (px - w_lo) / (w_hi - w_lo)

    best = None
    for a_i in range(len(lows) - 1):
        for b_i in range(a_i + 1, len(lows)):
            a, b = lows[a_i], lows[b_i]
            gap = b - a
            if not (min_gap <= gap <= max(30, win // 2)):
                continue
            # 두 번째 저점이 너무 오래되면 이미 끝난 패턴이다
            if (win - 1 - b) > cap * 2:
                continue
            pa, pb = close[a], close[b]
            # 추세 중간의 잔물결이 아니라 실제 바닥이어야 한다
            if _pos(pa) > 0.30 or _pos(pb) > 0.30:
                continue
            # 첫 저점까지 내려오는 하락이 선행해야 W가 성립한다
            before = close[:a + 1]
            if len(before) < 4 or (before[0] - pa) / max(before[0], 1e-9) < 0.05:
                continue
            # 두 저점의 높이 차이 5% 이내
            depth_diff = abs(pb - pa) / pa
            if depth_diff > 0.06:
                continue
            neck = float(np.max(close[a:b + 1]))
            rebound = (neck - min(pa, pb)) / min(pa, pb)
            if rebound < 0.07:          # 사이 반등이 너무 얕으면 그냥 횡보
                continue
            # 두 번째 저점 이후 회복 중인가
            after = close[b:]
            if len(after) < 3:
                continue
            recovery = (after[-1] - pb) / max(neck - pb, 1e-9)
            cand = (a, b, neck, depth_diff, rebound, recovery)
            if best is None or recovery > best[5]:
                best = cand

    if best is None:
        return NO
    a, b, neck, depth_diff, rebound, recovery = best

    # 형태 유사도: 첫 저점 직전부터 현재까지를 W 템플릿과 비교
    start = max(0, a - max(4, win // 12))
    sim = ind.shape_similarity(close[start:], _W_TEMPLATE)

    sym_pt = 100 - _ramp(depth_diff, 0.0, 0.06)          # 두 저점이 나란할수록
    reb_pt = _ramp(rebound, 0.07, 0.30)
    rec_pt = _clip(recovery * 100)                        # 넥라인까지 얼마나 올라왔나

    score = sim * 0.35 + sym_pt * 0.25 + reb_pt * 0.15 + rec_pt * 0.25
    offset = len(d) - win
    return Hit(True, _clip(score), (offset + start, len(d) - 1),
               {"저점 간격": f"{b - a}일", "저점 차이": f"{depth_diff:.1%}",
                "넥라인": f"{neck:,.0f}", "회복률": f"{_clip(recovery*100):.0f}%"})


# ===================================================================== 5. 눌림목 과매도 반등
def oversold_pullback(d: pd.DataFrame, lookback: int) -> Hit:
    """장기 상승추세는 살아 있는데 단기적으로만 과하게 눌린 종목.

    1차 5종 중 유일한 평균회귀형. 나머지 넷이 추세추종이라, 시장이 조정에
    들어가면 스캐너가 통째로 침묵하는 걸 막아주는 역할.
    """
    if len(d) < 130 or pd.isna(d["ma120"].iloc[-1]):
        return NO

    last = d.iloc[-1]
    ma120 = d["ma120"]

    # (1) 추세 필터 — 큰 흐름은 여전히 위
    if last["Close"] <= last["ma120"]:
        return NO
    if ma120.iloc[-1] <= ma120.iloc[-20]:      # MA120 자체가 상승 중이어야
        return NO

    # (2) 과매도 — 최근 lookback 안에서 RSI2가 바닥을 찍었는가
    win = min(lookback, len(d) - 1)
    tail = d.iloc[-win:]
    rsi_min_pos = int(np.argmin(tail["rsi2"].to_numpy()))
    rsi_min = float(tail["rsi2"].iloc[rsi_min_pos])
    at = len(d) - win + rsi_min_pos
    days_since = len(d) - 1 - at
    if rsi_min > 12 or days_since > 8:
        return NO

    # (3) 위치 — 눌린 지점이 MA20~MA60 근처여야 (추세 붕괴가 아님)
    low_close = float(d["Close"].iloc[at])
    ma20_at, ma60_at = d["ma20"].iloc[at], d["ma60"].iloc[at]
    if pd.isna(ma20_at) or pd.isna(ma60_at):
        return NO
    band_lo, band_hi = min(ma20_at, ma60_at) * 0.94, max(ma20_at, ma60_at) * 1.03
    if not (band_lo <= low_close <= band_hi):
        return NO

    # (4) 확정 트리거 — 반등이 시작됐는가 (저점 이후 전일 고가 돌파)
    triggered = False
    for i in range(at + 1, len(d)):
        if d["Close"].iloc[i] > d["High"].iloc[i - 1]:
            triggered = True
            break

    # 마지막 봉이 이상치급 급등이면 기술적 반등이 아니라 뉴스다
    if abs(float(last["chg"])) > 0.15:
        return NO

    trend_strength = _ramp((last["Close"] / last["ma120"] - 1), 0.0, 0.22)
    oversold_pt = 100 - _ramp(rsi_min, 0, 12)
    fresh_pt = 100 - _ramp(days_since, 0, 8)
    trigger_pt = 100.0 if triggered else 45.0

    score = (trend_strength * 0.25 + oversold_pt * 0.25
             + fresh_pt * 0.20 + trigger_pt * 0.30)
    return Hit(True, _clip(score), (max(0, at - 12), len(d) - 1),
               {"RSI(2) 저점": f"{rsi_min:.0f}", "눌림": f"{days_since}일 전",
                "MA120 이격": f"+{(last['Close']/last['ma120']-1):.1%}",
                "반등 트리거": "발생" if triggered else "대기"})


# ===================================================================== 등록
PATTERNS = {
    "golden-cross":      ("골든크로스",       "MA5가 MA20을 상향 돌파. 추세 전환의 가장 고전적인 신호.", golden_cross),
    "box-breakout":      ("박스권 돌파",       "60일 고점을 거래량 동반해 돌파. 오래 눌릴수록 의미가 크다.", box_breakout),
    "bb-squeeze":        ("볼린저 스퀴즈",     "밴드가 충분히 수축했다가 위로 벌어지며 상단 이탈.", bb_squeeze),
    "double-bottom":     ("이중바닥",         "비슷한 높이의 저점 2개 + 넥라인 회복. W자 형태.", double_bottom),
    "oversold-pullback": ("눌림목 과매도 반등", "장기 상승추세 안에서의 단기 과매도. 유일한 평균회귀형.", oversold_pullback),
}

PERIODS = {"short": ("단기", 40), "mid": ("중기", 90), "long": ("장기", 200)}


# ===================================================================== 6. 삼각수렴
def triangle(d: pd.DataFrame, lookback: int) -> Hit:
    """고점선과 저점선이 좁혀지는 구간.

    고점끼리·저점끼리 각각 직선을 맞춰서 두 선의 간격이 실제로 줄어드는지 본다.
    '수렴'이라는 말은 결국 간격이 좁아진다는 뜻이니, 눈으로 보는 것과 같은 걸 잰다.
    """
    win = min(lookback, len(d))
    if win < 40:
        return NO
    cap = max_age(lookback)

    seg = d.iloc[-win:]
    close = seg["Close"].to_numpy()
    order = max(3, win // 22)
    lows, highs = ind.local_extrema(close, order=order)
    if len(lows) < 3 or len(highs) < 3:
        return NO

    # 각각 직선 맞추기 (x는 창 안에서의 위치)
    hx, hy = np.array(highs, float), close[highs]
    lx, ly = np.array(lows, float), close[lows]
    try:
        hs, hi_ = np.polyfit(hx, hy, 1)
        ls, li = np.polyfit(lx, ly, 1)
    except Exception:
        return NO

    x0, x1 = 0.0, float(win - 1)
    gap0 = (hs * x0 + hi_) - (ls * x0 + li)
    gap1 = (hs * x1 + hi_) - (ls * x1 + li)
    if gap0 <= 0 or gap1 <= 0:
        return NO                       # 선이 이미 교차 — 삼각형이 아니다

    conv = gap1 / gap0
    if conv > 0.72:                     # 충분히 좁아지지 않았다
        return NO

    mid = float(np.mean(close))
    hs_n, ls_n = hs / mid * 100, ls / mid * 100   # 하루당 % 기울기

    # 유형 분류 — 화면에 그대로 보여준다
    flat = 0.05
    if abs(hs_n) < flat and ls_n > flat:
        kind, bull = "상승 삼각형", 1.0
    elif hs_n < -flat and abs(ls_n) < flat:
        kind, bull = "하락 삼각형", 0.35
    elif hs_n < -flat and ls_n > flat:
        kind, bull = "대칭 삼각형", 0.7
    else:
        return NO                       # 수렴이라 부르기 애매한 모양

    last = d.iloc[-1]
    # 하루 폭등으로 만들어진 모양은 제외 (기존 패턴들과 같은 가드)
    if abs(float(d["Close"].pct_change().iloc[-3:].abs().max())) > 0.12:
        return NO

    # 거래량이 말라가는가 — 수렴 구간의 전형적인 특징
    v = seg["Volume"].to_numpy()
    vol_dry = float(np.mean(v[-win // 4:]) / max(np.mean(v[:win // 4]), 1))
    vol_pt = 100 - _ramp(vol_dry, 0.5, 1.2)

    tight_pt = 100 - _ramp(conv, 0.25, 0.72)
    trend_pt = 100.0 if (not pd.isna(last["ma60"]) and last["Close"] > last["ma60"]) else 40.0
    # 꼭짓점에 너무 가까우면 이미 결판난 뒤일 수 있다
    room = gap1 / mid
    room_pt = _ramp(room, 0.01, 0.06) if room < 0.06 else 100.0

    score = (tight_pt * 0.35 + vol_pt * 0.20 + trend_pt * 0.25 + room_pt * 0.20) * bull
    return Hit(True, _clip(score), (len(d) - win, len(d) - 1),
               {"유형": kind, "수렴도": f"폭이 {conv:.0%}로 축소",
                "거래량": f"초반 대비 {vol_dry:.0%}", "남은 폭": f"{room:.1%}"})


# ===================================================================== 7. 역헤드앤숄더
def inverse_hns(d: pd.DataFrame, lookback: int) -> Hit:
    """가운데가 가장 깊은 저점 3개 + 넥라인 회복. 하락 뒤 반전형."""
    win = min(lookback, len(d))
    if win < 45:
        return NO
    cap = max_age(lookback)

    seg = d.iloc[-win:]
    close = seg["Close"].to_numpy()
    lows, _ = ind.local_extrema(close, order=max(3, win // 20))
    if len(lows) < 3:
        return NO

    w_lo, w_hi = float(close.min()), float(close.max())
    if w_hi - w_lo < 1e-9:
        return NO

    best = None
    for i in range(len(lows) - 2):
        for j in range(i + 1, len(lows) - 1):
            for k in range(j + 1, len(lows)):
                a, b, c = lows[i], lows[j], lows[k]
                pa, pb, pc = close[a], close[b], close[c]

                # 머리가 양 어깨보다 확실히 깊어야 한다
                if not (pb < pa * 0.97 and pb < pc * 0.97):
                    continue
                # 두 어깨의 높이가 비슷해야 한다
                sh_diff = abs(pc - pa) / pa
                if sh_diff > 0.10:
                    continue
                # 간격이 한쪽으로 심하게 쏠리면 형태가 아니다
                g1, g2 = b - a, c - b
                if g1 < 5 or g2 < 5 or max(g1, g2) / min(g1, g2) > 3.0:
                    continue
                # 바닥권에 있어야 한다
                if (pb - w_lo) / (w_hi - w_lo) > 0.25:
                    continue
                # 반전형이므로 왼쪽 어깨까지 내려오는 하락이 선행해야 한다.
                # 이 조건이 없으면 상승 추세 중의 잔물결도 전부 잡힌다.
                before = close[:a + 1]
                if len(before) < 5 or (float(before.max()) - pa) / pa < 0.07:
                    continue
                # 세 번째 저점이 너무 오래됐으면 이미 끝난 패턴
                if (win - 1 - c) > cap * 2:
                    continue

                neck = float(max(close[a:b + 1].max(), close[b:c + 1].max()))
                depth = (neck - pb) / pb
                if depth < 0.08:
                    continue
                recovery = (close[-1] - pc) / max(neck - pc, 1e-9)
                # 넥라인을 한참 지났으면 이미 끝난 패턴이다. 스크리너가 찾을 건
                # '지금 넥라인에 다가서는 중'이지, '작년에 완성된 것'이 아니다.
                if recovery > 1.8:
                    continue
                # 넥라인 근처(1.0)를 정점으로 하는 종 모양 점수
                near = 100 - _ramp(abs(recovery - 1.0), 0.0, 0.9)
                quality = near * 0.6 + (100 - _ramp(sh_diff, 0, 0.10)) * 0.4
                cand = (a, b, c, neck, sh_diff, depth, recovery, near)
                if best is None or quality > best[7]:
                    best = cand

    if best is None:
        return NO
    a, b, c, neck, sh_diff, depth, recovery, near = best

    sym_pt = 100 - _ramp(sh_diff, 0.0, 0.10)      # 어깨가 나란할수록
    depth_pt = _ramp(depth, 0.08, 0.35)
    vol_pt = 100.0 if d["Volume"].iloc[-1] > d["vol_ma20"].iloc[-1] else 55.0

    score = sym_pt * 0.30 + depth_pt * 0.20 + near * 0.35 + vol_pt * 0.15
    offset = len(d) - win
    return Hit(True, _clip(score), (offset + max(0, a - 3), len(d) - 1),
               {"어깨 차이": f"{sh_diff:.1%}", "머리 깊이": f"{depth:.1%}",
                "넥라인": f"{neck:,.0f}",
                "위치": ("넥라인 돌파" if recovery >= 1.0 else f"넥라인까지 {(1-recovery)*100:.0f}%")})


# ===================================================================== 8. 플래그
def flag(d: pd.DataFrame, lookback: int) -> Hit:
    """급등(깃대) 뒤 좁은 횡보(깃발). 조정이 얕을수록 좋은 신호로 본다."""
    if len(d) < 60:
        return NO
    win = min(lookback, len(d) - 5)
    if win < 25:
        return NO

    close = d["Close"].to_numpy()
    vol = d["Volume"].to_numpy()
    n = len(d)

    # 깃대·깃발 길이를 보는 기간에 맞춰 늘린다. 이게 없으면 단기·중기·장기가
    # 전부 같은 결과를 내놓아서 기간 선택이 무의미해진다.
    #   단기 40일 → 깃발 3~12일 / 중기 90일 → 5~24일 / 장기 200일 → 10~40일
    f_lo = max(3, int(lookback * 0.07))
    f_hi = min(40, max(f_lo + 4, int(lookback * 0.27)))
    p_lo = max(4, int(lookback * 0.09))
    p_hi = min(30, max(p_lo + 4, int(lookback * 0.22)))

    best = None
    for flag_len in range(f_lo, f_hi + 1):
        f0 = n - flag_len                      # 깃발 시작
        if f0 < 30:
            continue
        for pole_len in range(p_lo, p_hi + 1):
            p0 = f0 - pole_len
            if p0 < 20:
                continue
            pole_gain = close[f0 - 1] / close[p0] - 1
            if pole_gain < 0.15:               # 깃대가 약하면 플래그가 아니다
                continue
            # 깃대가 하루짜리 폭등이면 제외
            step_max = float(np.max(np.abs(np.diff(close[p0:f0]) / close[p0:f0 - 1])))
            if step_max > 0.12:
                continue

            seg = close[f0:]
            hi, lo = float(seg.max()), float(seg.min())
            pullback = (close[f0 - 1] - lo) / close[f0 - 1]
            rng = (hi - lo) / close[f0 - 1]
            # 깃발은 깃대 상승폭의 절반 이상을 되돌리면 안 된다
            if pullback > pole_gain * 0.5 or rng > pole_gain * 0.7:
                continue
            # 아직 깃대 고점을 크게 넘지 않았어야 '진행 중'
            if close[-1] > hi * 1.02:
                continue

            vol_dry = float(np.mean(vol[f0:]) / max(np.mean(vol[p0:f0]), 1))
            if vol_dry > 1.3:          # 깃발 구간에서 거래가 더 터지면 눌림이 아니다
                continue
            quality = pole_gain / max(rng, 0.01)
            cand = (p0, f0, pole_gain, pullback, rng, vol_dry, quality)
            if best is None or quality > best[6]:
                best = cand

    if best is None:
        return NO
    p0, f0, pole_gain, pullback, rng, vol_dry, quality = best

    pole_pt = _ramp(pole_gain, 0.15, 0.60)
    tight_pt = 100 - _ramp(rng / max(pole_gain, 0.01), 0.15, 0.70)
    dry_pt = 100 - _ramp(vol_dry, 0.4, 1.1)      # 깃발에서 거래량이 줄어야 정상
    shallow_pt = 100 - _ramp(pullback / max(pole_gain, 0.01), 0.1, 0.5)

    score = pole_pt * 0.25 + tight_pt * 0.30 + dry_pt * 0.20 + shallow_pt * 0.25
    return Hit(True, _clip(score), (p0, len(d) - 1),
               {"깃대 상승": f"+{pole_gain:.0%}", "깃발 길이": f"{len(d)-f0}일",
                "되돌림": f"{pullback:.1%}", "거래량": f"깃대 대비 {vol_dry:.0%}"})


# 2차 목록 등록 — 1차 5종 뒤에 붙는다
PATTERNS.update({
    "triangle":    ("삼각수렴",     "고점선과 저점선이 좁혀지는 구간. 유형(상승·대칭·하락)을 함께 표시.", triangle),
    "inverse-hns": ("역헤드앤숄더", "가운데가 가장 깊은 저점 3개 + 넥라인 회복. 하락 뒤 반전형.", inverse_hns),
    "flag":        ("플래그",       "급등(깃대) 뒤 얕고 좁은 횡보(깃발). 조정이 얕을수록 좋게 본다.", flag),
})
