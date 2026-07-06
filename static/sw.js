const CACHE = "fridgechef-v1";
const ASSETS = [
  "/",
  "/index.html",
  "/pantry.html",
  "/recipes.html",
  "/favorites.html",
  "/scan.html",
  "/css/style.css",
  "/manifest.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS))
  );
});

self.addEventListener("fetch", (e) => {
  // Skip API calls - don't cache
  if (e.request.url.includes("/recipes") || e.request.url.includes("/scan")) {
    return;
  }
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
