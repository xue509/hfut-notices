const CACHE_NAME = 'hfut-notices-v2';

self.addEventListener('install', e => {
  // Skip waiting so new SW takes over immediately
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(['./', 'index.html', 'manifest.json', 'data.json']))
  );
});

self.addEventListener('activate', e => {
  // Delete old caches
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Network first, fall back to cache
  e.respondWith(
    fetch(e.request).then(resp => {
      if (resp.ok) {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
      }
      return resp;
    }).catch(() => caches.match(e.request))
  );
});
