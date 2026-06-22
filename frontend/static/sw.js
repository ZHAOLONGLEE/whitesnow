const CACHE_NAME = 'whitesnow-v1';
const STATIC_CACHE = 'whitesnow-static-v1';
const DYNAMIC_CACHE = 'whitesnow-dynamic-v1';

// Static assets to cache
const STATIC_ASSETS = [
  '/',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/manifest.json',
  'https://cdn.tailwindcss.com',
  'https://vjs.zencdn.net/8.10.0/video-js.css',
  'https://vjs.zencdn.net/8.10.0/video.min.js',
  'https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing...');
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        console.log('[Service Worker] Caching static assets');
        return cache.addAll(STATIC_ASSETS);
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean old caches
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== STATIC_CACHE && name !== DYNAMIC_CACHE)
          .map((name) => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event - serve from cache or network
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip video streaming requests
  if (url.pathname.startsWith('/api/stream/') || 
      url.pathname.startsWith('/api/download/')) {
    return;
  }

  // API requests - network first, fallback to cache
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const responseClone = response.clone();
          caches.open(DYNAMIC_CACHE).then((cache) => {
            cache.put(request, responseClone);
          });
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Static assets - cache first, fallback to network
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(request).then((response) => {
        const responseClone = response.clone();
        caches.open(STATIC_CACHE).then((cache) => {
          cache.put(request, responseClone);
        });
        return response;
      }).catch(() => {
        // Return offline page for navigation requests
        if (request.mode === 'navigate') {
          return caches.match('/');
        }
      });
    })
  );
});

// Background sync for play history
self.addEventListener('sync', (event) => {
  if (event.tag === 'save-play-progress') {
    event.waitUntil(savePlayProgress());
  }
});

async function savePlayProgress() {
  // This would sync play history when online
  console.log('[Service Worker] Syncing play history...');
}

// Push notification (for future use)
self.addEventListener('push', (event) => {
  const data = event.data.json();
  const options = {
    body: data.body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: 'whitesnow-notification'
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});
