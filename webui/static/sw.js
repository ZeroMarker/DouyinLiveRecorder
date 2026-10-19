/* Cache the shell and pinned CDN modules; never persist config, logs or media. */
const VERSION = '2026.10.19.1';
const CACHE_PREFIX = `dlr-webui-${self.registration.scope}::`;
const SCOPED_CACHE = `${CACHE_PREFIX}${VERSION}`;
const PRECACHE = [
  './', 'static/app.js', 'static/styles.css', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png',
  'icons/icon-maskable-192.png', 'icons/icon-maskable-512.png',
];
const CDN_PACKAGES = [
  'lit@3.3.1/', 'lit-element@4.2.1/', 'lit-html@3.3.1/',
  '@lit/reactive-element@2.1.1/', 'tslib@2.8.1/', '@material/web@2.4.1/',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SCOPED_CACHE)
    .then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys
    .filter(key => (key.startsWith(CACHE_PREFIX) && key !== SCOPED_CACHE) || /^dlr-webui-\d/.test(key))
    .map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

async function networkFirst(request, fallback = request) {
  const cache = await caches.open(SCOPED_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(fallback, response.clone());
    return response;
  } catch {
    return await cache.match(fallback) || Response.error();
  }
}
async function cacheFirst(request) {
  const cache = await caches.open(SCOPED_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  // Opaque responses cannot be used as ES modules. Cache successful CORS responses only.
  if (response.ok && response.type !== 'opaque') await cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) {
    if (url.origin === 'https://cdn.jsdelivr.net' &&
        CDN_PACKAGES.some(pkg => url.pathname.startsWith(`/npm/${pkg}`))) {
      event.respondWith(cacheFirst(request));
    }
    return;
  }
  const scopePath = new URL(self.registration.scope).pathname;
  if (!url.pathname.startsWith(scopePath)) return;
  const path = '/' + url.pathname.slice(scopePath.length);
  if (path.startsWith('/api/videos/play/') || path.startsWith('/videos/')) return;
  if (path === '/api/config' || path.startsWith('/api/logs')) return;
  if (['/api/status', '/api/tasks', '/api/platforms'].includes(path)) {
    event.respondWith(networkFirst(request));
    return;
  }
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, new URL('./', self.registration.scope).href));
    return;
  }
  if (path.startsWith('/static/') || path.startsWith('/icons/') || path === '/manifest.webmanifest') {
    event.respondWith(networkFirst(request));
  }
});
