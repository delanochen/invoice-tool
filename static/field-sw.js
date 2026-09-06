const FIELD_CACHE = 'prasinos-field-21';
const ASSETS = ['/field/', '/static/field-watermark.js', '/static/field-work.js', '/static/field-work.css', '/static/logo.svg', '/static/field-icon-192.png', '/static/field-icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(FIELD_CACHE).then(cache => cache.addAll(ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('prasinos-field-') && key !== FIELD_CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Never cache accounts, API data, photo contents or approval pages.
  if (url.pathname === '/field/' && event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/field/')));
  } else if (ASSETS.includes(url.pathname) && url.pathname !== '/field/') {
    event.respondWith(caches.match(url.pathname).then(cached => cached || fetch(event.request)));
  }
});
