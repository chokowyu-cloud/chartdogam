# 시세 중계 서버 (인터넷에 올리는 판)

브라우저는 증권사 API를 직접 못 부른다(CORS). API 키를 페이지에 넣으면 소스
보기로 새어 나간다. 이 둘 때문에 중간에 이게 필요하다.

> **내 PC 에서만 볼 거면 이건 필요 없다.** 상위 폴더의 `실시간연결.bat`
> (`relay.py`) 이 똑같은 일을 하고, 계정도 Node.js 도 필요 없다.
> 이 폴더는 **내 PC 가 꺼져 있어도 도는 주소**가 필요할 때만 쓴다.
> 두 판은 응답 모양이 완전히 같아서 앱은 어느 쪽에 붙었는지 모른다.

무료 한도 안에서 돌아가고, 평소에는 비용이 없다.

---

## 준비물

| | 어디서 | 필요한 것 |
|---|---|---|
| 국내 시세 | [한국투자증권 Open API](https://apiportal.koreainvestment.com/) | 계좌 + 앱키·앱시크릿 |
| 미국 시세 | [Finnhub](https://finnhub.io/) | 무료 가입 후 API 키 |
| 서버 | [Cloudflare](https://dash.cloudflare.com/) | 무료 계정 |

국내만 먼저 해도 된다. 미국 키가 없으면 미국 요청만 실패하고 국내는 잘 돈다.

## 올리기 — **`올리기.bat` 더블클릭**

이 폴더의 `올리기.bat` 을 더블클릭하면 끝난다. 직접 할 일은 두 가지뿐이다.

1. **Cloudflare 로그인** — 브라우저가 열린다. 계정이 없으면 그 자리에서 무료 가입.
2. **키 붙여넣기** — 앱키·앱시크릿을 붙여넣는다. 화면에 보이지 않고, 파일에도
   남지 않는다. 이 창에서 곧바로 Cloudflare 로 넘어가고 중계 서버만 꺼내 쓴다.

나머지 — `ALLOW_ORIGIN` 설정, 배포, 주소 받아오기, `config.py` 반영, 동작 확인 —
는 스크립트가 한다. 5분쯤 걸린다.

끝나면 위 폴더의 `실행하기` 를 다시 더블클릭하면 된다. 장중이면 화면 맨 위에
`실시간 · 시:분:초` 가 뜬다.

<details>
<summary>손으로 하고 싶다면</summary>

```bash
cd worker
npx wrangler login          # 브라우저가 열리고 Cloudflare 로그인
npx wrangler deploy         # 먼저 올린다
npx wrangler secret put KIS_APP_KEY
npx wrangler secret put KIS_APP_SECRET
npx wrangler secret put FINNHUB_KEY     # 미국도 할 때만
```

배포가 키 등록보다 **먼저**다. 아직 없는 워커에 `secret put` 을 하면 wrangler 가
"새로 만들까요?" 를 되묻는데, 그때 붙여넣은 키가 그 질문의 답으로 먹힌다.

마지막에 `https://chartdogam-quote.<계정>.workers.dev` 같은 주소가 나온다.
그게 중계 서버 주소다.

**`wrangler.toml` 의 `ALLOW_ORIGIN` 을 내 페이지 주소로 바꿀 것.** `*` 로 두면
누구나 내 중계 서버를 통해 호출할 수 있고, 그만큼 내 증권사 호출 한도가 남에게
소진된다.

</details>

## 확인

```
https://<주소>/health
→ {"ok":true,"kr":true,"us":true}

https://<주소>/quote?market=KR&codes=005930,000660
→ {"asOf":"...","source":"KIS","delayed":false,"quotes":{"005930":{...}}}
```

## 앱에 연결

`config.py` 의 `LIVE_ENDPOINT` 에 위 주소를 적고 다시 빌드한다.

```python
LIVE_ENDPOINT = "https://chartdogam-quote.내계정.workers.dev"
```

```bash
python scan.py --real
python build_app.py
```

비워 두면 폴링 자체를 하지 않는다. 지금까지처럼 종가 기준으로만 돈다.

---

## 안에서 하는 일

```
브라우저 ──30초마다──▶ 중계 서버 ──▶ KIS / Finnhub
                         │
                         ├ 접근토큰 캐시 (12시간)
                         └ 시세 응답 캐시 (20초)
```

- **토큰 캐시** — KIS 토큰 발급은 호출 한도가 빡빡하다. 매번 새로 받으면 금방
  막혀서, 12시간 재사용한다.
- **응답 캐시 20초** — 방문자가 열 명이든 백 명이든 증권사 호출은 20초에 한 번.
- **종목 20개 상한** — 한 요청이 증권사 한도를 통째로 먹지 않도록.
- **못 받은 종목은 응답에서 뺀다** — 지어내지 않는다. 화면도 그 종목만 종가를
  그대로 둔다.
- **`delayed` 플래그** — 지연 데이터를 실시간인 척 보여주지 않기 위해 그대로
  내려보내고, 화면이 "15분 지연"으로 표시한다.

## 소스를 바꾸고 싶으면

`fetchKR` / `fetchUS` 두 함수만 고치면 된다. 돌려주는 모양
(`{price, change, changePct, volume}`)만 지키면 나머지는 손댈 필요가 없다.
파이썬 쪽 `DataSource` 와 같은 방식이다.

## 비용

Cloudflare Workers 무료 플랜의 하루 요청 한도 안에서 돈다. 30초 폴링이면
한 사람이 장중 내내 켜놔도 하루 800회 남짓이다. 한도와 요금은 가입 시점에
[요금 페이지](https://developers.cloudflare.com/workers/platform/pricing/)에서
확인할 것.
