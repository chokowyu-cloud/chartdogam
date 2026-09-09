"""차트도감 — 전역 설정.

숫자를 바꿀 일이 생기면 전부 여기서만 바꿉니다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
SEED_DIR = DATA_DIR / "seed"
OUT_DIR = ROOT / "public" / "data"

# ---------------------------------------------------------------- 유니버스
# 지수 구성종목을 1차 필터로 쓴다. 유동성·재무 요건을 거래소가 이미 걸러줬으므로
# 시총/거래대금 임계값을 직접 튜닝할 일이 사라진다.
KRX_INDEX_CODES = {
    "KOSPI200": "1028",
    "KOSDAQ150": "2203",
}

KR_TOP_N = 250          # 거래대금 상위 몇 종목을 남길지
US_TOP_N = 250
KR_MIN_VALUE = 3_000_000_000   # 20일 평균 거래대금 하한 (30억원)

# 상장 후 이 일수를 못 채운 종목은 제외 (패턴 판별에 필요한 봉이 부족)
MIN_HISTORY_DAYS = 150

# ---------------------------------------------------------------- 수집 기간
# 달력일 기준. 3년+ 를 받는 이유는 성적표(backtest.py) 때문이다.
# 매일 스캔에는 250일이면 충분하지만, 패턴별 승률을 재려면 표본이 필요하다.
HISTORY_DAYS = 1180
LOOKBACK_FOR_LIQUIDITY = 20

# ---------------------------------------------------------------- 컬럼 규약
# 소스가 뭐든 이 이름으로 정규화해서 내보낸다.
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume", "Value"]
UNIVERSE_COLUMNS = ["code", "name", "market", "source_index"]

# ---------------------------------------------------------------- 네트워크
REQUEST_TIMEOUT = 20
RETRY = 2
SLEEP_BETWEEN_CALLS = 0.12   # 스크래핑 소스에 대한 최소한의 예의

SEED_SP500 = SEED_DIR / "sp500.csv"

# ---------------------------------------------------------------- 실시간 현재가
# 시세 중계 서버 주소 (worker/README.md 참고). 비워 두면 앱이 폴링을 아예 하지
# 않고 지금까지처럼 종가 기준으로만 돈다.
#   예: "https://chartdogam-quote.내계정.workers.dev"
LIVE_ENDPOINT = ""
