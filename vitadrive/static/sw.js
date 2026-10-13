// VitaDrive service worker: shows critical-alert push notifications and opens the dashboard when tapped.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "VitaDrive", {
    body: d.body || "", icon: "/static/icon-192.png", badge: "/static/icon-192.png",
    tag: d.tag, renotify: !!d.tag, data: {url: d.url || "/"},
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || "/", self.location.origin).href;
  e.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(wins => {
    const open = wins.find(w => w.url.startsWith(self.location.origin));
    return open ? open.focus().then(w => (w.navigate ? w.navigate(url) : w)) : clients.openWindow(url);
  }));
});
