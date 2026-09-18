// Prasinos Power main-site service worker (v0.1.244).
// Minimal passthrough: exists only so the browser treats the site as an
// installable PWA (standalone app window on the phone). It deliberately does
// NOT cache any response — the tool is dynamic and every page must stay live
// exactly like the normal website. All requests go to the network.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(fetch(event.request));
});
