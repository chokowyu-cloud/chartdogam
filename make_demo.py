"""데모용 일봉 생성기.

실제 시세 소스가 막힌 환경에서 스캐너와 화면을 검증하기 위한 합성 데이터.
종목명·코드는 실제지만 **가격은 전부 가짜**다. 산출 JSON에 is_demo 플래그를
박아서 화면에서도 오해가 없도록 한다.

국면(regime)을 섞어서 만든다. 전부 랜덤워크로 만들면 패턴이 거의 안 잡혀서
화면을 검증할 수 없기 때문에, 종목마다 '사연'을 하나씩 준다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAYS = 780        # 약 3년치 거래일 — 성적표 표본 확보용

KR_STOCKS = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"), ("005380", "현대차"), ("005490", "POSCO홀딩스"),
    ("000270", "기아"), ("068270", "셀트리온"), ("035420", "NAVER"),
    ("051910", "LG화학"), ("006400", "삼성SDI"), ("035720", "카카오"),
    ("105560", "KB금융"), ("055550", "신한지주"), ("012330", "현대모비스"),
    ("028260", "삼성물산"), ("003670", "포스코퓨처엠"), ("015760", "한국전력"),
    ("032830", "삼성생명"), ("086790", "하나금융지주"), ("066570", "LG전자"),
    ("323410", "카카오뱅크"), ("003550", "LG"), ("017670", "SK텔레콤"),
    ("034730", "SK"), ("009150", "삼성전기"), ("011200", "HMM"),
    ("010130", "고려아연"), ("316140", "우리금융지주"), ("259960", "크래프톤"),
    ("018260", "삼성에스디에스"), ("024110", "기업은행"), ("030200", "KT"),
    ("011170", "롯데케미칼"), ("010950", "S-Oil"), ("128940", "한미약품"),
    ("090430", "아모레퍼시픽"), ("051900", "LG생활건강"), ("161390", "한국타이어앤테크놀로지"),
    ("097950", "CJ제일제당"), ("036570", "엔씨소프트"), ("047050", "포스코인터내셔널"),
    ("267250", "HD현대"), ("329180", "HD현대중공업"), ("042660", "한화오션"),
    ("010140", "삼성중공업"), ("009830", "한화솔루션"), ("271560", "오리온"),
    ("004020", "현대제철"), ("001570", "금양"), ("005387", "현대차2우B"),
    ("302440", "SK바이오사이언스"), ("326030", "SK바이오팜"), ("000810", "삼성화재"),
    ("012450", "한화에어로스페이스"), ("064350", "현대로템"), ("079550", "LIG넥스원"),
    ("047810", "한국항공우주"), ("272210", "한화시스템"), ("352820", "하이브"),
    ("035900", "JYP Ent."), ("041510", "에스엠"), ("122870", "와이지엔터테인먼트"),
    ("020150", "롯데에너지머티리얼즈"), ("450080", "에코프로머티"), ("247540", "에코프로비엠"),
    ("086520", "에코프로"), ("091990", "셀트리온헬스케어"), ("196170", "알테오젠"),
    ("145020", "휴젤"), ("214150", "클래시스"), ("278280", "천보"),
    ("357780", "솔브레인"), ("240810", "원익IPS"), ("095340", "ISC"),
    ("058470", "리노공업"), ("222080", "씨아이에스"), ("034020", "두산에너빌리티"),
    ("241560", "두산밥캣"), ("000150", "두산"), ("336260", "두산퓨얼셀"),
    ("112610", "씨에스윈드"), ("322000", "현대에너지솔루션"), ("137400", "피엔티"),
    ("039030", "이오테크닉스"), ("178920", "PI첨단소재"), ("108320", "LX세미콘"),
    ("098460", "고영"), ("140860", "파크시스템스"), ("189300", "엔젤로보틱스"),
    ("454910", "두산로보틱스"), ("277810", "레인보우로보틱스"), ("293490", "카카오게임즈"),
    ("263750", "펄어비스"), ("112040", "위메이드"), ("225570", "넥슨게임즈"),
    ("058970", "엠로"), ("053800", "안랩"), ("060250", "NHN KCP"),
    ("035760", "CJ ENM"), ("079160", "CJ CGV"), ("192820", "코스맥스"),
    ("237880", "클리오"), ("214320", "이노션"), ("030000", "제일기획"),
]

REGIMES = ["uptrend", "downtrend", "range", "double_bottom", "squeeze_break",
           "pullback", "surge", "plunge", "recovery"]
# 화면에 뭔가 잡히도록 사연 있는 국면에 가중치를 준다
REGIME_W = [0.16, 0.10, 0.14, 0.11, 0.11, 0.14, 0.08, 0.06, 0.10]


def _series(rng: np.random.Generator, regime: str, n: int) -> np.ndarray:
    """수익률 시계열을 국면에 맞게 만든다."""
    base_vol = rng.uniform(0.013, 0.026)
    r = rng.normal(0, base_vol, n)

    if regime == "uptrend":
        r += np.linspace(0.0004, 0.0022, n)
    elif regime == "downtrend":
        r -= np.linspace(0.0004, 0.0020, n)
    elif regime == "range":
        r *= 0.7
        r += -0.02 * np.sin(np.linspace(0, 6 * np.pi, n)) * base_vol * 8
    elif regime == "double_bottom":
        # W자: 내려갔다 반등, 다시 비슷한 깊이로 내려갔다 회복
        t = np.linspace(0, 1, n)
        shape = -np.sin(t * np.pi * 2.0) * 0.9
        shape[int(n * 0.75):] += np.linspace(0, 1.4, n - int(n * 0.75))
        r += np.gradient(shape) * 0.055
    elif regime == "squeeze_break":
        cut = int(n * 0.78)
        r[:cut] *= 0.32                      # 오래 수축
        r[cut:] += rng.uniform(0.004, 0.009)  # 이후 위로 이탈
    elif regime == "pullback":
        r += np.linspace(0.0008, 0.0016, n)   # 장기 상승
        dip = rng.integers(int(n * 0.86), n - 4)
        r[dip - 3:dip + 1] -= rng.uniform(0.022, 0.040)   # 단기 급락
        r[dip + 1:dip + 4] += rng.uniform(0.012, 0.026)   # 곧바로 반등
    elif regime == "surge":
        r += np.linspace(0.0002, 0.0010, n)
        r[-1] += rng.uniform(0.09, 0.22)
    elif regime == "plunge":
        r[-1] -= rng.uniform(0.09, 0.18)
    elif regime == "recovery":
        half = n // 2
        r[:half] -= np.linspace(0.0002, 0.0018, half)
        r[half:] += np.linspace(0.0002, 0.0026, n - half)
    return r


def make_stock(code: str, name: str, market: str, rng: np.random.Generator,
               dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, str]:
    regime = str(rng.choice(REGIMES, p=REGIME_W))
    n = len(dates)
    r = _series(rng, regime, n)

    start_price = rng.uniform(18_000, 420_000) if market == "KR" else rng.uniform(28, 620)
    close = start_price * np.cumprod(1 + r)
    close = np.maximum(close, start_price * 0.12)

    intraday = np.abs(rng.normal(0, 0.010, n)) + 0.002
    high = close * (1 + intraday)
    low = close * (1 - intraday * rng.uniform(0.5, 1.4, n))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.005, n))
    open_ = np.clip(open_, low, high)
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])

    # 가격이 높을수록 거래 '주식 수'는 적다. 안 그러면 거래대금이 비현실적으로 커진다.
    scale = (60_000 / start_price) if market == "KR" else (90 / start_price)
    base_vol = (rng.uniform(2e5, 3.0e6) if market == "KR"
                else rng.uniform(9e5, 1.4e7)) * np.clip(scale, 0.25, 3.0)
    vol = base_vol * np.exp(rng.normal(0, 0.42, n))
    # 큰 변동일에는 거래량이 실린다
    vol *= 1 + np.abs(r) * rng.uniform(8, 22)
    if regime in ("squeeze_break", "surge", "box"):
        vol[-1] *= rng.uniform(2.0, 4.0)

    if market == "KR":
        close, open_, high, low = (np.round(x, -1) for x in (close, open_, high, low))

    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close,
         "Volume": np.round(vol), "Value": close * np.round(vol)},
        index=dates,
    )
    df.index.name = "Date"
    return df, regime


def build(seed: int = 20260907) -> dict[str, dict]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp("2026-09-04"), periods=DAYS)

    us = pd.read_csv("data/seed/sp500.csv").head(150)
    out: dict[str, dict] = {}

    for code, name in KR_STOCKS:
        df, regime = make_stock(code, name, "KR", rng, dates)
        out[code] = {"name": name, "market": "KR", "df": df, "regime": regime}

    for row in us.to_dict("records"):
        code = str(row["Symbol"]).strip()
        df, regime = make_stock(code, row["Security"], "US", rng, dates)
        out[code] = {"name": str(row["Security"]).strip(), "market": "US",
                     "df": df, "regime": regime}

    return out


if __name__ == "__main__":
    data = build()
    kr = sum(1 for v in data.values() if v["market"] == "KR")
    print(f"생성 완료: {len(data)}종목 (KR {kr} / US {len(data)-kr}), {DAYS}일")
    from collections import Counter
    print("국면 분포:", dict(Counter(v["regime"] for v in data.values())))
