const CACHE="m2-wallet-demo-v3";
const ASSETS=["./index.html","./cregis.css","./wallet-detail.css","./session.css","./operations.css","./mobile.css","./demo-store.js","./cregis.js","./i18n-en.js","./manifest.webmanifest","./icon-192.png","./icon-512.png"];
self.addEventListener("install",event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",event=>{const url=new URL(event.request.url);if(event.request.method!=="GET"||url.pathname.startsWith("/api/"))return;event.respondWith(fetch(event.request).then(response=>{const clone=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,clone));return response;}).catch(()=>caches.match(event.request).then(response=>response||caches.match("./index.html"))));});
