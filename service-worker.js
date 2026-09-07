const CACHE="baby-smart-v053-exact-packages-push";
const SHELL=["./","./index.html","./manifest.webmanifest","./app-icon-192.png","./app-icon-512.png"];

self.addEventListener("install",event=>{
  event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).catch(()=>{}));
  self.skipWaiting();
});

self.addEventListener("activate",event=>{
  event.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
      .then(()=>self.clients.claim())
  );
});

self.addEventListener("fetch",event=>{
  if(event.request.method!=="GET")return;
  event.respondWith(
    fetch(event.request).catch(()=>caches.match(event.request).then(r=>r||caches.match("./index.html")))
  );
});

self.addEventListener("push",event=>{
  let payload={};
  try{payload=event.data?.json()||{}}catch{payload={body:event.data?.text()||""}}
  const title=payload.title||"Baby Smart List";
  const options={
    body:payload.body||"יש עדכון חדש במלאי המשפחתי.",
    tag:payload.id||"baby-smart-notification",
    renotify:false,
    data:payload,
    dir:"rtl",
    lang:"he"
  };
  event.waitUntil(self.registration.showNotification(title,options));
});

self.addEventListener("notificationclick",event=>{
  event.notification.close();
  const payload=event.notification.data||{};
  const productId=payload?.data?.product_id||payload?.product_id||"";
  const target=new URL(productId?`./#product=${encodeURIComponent(productId)}`:"./#notifications",self.registration.scope).href;
  event.waitUntil(
    clients.matchAll({type:"window",includeUncontrolled:true}).then(list=>{
      for(const client of list){
        if("navigate" in client)client.navigate(target);
        if("focus" in client)return client.focus();
      }
      return clients.openWindow(target);
    })
  );
});
