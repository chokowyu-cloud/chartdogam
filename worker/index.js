/**
 * 차트도감 시세 중계 서버 (Cloudflare Worker)
 *
 * 브라우저는 증권사 API를 직접 부를 수 없다(CORS). 그리고 API 키를 페이지에
 * 넣으면 소스 보기로 누구나 가져간다. 이 두 가지 때문에 중간에 이게 필요하다.
 *
 *   GET /quote?market=KR&codes=005930,000660
 *   GET /quote?market=US&codes=AAPL,MSFT
 *
 *   → { asOf, market, source, delayed, quotes: { "005930": {price, change, changePct, volume} } }
 *
 * 설계 원칙은 파이썬 쪽과 같다.
 *   · 어댑터로 감싼다 — 소스가 바뀌어도 응답 모양은 그대로.
 *   · 없으면 없다고 한다 — 못 받은 종목은 응답에서 빠진다. 지어내지 않는다.
 *   · 지연 여부를 숨기지 않는다 — delayed 플래그를 그대로 내려보낸다.
 */

const KIS_HOST = "https://openapi.koreainvestment.com:9443";
const FINNHUB = "https://finnhub.io/api/v1";

const MAX_CODES = 20;      // 한 번에 물어볼 수 있는 종목 수 (증권사 호출 한도 보호)
const QUOTE_TTL = 20;      // 같은 종목을 여러 방문자가 봐도 20초에 한 번만 실제 호출
const TOKEN_MARGIN = 600;  // 토큰 만료 10분 전에 미리 갱신

let memToken = null;       // { value, exp } — 같은 인스턴스가 살아있는 동안 재사용

// ---------------------------------------------------------------- 공통
function cors(env) {
  const allow = env.ALLOW_ORIGIN || "*";
  return {
    "access-control-allow-origin": allow,
    "access-control-allow-methods": "GET,OPTIONS",
    "access-control-max-age": "86400",
    "vary": "origin",
  };
}

function json(body, env, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...cors(env), ...extra },
  });
}

/** 응답에서 숫자를 꺼낸다. 증권사마다 필드명이 달라서 후보를 순서대로 본다. */
function pick(obj, names) {
  for (const n of names) {
    if (obj && obj[n] !== undefined && obj[n] !== null && obj[n] !== "") {
      const v = Number(String(obj[n]).replace(/,/g, ""));
      if (Number.isFinite(v)) return v;
    }
  }
  return null;
}

// ---------------------------------------------------------------- KIS 토큰
async function kisToken(env) {
  const now = Math.floor(Date.now() / 1000);
  if (memToken && memToken.exp - TOKEN_MARGIN > now) return memToken.value;

  // 인스턴스가 새로 떴을 수도 있으니 캐시도 본다. 토큰 발급은 호출 한도가
  // 빡빡해서, 매 요청마다 새로 받으면 금방 막힌다.
  const key = new Request("https://cdg.internal/kis-token");
  const hit = await caches.default.match(key);
  if (hit) {
    const t = await hit.json();
    if (t.exp - TOKEN_MARGIN > now) {
      memToken = t;
      return t.value;
    }
  }

  const res = await fetch(`${KIS_HOST}/oauth2/tokenP`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      appkey: env.KIS_APP_KEY,
      appsecret: env.KIS_APP_SECRET,
    }),
  });
  if (!res.ok) throw new Error(`KIS 토큰 발급 실패 (HTTP ${res.status})`);

  const data = await res.json();
  if (!data.access_token) throw new Error("KIS 토큰 응답에 access_token 이 없음");

  // 유효기간은 하루지만, 넉넉잡아 12시간만 쓴다.
  const tok = { value: data.access_token, exp: now + 12 * 3600 };
  memToken = tok;
  await caches.default.put(
    key,
    new Response(JSON.stringify(tok), {
      headers: { "content-type": "application/json", "cache-control": "max-age=43200" },
    })
  );
  return tok.value;
}

// ---------------------------------------------------------------- 어댑터: 국내
async function fetchKR(codes, env) {
  const token = await kisToken(env);
  const quotes = {};

  for (const code of codes) {
    const url = new URL(`${KIS_HOST}/uapi/domestic-stock/v1/quotations/inquire-price`);
    url.searchParams.set("FID_COND_MRKT_DIV_CODE", "J");
    url.searchParams.set("FID_INPUT_ISCD", code);

    const res = await fetch(url, {
      headers: {
        authorization: `Bearer ${token}`,
        appkey: env.KIS_APP_KEY,
        appsecret: env.KIS_APP_SECRET,
        tr_id: "FHKST01010100",
        custtype: "P",
        "content-type": "application/json; charset=utf-8",
      },
    });
    if (!res.ok) continue;                     // 못 받은 종목은 그냥 빠진다

    const body = await res.json();
    const o = body && body.output;
    if (!o) continue;

    const price = pick(o, ["stck_prpr"]);
    if (price === null) continue;
    quotes[code] = {
      price,
      change: pick(o, ["prdy_vrss"]),
      changePct: pick(o, ["prdy_ctrt"]),
      volume: pick(o, ["acml_vol"]),
    };
  }
  return { source: "KIS", delayed: false, quotes };
}

// ---------------------------------------------------------------- 어댑터: 미국
async function fetchUS(codes, env) {
  // 1순위 Finnhub — 키만 있으면 되고 계좌가 필요 없다.
  if (env.FINNHUB_KEY) {
    const quotes = {};
    for (const code of codes) {
      const res = await fetch(
        `${FINNHUB}/quote?symbol=${encodeURIComponent(code)}&token=${env.FINNHUB_KEY}`
      );
      if (!res.ok) continue;
      const q = await res.json();
      if (!q || !Number.isFinite(q.c) || q.c === 0) continue;
      quotes[code] = {
        price: q.c,
        change: Number.isFinite(q.d) ? q.d : null,
        changePct: Number.isFinite(q.dp) ? q.dp : null,
        volume: null,
      };
    }
    // 무료 티어는 지연될 수 있다. 확실하지 않은 건 확실하지 않다고 표시한다.
    return { source: "Finnhub", delayed: env.FINNHUB_REALTIME !== "1", quotes };
  }

  // 2순위 KIS 해외주식 — 해외 서비스 신청이 되어 있어야 한다.
  if (env.KIS_APP_KEY && env.KIS_OVERSEAS === "1") {
    const token = await kisToken(env);
    const quotes = {};
    for (const code of codes) {
      const url = new URL(`${KIS_HOST}/uapi/overseas-price/v1/quotations/price`);
      url.searchParams.set("AUTH", "");
      url.searchParams.set("EXCD", env.KIS_EXCD || "NAS");   // NAS·NYS·AMS
      url.searchParams.set("SYMB", code);
      const res = await fetch(url, {
        headers: {
          authorization: `Bearer ${token}`,
          appkey: env.KIS_APP_KEY,
          appsecret: env.KIS_APP_SECRET,
          tr_id: "HHDFS00000300",
          custtype: "P",
        },
      });
      if (!res.ok) continue;
      const o = (await res.json()).output;
      const price = pick(o, ["last"]);
      if (price === null) continue;
      quotes[code] = {
        price,
        change: pick(o, ["diff"]),
        changePct: pick(o, ["rate"]),
        volume: pick(o, ["tvol"]),
      };
    }
    return { source: "KIS(해외)", delayed: false, quotes };
  }

  throw new Error("미국 시세 소스가 설정되지 않았습니다 (FINNHUB_KEY 또는 KIS_OVERSEAS)");
}

// ---------------------------------------------------------------- 진입점
export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }

    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({ ok: true, kr: !!env.KIS_APP_KEY, us: !!(env.FINNHUB_KEY || env.KIS_OVERSEAS) }, env);
    }
    if (url.pathname !== "/quote") {
      return json({ error: "not found" }, env, 404);
    }

    const market = (url.searchParams.get("market") || "").toUpperCase();
    const raw = (url.searchParams.get("codes") || "").trim();
    if (market !== "KR" && market !== "US") {
      return json({ error: "market 은 KR 또는 US" }, env, 400);
    }
    // 코드 정리 — 중복 제거, 상한, 이상한 문자 차단
    const codes = [...new Set(raw.split(",").map((s) => s.trim()).filter(Boolean))]
      .filter((c) => /^[A-Za-z0-9.\-]{1,12}$/.test(c))
      .slice(0, MAX_CODES);
    if (!codes.length) return json({ error: "codes 가 비어 있음" }, env, 400);

    // 같은 질문은 20초간 재사용한다. 방문자가 늘어도 증권사 호출은 늘지 않는다.
    const cacheKey = new Request(
      `https://cdg.internal/q?m=${market}&c=${codes.slice().sort().join(",")}`
    );
    const cached = await caches.default.match(cacheKey);
    if (cached) {
      const b = await cached.json();
      return json({ ...b, cached: true }, env);
    }

    let out;
    try {
      out = market === "KR" ? await fetchKR(codes, env) : await fetchUS(codes, env);
    } catch (e) {
      // 실패를 조용히 삼키지 않는다. 화면이 "갱신 실패"를 표시할 수 있어야 한다.
      return json({ error: String(e.message || e) }, env, 502);
    }

    const body = {
      asOf: new Date().toISOString(),
      market,
      source: out.source,
      delayed: out.delayed,
      count: Object.keys(out.quotes).length,
      quotes: out.quotes,
    };
    ctx.waitUntil(
      caches.default.put(
        cacheKey,
        new Response(JSON.stringify(body), {
          headers: { "content-type": "application/json", "cache-control": `max-age=${QUOTE_TTL}` },
        })
      )
    );
    return json(body, env);
  },
};
