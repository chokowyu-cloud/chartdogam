"""정세 모듈 — 뉴스 3건 · 금리/환율 지표 · D-day.

세 가지 원칙.

1. **제목과 링크만.** 언론사 본문은 저장하지도 표시하지도 않는다. AI로
   재작성하는 것도 전재에 해당하므로 하지 않는다. 탭하면 원문으로 보낸다.

2. **없으면 없다고 한다.** API 키가 없거나 소스가 죽으면 지어내지 않고
   None을 돌려준다. 앱은 그 자리를 비우거나 안내 문구를 보여준다.

3. **일정은 API가 필요 없다.** FOMC·금통위 날짜는 연초에 공개되므로
   data/calendar.json 에 적어두면 그만이다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import console

console.setup()

TIMEOUT = 12
UA = {"User-Agent": "chartdogam/1.0 (personal chart screener)"}

# 한국경제 RSS — 슬롯별로 어느 피드를 볼지
FEEDS = {
    "국내 금융·정책": ["https://www.hankyung.com/feed/economy",
                  "https://www.hankyung.com/feed/finance",
                  "https://www.hankyung.com/feed/politics"],
    "해외 금융":     ["https://www.hankyung.com/feed/international"],
    "지정학·정치":   ["https://www.hankyung.com/feed/international",
                  "https://www.hankyung.com/feed/politics"],
}

# 제목 키워드 가중치. AI 요약까지 갈 필요 없이 이걸로 충분하다.
WEIGHTS = {
    "국내 금융·정책": {3: ["금리", "금통위", "한국은행", "기준금리", "환율", "원화"],
                  2: ["물가", "부동산", "규제", "예산", "세제", "부양", "국채", "증시"],
                  1: ["수출", "무역", "반도체", "실적", "코스피", "코스닥"]},
    "해외 금융":     {3: ["연준", "FOMC", "파월", "기준금리", "국채금리", "CPI", "고용지표"],
                  2: ["인플레", "달러", "나스닥", "다우", "S&P", "금리"],
                  1: ["실적", "빅테크", "유가", "엔비디아"]},
    "지정학·정치":   {3: ["관세", "무역분쟁", "제재", "선거", "전쟁", "분쟁", "협상"],
                  2: ["정상회담", "수출규제", "동맹", "중동", "대만", "우크라"],
                  1: ["외교", "정상", "회담"]},
}
PENALTY = ["연예", "스포츠", "부고", "인사", "브리핑", "포토", "영상", "칼럼", "오늘의"]


def _get(url: str):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        r.raise_for_status()
        return r
    except Exception:
        return None


def _parse_rss(xml_text: str) -> list[dict]:
    """RSS에서 제목·링크·언론사만 뽑는다. 본문(description)은 건드리지 않는다."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title and link:
            out.append({"title": re.sub(r"\s+", " ", title), "link": link, "pub": pub})
    return out


def _score(title: str, table: dict) -> int:
    s = 0
    for w, words in table.items():
        s += w * sum(1 for k in words if k in title)
    s -= 3 * sum(1 for k in PENALTY if k in title)
    return s


def _similar(a: str, b: str) -> bool:
    """같은 사안 중복 제거 — 제목의 두 글자 조각이 절반 넘게 겹치면 같은 건으로."""
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return False
    return len(ga & gb) / min(len(ga), len(gb)) > 0.5


def fetch_news() -> list[dict] | None:
    """슬롯 3개. 하나도 못 채우면 None."""
    cache: dict[str, list[dict]] = {}
    slots, used = [], []

    for slot, urls in FEEDS.items():
        items = []
        for u in urls:
            if u not in cache:
                r = _get(u)
                cache[u] = _parse_rss(r.text) if r else []
            items += cache[u]
        if not items:
            continue

        table = WEIGHTS[slot]
        ranked = sorted(items, key=lambda it: _score(it["title"], table), reverse=True)
        for it in ranked:
            if _score(it["title"], table) <= 0:
                break
            if any(_similar(it["title"], t) for t in used):
                continue
            used.append(it["title"])
            slots.append({"slot": slot, "title": it["title"],
                          "source": "한국경제", "url": it["link"]})
            break

    return slots or None


def fetch_macro() -> list[dict] | None:
    """금리·환율 4개. 키가 없으면 그 항목은 빠지고, 전부 없으면 None.

    FRED   https://fred.stlouisfed.org/docs/api/api_key.html  (무료)
    ECOS   https://ecos.bok.or.kr/api/                        (무료)
    """
    out = []
    fred = os.getenv("FRED_API_KEY")
    ecos = os.getenv("ECOS_API_KEY")

    if fred:
        for sid, label, unit in (("DFF", "미국 기준금리", "%"),
                                 ("DGS10", "미 10년물", "%")):
            r = _get("https://api.stlouisfed.org/fred/series/observations"
                     f"?series_id={sid}&api_key={fred}&file_type=json"
                     "&sort_order=desc&limit=1")
            if not r:
                continue
            try:
                v = r.json()["observations"][0]["value"]
                if v not in (".", ""):
                    out.append({"label": label, "value": f"{float(v):.2f}{unit}"})
            except Exception:
                pass

    if ecos:
        today = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=20)).strftime("%Y%m%d")
        # 한국은행 기준금리(722Y001/0101000), 원/달러 종가(731Y001/0000001)
        for stat, item, label, fmt in (
            ("722Y001", "0101000", "한국 기준금리", "{:.2f}%"),
            ("731Y001", "0000001", "원/달러", "{:,.0f}"),
        ):
            r = _get(f"https://ecos.bok.or.kr/api/StatisticSearch/{ecos}/json/kr/1/1/"
                     f"{stat}/D/{start}/{today}/{item}")
            if not r:
                continue
            try:
                rows = r.json()["StatisticSearch"]["row"]
                out.append({"label": label, "value": fmt.format(float(rows[-1]["DATA_VALUE"]))})
            except Exception:
                pass

    # 한국 항목이 앞에 오도록
    order = {"한국 기준금리": 0, "미국 기준금리": 1, "원/달러": 2, "미 10년물": 3}
    out.sort(key=lambda x: order.get(x["label"], 9))
    return out or None


def load_calendar(path: str = "data/calendar.json", limit: int = 4) -> list[dict] | None:
    """앞으로 열릴 일정만 D-day로. API가 필요 없는 부분이다."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        events = json.loads(p.read_text(encoding="utf-8")).get("events", [])
    except Exception:
        return None

    today = dt.date.today()
    up = []
    for e in events:
        try:
            d = dt.date.fromisoformat(e["date"])
        except Exception:
            continue
        if d >= today:
            up.append({"label": e["label"], "d": (d - today).days, "date": e["date"]})
    up.sort(key=lambda x: x["d"])
    return up[:limit] or None


def gather() -> dict:
    """배치에서 한 번에 호출. 실패한 항목은 None으로 남는다."""
    return {"news": fetch_news(), "macro": fetch_macro(), "dday": load_calendar()}


if __name__ == "__main__":
    import pprint
    pprint.pp(gather())
