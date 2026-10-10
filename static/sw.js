// Goop service worker: app shell stale-while-revalidate; API network-first with last-good fallback (offline viewing).
const CACHE = "goop-v28";
const SHELL = ["/", "/static/styles.css", "/static/app.js", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png",
  "/static/fonts/figtree-var.woff2", "/static/fonts/barlow-500.woff2", "/static/fonts/barlow-600.woff2", "/static/fonts/barlow-700.woff2"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const net = () => fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  });
  if (url.pathname.startsWith("/api/")) { e.respondWith(net().catch(() => caches.match(e.request))); return; }
  // App shell: stale-while-revalidate, so the UI opens instantly even while a sleeping (cold-start) server wakes up
  e.respondWith(caches.match(e.request).then(hit => {
    const p = net().catch(() => hit);
    if (hit) { e.waitUntil(p.catch(() => {})); return hit; }
    return p;
  }));
});
