const CACHE_NAME = 'garden-goats-v2';
const ASSETS_TO_CACHE = [
  './',
  './index.html',
  './manifest.json',
  'https://cdn.tailwindcss.com',
  'https://unpkg.com/react@18/umd/react.production.min.js',
  'https://unpkg.com/react-dom@18/umd/react-dom.production.min.js',
  'https://unpkg.com/@babel/standalone/babel.min.js'
];

// Network-ish API hosts we runtime-cache so offline trips reuse fetched routes/geocodes
const RUNTIME_CACHE_HOSTS = ['router.project-osrm.org', 'nominatim.openstreetmap.org'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)));
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isApi = RUNTIME_CACHE_HOSTS.some((h) => url.hostname.includes(h));

  // Page navigations: network-first so the REX /dispatch PIN gate always runs
  // on the server (a stale cached document must never bypass the key check).
  // Falls back to the cached document only when offline.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request).then((c) => c || caches.match('./')))
    );
    return;
  }

  // API requests: network-first with cache fallback (route/geocode reuse offline)
  if (isApi) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, clone);
              // Keep the runtime API cache bounded
              cache.keys().then((reqs) => {
                if (reqs.length > 120) cache.delete(reqs[0]);
              });
            });
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Static assets: cache-first
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    }).catch(() => {
      console.log('Offline and resource not cached.');
    })
  );
});
