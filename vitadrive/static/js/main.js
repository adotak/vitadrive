import {api} from "./api.js";
import {deleteFuel} from "./costs.js";
import {initNearbyEvents} from "./nearby.js";
import {dismiss, loadVehicles, prefill, refresh, startHeroVideo, toggleMore} from "./overview.js";
import {initPush, initPushEvents} from "./push.js";
import {initMotion} from "./motion.js";
import {initRouter} from "./router.js";
import {loadRecalls} from "./webinfo.js";
import {loadTrips, toggleTrips} from "./trips.js";
import {state} from "./state.js";
import {$} from "./util.js";

async function boot() {
  const cfg = await fetch("/api/config").then(r => r.json());
  state.authEnabled = cfg.auth_enabled;
  $("#askCard").hidden = !cfg.assistant_enabled;
  $("#recallCard").classList.toggle("wide", !cfg.assistant_enabled);
  $("#recallCard").classList.toggle("span-6", cfg.assistant_enabled);
  if (state.authEnabled) {
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `https://${cfg.clerk_frontend_api}/npm/@clerk/clerk-js@5/dist/clerk.browser.js`;
      s.crossOrigin = "anonymous"; s.dataset.clerkPublishableKey = cfg.clerk_publishable_key;
      s.onload = resolve; s.onerror = reject; document.head.appendChild(s);
    });
    await window.Clerk.load();
    if (!window.Clerk.user) {
      document.querySelector("header").hidden = true; $("main").hidden = true;
      $("#landing").hidden = false;
      startHeroVideo();
      window.Clerk.mountSignIn($("#signin"), {appearance: {variables: {colorPrimary: "#173e35", fontFamily: "Manrope, sans-serif"}}});
      window.Clerk.addListener(({user}) => user && location.reload());
      return;
    }
    window.Clerk.mountUserButton($("#userButton"));
  }
  document.body.classList.add("dashboard");
  $("#sidebar").hidden = false;
  $("#today").textContent = new Date().toLocaleDateString(undefined, {weekday: "short", day: "numeric", month: "short", year: "numeric"});
  initPush(cfg);
  await loadVehicles(new URLSearchParams(location.search).get("vin"));
  if (state.vin) loadTrips(state.vin);
  setInterval(() => state.vin && refresh(), 15000);
}

function initActions() {
  document.addEventListener("click", e => {
    const action = e.target.closest("[data-action]");
    if (!action) return;
    if (action.dataset.action === "toggle-more" && action.dataset.id === "tripList") toggleTrips();
    else if (action.dataset.action === "toggle-more") toggleMore(action.dataset.id);
    else if (action.dataset.action === "dismiss-alerts") dismiss(action.dataset.ids.split(",").map(Number));
    else if (action.dataset.action === "prefill-service") prefill(action.dataset.item);
    else if (action.dataset.action === "delete-fuel") deleteFuel(Number(action.dataset.id));
    else if (action.dataset.action === "reload-recalls") loadRecalls(state.vin);
  });
}

function initForms() {
  $("#fuelForm").onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v !== ""));
    await api(`/api/vehicles/${state.vin}/fuel`, {method: "POST", body: JSON.stringify(body)});
    e.target.reset(); refresh();
  };
  document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x === b));
    $("#services").hidden = b.dataset.tab !== "services"; $("#log").hidden = b.dataset.tab !== "log";
  });
  $("#askChips").onclick = e => { if (e.target.matches("button")) { $("#askForm").q.value = e.target.textContent; $("#askForm").requestSubmit(); } };
  $("#vehicle").onchange = e => {
    state.vin = e.target.value; state.calendarReadyVin = null; $("#calendarBtn").disabled = true; $("#calendarStatus").hidden = true;
    $("#svcForm").reset(); $("#fuelForm").reset(); $("#answer").textContent = ""; refresh(); loadTrips(state.vin);
  };
  $("#calendarBtn").onclick = async () => {
    const forVin = state.vin, button = $("#calendarBtn"), status = $("#calendarStatus");
    if (!forVin || state.calendarReadyVin !== forVin || state.calendarBusy) return;
    state.calendarBusy = true; button.disabled = true; button.textContent = "Preparing…"; status.hidden = true;
    try {
      const blob = await api(`/api/vehicles/${encodeURIComponent(forVin)}/maintenance/calendar.ics`, {blob: true, quiet: true});
      if (state.vin !== forVin) return;
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "vitadrive-maintenance.ics";
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = "Calendar file ready. Import it into your calendar app to add reminders.";
      status.hidden = false;
    } catch (err) {
      if (state.vin === forVin) { status.textContent = `Could not export: ${err.message}`; status.hidden = false; }
    } finally {
      state.calendarBusy = false; button.textContent = "Download .ics"; button.disabled = state.calendarReadyVin !== state.vin;
    }
  };
  $("#askForm").onsubmit = async e => {
    e.preventDefault();
    const btn = e.target.querySelector("button"), out = $("#answer");
    btn.disabled = true; out.className = "mute"; out.textContent = "Thinking…";
    try {
      const {answer} = await api(`/api/vehicles/${state.vin}/ask`, {method: "POST", body: JSON.stringify({question: e.target.q.value})});
      out.className = ""; out.textContent = answer;
    } catch (err) { out.textContent = err.message; } finally { btn.disabled = false; }
  };
  $("#svcForm").onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v !== ""));
    await api(`/api/vehicles/${state.vin}/services`, {method: "POST", body: JSON.stringify(body)});
    e.target.reset(); refresh();
  };
  $("#demoBtn").onclick = async e => {
    const button = e.currentTarget;
    button.disabled = true; button.textContent = "Simulating 90 days…";
    try {
      const vs = await api("/api/demo", {method: "POST"});
      await loadVehicles(vs[0]?.vin);
      if (state.vin) loadTrips(state.vin);
    }
    finally { button.disabled = false; button.textContent = "Add demo cars"; }
  };
  $("#apiKeyBtn").onclick = async () => {
    if (!confirm("Generate a new API key for this vehicle? Any previous key stops working.")) return;
    const {api_key} = await api(`/api/vehicles/${state.vin}/api-key`, {method: "POST"});
    prompt("Send readings with header  X-API-Key: <key>  to POST /api/vehicles/" + state.vin + "/readings\n" +
           "Copy it now - it won't be shown again:", api_key);
  };
  $("#vehForm").onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v !== ""));
    body.year = +body.year; body.vin = body.vin.toUpperCase();
    await api(`/api/vehicles/${body.vin}`, {method: "PUT", body: JSON.stringify(body)});
    e.target.reset(); loadVehicles(body.vin);
  };
}

initPushEvents();
initForms();
initActions();
initNearbyEvents();
initMotion();
initRouter();
boot();
