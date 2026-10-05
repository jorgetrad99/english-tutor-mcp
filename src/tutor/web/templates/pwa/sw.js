// Service worker for the installable shell (spec 8.2). Generated per deploy by /sw.js.
// It caches only the static shell and the offline page. Pages, partials and data always
// come from the network and are never stored on the device.
const VERSION = {{ version|tojson }};
const CACHE = `tutor-shell-${VERSION}`;
const PRECACHE = {{ precache|tojson }};
const NEVER_CACHE = ["/app/", "/api/", "/billing/", "/admin/", "/auth/", "/webhooks/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // credentials: "omit" so the cached offline page is rendered for nobody in particular.
      .then((cache) => cache.addAll(PRECACHE.map((url) => new Request(url, { credentials: "omit" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("tutor-shell-") && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.headers.get("HX-Request") === "true") return;
  if (request.mode === "navigate") {
    // Network only; the cached offline page is the fallback when there is no network.
    event.respondWith(fetch(request).catch(() => caches.match("/offline")));
    return;
  }
  if (NEVER_CACHE.some((prefix) => url.pathname.startsWith(prefix))) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(request, { ignoreSearch: true }).then((hit) => hit || fetch(request)));
  }
});
