import {api} from "./api.js";
import {state} from "./state.js";
import {$} from "./util.js";

const b64u8 = s => Uint8Array.from(atob((s + "===".slice((s.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
const pushSub = async () => (await navigator.serviceWorker.getRegistration("/"))?.pushManager.getSubscription() ?? null;
const keyMatches = sub => { const k = sub.options?.applicationServerKey; if (!k) return true;
  const a = new Uint8Array(k), b = b64u8(state.pushKey); return a.length === b.length && a.every((x, i) => x === b[i]); };

function setAlertsBtn(value) {
  const b = $("#alertsBtn"); b.dataset.state = value; $("#alertsPop").hidden = true;
  b.innerHTML = value === "on" ? `<span class="dot"></span>Alerts on` : value === "blocked" ? "Alerts blocked" : "Turn on alerts";
}

export async function initPush(cfg) {
  state.pushKey = cfg.push_public_key;
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const iosBrowser = /iPhone|iPad|iPod/.test(navigator.userAgent) && !navigator.standalone;
  if (!state.pushKey || (!supported && !iosBrowser)) return;
  $("#alertsBtn").hidden = false;
  if (!supported) return setAlertsBtn("ios");
  if (Notification.permission === "denied") return setAlertsBtn("blocked");
  const sub = await pushSub().catch(() => null);
  if (sub && Notification.permission === "granted") {
    if (keyMatches(sub)) {
      await api("/api/push/subscriptions", {method: "POST", body: JSON.stringify(sub.toJSON()), quiet: true}).catch(() => {});
      return setAlertsBtn("on");
    }
    await sub.unsubscribe().catch(() => {});
  }
  setAlertsBtn("off");
}

async function enablePush() {
  const b = $("#alertsBtn"); b.disabled = true;
  try {
    const reg = await navigator.serviceWorker.register("/sw.js");
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return setAlertsBtn(perm === "denied" ? "blocked" : "off");
    await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64u8(state.pushKey)});
    await api("/api/push/subscriptions", {method: "POST", body: JSON.stringify(sub.toJSON()), quiet: true});
    setAlertsBtn("on");
    api("/api/push/test", {method: "POST", quiet: true}).catch(() => {});
  } catch (e) { alert(`Couldn't turn on alerts: ${e.message}`); }
  finally { b.disabled = false; }
}

export function initPushEvents() {
  $("#alertsBtn").onclick = () => {
    const s = $("#alertsBtn").dataset.state;
    if (s === "on") $("#alertsPop").hidden = !$("#alertsPop").hidden;
    else if (s === "ios") alert("On iPhone and iPad, alerts work in the installed app: tap Share, then Add to Home Screen, and open VitaDrive from your home screen.");
    else if (s === "blocked") alert("Notifications are blocked for this site. Allow them in your browser's site settings, then reload the page.");
    else enablePush();
  };
  $("#pushTest").onclick = async () => {
    $("#alertsPop").hidden = true;
    const r = await api("/api/push/test", {method: "POST"});
    if (!r.sent) alert("No device accepted the test alert. Try turning alerts off and on again.");
  };
  $("#pushOff").onclick = async () => {
    const sub = await pushSub().catch(() => null);
    if (sub) {
      await api(`/api/push/subscriptions?endpoint=${encodeURIComponent(sub.endpoint)}`, {method: "DELETE", quiet: true}).catch(() => {});
      await sub.unsubscribe().catch(() => {});
    }
    setAlertsBtn("off");
  };
  document.addEventListener("click", e => { if (!e.target.closest(".alerts-menu")) $("#alertsPop").hidden = true; });
}
