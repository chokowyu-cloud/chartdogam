/* 차트도감 서비스워커.

   전략이 둘로 나뉜다.
   - 앱 껍데기(HTML·아이콘): 캐시 우선. 매번 받을 이유가 없다.
   - 데이터(scan.json): 네트워크 우선, 실패하면 캐시. 지하철에서 열어도
     마지막으로 받아둔 결과가 뜨고, 화면의 '데이터 기준일'이 언제 것인지 알려준다.

   앱을 새로 배포할 때는 VERSION을 올린다. 그래야 낡은 껍데기가 안 남는다. */

const VERSION = 'v1';
const SHELL = `chartdogam-shell-${VERSION}`;
const DATA = `chartdogam-data-${VERSION}`;

const SHELL_FILES = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon.png',
  './icons/favicon.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(SHELL)
      // 하나가 404여도 설치 전체가 실패하지 않도록 개별 처리
      .then(c => Promise.allSettled(SHELL_FILES.map(f => c.add(f))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 폰트 등 외부는 브라우저에 맡긴다

  // 데이터: 네트워크 우선
  if (url.pathname.endsWith('/data/scan.json')) {
    e.respondWith(
      fetch(req)
        .then(res => {
          const copy = res.clone();
          caches.open(DATA).then(c => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then(r => r || Response.error()))
    );
    return;
  }

  // 껍데기: 캐시 우선, 없으면 네트워크
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok && res.type === 'basic') {
        const copy = res.clone();
        caches.open(SHELL).then(c => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match('./index.html')))
  );
});
