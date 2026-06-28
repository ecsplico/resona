// Service Worker for Dictaphone PWA
const CACHE_NAME = 'dictaphone-v2';
const urlsToCache = [
    '/dictaphone.html',
    '/dictaphone-db.js',
    '/manifest.json'
];

// Install event - cache resources
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('Opened cache');
                return cache.addAll(urlsToCache);
            })
    );
    self.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

// --- Web Share Target support ---------------------------------------------
// Mirror of dictaphone-db.js so the SW can stash shared files without the page.
const DB_NAME = 'DictaphoneDB';
const DB_VERSION = 1;
const STORE_NAME = 'recordings';

function openDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                const store = db.createObjectStore(STORE_NAME, { keyPath: 'id' });
                store.createIndex('date', 'date', { unique: false });
                store.createIndex('uploaded', 'uploaded', { unique: false });
            }
        };
    });
}

function addRecording(db, recording) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction([STORE_NAME], 'readwrite');
        tx.objectStore(STORE_NAME).add(recording);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

async function handleShare(event) {
    try {
        const formData = await event.request.formData();
        const files = formData.getAll('audio').filter((f) => f && f.size > 0);
        const today = new Date().toISOString().split('T')[0];
        if (files.length) {
            const db = await openDB();
            let i = 0;
            for (const file of files) {
                const fname = file.name || 'shared-audio.webm';
                await addRecording(db, {
                    id: Date.now() + (i++),
                    title: fname.replace(/\.[^.]+$/, '') || 'Geteilte Datei',
                    date: today,
                    blob: file,
                    filename: fname,
                    duration: 0,
                    uploaded: false,
                    createdAt: Date.now()
                });
            }
        }
    } catch (error) {
        console.error('Share target failed:', error);
    }
    // Redirect (303 = POST -> GET) into the app, flagging the import.
    return Response.redirect('/dictaphone.html?shared=1', 303);
}

// Fetch event - serve from cache, fallback to network
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (event.request.method === 'POST' && url.pathname === '/share-target') {
        event.respondWith(handleShare(event));
        return;
    }

    event.respondWith(
        caches.match(event.request)
            .then((response) => {
                // Cache hit - return response
                if (response) {
                    return response;
                }

                // Clone the request
                const fetchRequest = event.request.clone();

                return fetch(fetchRequest).then((response) => {
                    // Check if valid response
                    if (!response || response.status !== 200 || response.type !== 'basic') {
                        return response;
                    }

                    // Clone the response
                    const responseToCache = response.clone();

                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseToCache);
                    });

                    return response;
                });
            })
            .catch(() => {
                // Could return a custom offline page here
                return new Response('Offline - content not available', {
                    status: 503,
                    statusText: 'Service Unavailable'
                });
            })
    );
});
