/* DouyinLiveRecorder WebUI - Service Worker
 *
 * 策略说明：
 * - 静态资源（/icons、/manifest.webmanifest 等）：stale-while-revalidate
 * - 页面导航（/）：network-first，离线时回退到缓存的 App Shell
 * - GET /api/status、/api/tasks、/api/platforms：network-first（离线时展示最近一次数据）
 * - 配置与日志可能包含敏感信息，始终走网络且不写入缓存
 * - POST/PUT/DELETE 与流媒体播放（/api/videos/play/*、/videos/*）：不缓存，走网络
 *
 * 注意：Service Worker 仅在 HTTPS 或 localhost 环境下生效。
 * 局域网 HTTP 访问时浏览器会拒绝注册，页面功能不受影响，仅失去离线能力。
 */
const VERSION = '2026.08.20.1';
const CACHE_NAME = `dlr-webui-${VERSION}`;

const PRECACHE = [
  './',
  'manifest.webmanifest',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-maskable-192.png',
  'icons/icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return; // 写操作一律走网络

  const url = new URL(request.url);
  if (url.origin !== location.origin) return; // 跨域（如视频播放源）不处理

  const scopePath = new URL(self.registration.scope).pathname.replace(/\/$/, '');
  const path = url.pathname.startsWith(scopePath)
    ? url.pathname.slice(scopePath.length) || '/'
    : url.pathname;

  // 流媒体 / 文件下载：绝不缓存（大文件、实时转封装）
  if (path.startsWith('/api/videos/play/') || path.startsWith('/videos/')) return;

  // 配置可能包含 Cookie / Token，日志也可能包含直播源信息，禁止持久缓存
  if (path === '/api/config' || path.startsWith('/api/logs')) return;

  // 非敏感只读 API：network-first，失败回退缓存
  if (['/api/status', '/api/tasks', '/api/platforms'].includes(path)) {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then((c) => c.put(request, clone));
          }
          return resp;
        })
        .catch(() => caches.match(request).then((cached) => cached || Response.error()))
    );
    return;
  }

  // 页面导航：network-first，离线回退 App Shell
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put('./', clone));
          return resp;
        })
        .catch(() => caches.match('./'))
    );
    return;
  }

  // 静态资源：stale-while-revalidate
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetched = fetch(request)
        .then((resp) => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then((c) => c.put(request, clone));
          }
          return resp;
        })
        .catch(() => cached);
      return cached || fetched;
    })
  );
});
