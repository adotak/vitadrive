import {api} from "./api.js";
import {loadCosts} from "./costs.js";
import {loadNearby} from "./nearby.js";
import {loadPhoto, loadRecalls} from "./webinfo.js";
import {state} from "./state.js";
import {GLYPH, HEADLINE, SEV, STATE, SYSTEMS, VITALS, label, worst} from "./constants.js";
import { $, collapsible, countUp, dueText, esc, fmtDate, setHTML } from "./util.js";

function issueGroups(alerts) {
  const dtc = alerts.filter(a => a.metric === "dtc");
  const groups = alerts.filter(a => a.metric !== "dtc").map(a => ({sev: a.severity, title: a.label, detail: a.message, ids: [a.id]}));
  if (dtc.length) {
    const codes = [...new Set(dtc.map(a => a.value))];
    groups.push({sev: worst(dtc.map(a => a.severity)), ids: dtc.map(a => a.id),
      title: codes.length === 1 ? `Fault code ${codes[0]}` : `${codes.length} fault codes reported`,
      detail: `${codes.join(", ")}. Have the car scanned by a mechanic.`});
  }
  return groups.sort((a, b) => SEV.indexOf(b.sev) - SEV.indexOf(a.sev));
}

export function toggleMore(id) {
  state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
  id === "recallList" ? collapsible(id, state.recallRows, 3, "recalls") : refresh();
}

export async function loadVehicles(select) {
  const vs = await api("/api/vehicles");
  $("#vehicle").innerHTML = vs.map(v => `<option value="${esc(v.vin)}">${esc(v.year)} ${esc(v.make)} ${esc(v.model)} · …${esc(v.vin.slice(-4))}</option>`).join("");
  state.vin = vs.find(v => v.vin === select)?.vin || vs[0]?.vin;
  $("#vehicle").value = state.vin || "";
  $("#main").hidden = $("#vehicle").hidden = $("#apiKeyBtn").hidden = !state.vin;
  $("#empty").hidden = !!state.vin;
  $(".nav-links").hidden = $(".nav-label").hidden = !state.vin;
  if (state.vin) refresh();
}

export function startHeroVideo() {
  const v = $("#landing video"), mq = matchMedia("(prefers-reduced-motion: reduce)");
  v.poster = "/static/media/hero-poster.jpg";
  const sync = () => {
    if (mq.matches) { v.pause(); return; }
    if (!v.querySelector("source")) {
      for (const [src, type] of [["/static/media/hero.webm", "video/webm"], ["/static/media/hero.mp4", "video/mp4"]]) {
        const s = document.createElement("source"); s.src = src; s.type = type; v.appendChild(s);
      }
      v.load();
    }
    v.play().catch(() => {});
  };
  mq.addEventListener("change", sync);
  sync();
}

export async function refresh() {
  const [rep, services, log] = await Promise.all([
    api(`/api/vehicles/${state.vin}/report`), api(`/api/vehicles/${state.vin}/services`), api(`/api/vehicles/${state.vin}/alerts?limit=50`)]);
  const v = rep.vehicle, lr = rep.latest_reading, groups = issueGroups(rep.active_alerts);
  if (v.vin === state.vin) {
    state.calendarReadyVin = rep.maintenance.length ? state.vin : null;
    $("#calendarBtn").disabled = state.calendarBusy || !state.calendarReadyVin;
    if (!rep.maintenance.length) {
      $("#calendarStatus").textContent = "Add a sensor reading to export predicted service dates.";
      $("#calendarStatus").hidden = false;
    } else if ($("#calendarStatus").textContent === "Add a sensor reading to export predicted service dates.") {
      $("#calendarStatus").hidden = true;
    }
  }
  const upcoming = [...rep.maintenance].sort((a, b) => a.days_remaining - b.days_remaining), next = upcoming[0];
  $("#statusRing").style.setProperty("--c", `var(--${rep.status})`);
  $("#statusRing").textContent = GLYPH[rep.status];
  $("#statusRing").classList.toggle("pulse", rep.status === "warning" || rep.status === "critical");
  $("#carName").textContent = `${v.year} ${v.make} ${v.model}`;
  $("#status").textContent = HEADLINE[rep.status];
  $("#status").style.color = `var(--${rep.status})`;
  $("#summary").textContent = [groups.length ? `${groups.length} ${groups.length === 1 ? "item needs" : "items need"} attention` : "No warnings",
    next && `next service: ${next.label.toLowerCase()} ${dueText(next)}`].filter(Boolean).join(" · ");
  if (!lr) setHTML($("#odo"), "No readings yet");
  else { setHTML($("#odo"), `<span id="odoNum"></span> <small>km</small>`); countUp($("#odoNum"), Math.round(lr.odometer_km)); }
  $("#powertrain").textContent = {ice: "Petrol / diesel", hybrid: "Hybrid", ev: "Electric"}[v.powertrain] || v.powertrain;
  $("#updatedStamp").textContent = lr ? `Last reading · ${new Date(lr.timestamp).toLocaleString()}` : "Waiting for your first reading";
  $("#nextService").textContent = next ? next.label : "No forecast yet";
  $("#nextDue").textContent = next ? dueText(next) : "Add a reading to get started";
  $("#attentionCount").innerHTML = `${groups.length} <small>${groups.length === 1 ? "item" : "items"}</small>`;
  const an = rep.anomaly;
  $("#aiCheck").hidden = !an;
  if (an) {
    $("#aiCheck").innerHTML = `<span class="dot" style="--c:var(--${an.anomalous ? "info" : "ok"})"></span>AI check: ${an.anomalous
      ? `unusual ${an.top_features.map(k => esc(label(k).toLowerCase())).join(", ")} reading` : "normal"}`;
    $("#aiCheck").title = `Learned fault detector score ${an.score} (normal below ${an.threshold})`;
  }
  const r = rep.range, C = 2 * Math.PI * 52;
  const rangeChanged = setHTML($("#range"), r ? `<div class="gauge-wrap" title="${esc(r.consumption_basis)}">
      <svg class="gauge" aria-hidden="true" viewBox="0 0 120 120"><circle class="track" cx="60" cy="60" r="52"/>
      <circle class="fill" cx="60" cy="60" r="52" style="stroke:var(--${r.status});stroke-dasharray:${C * r.level_pct / 100} ${C}"/></svg>
      <div class="gauge-label"><b data-count="${Math.round(r.range_km)}">0</b><span>km range</span></div></div>
    <div class="range-note"><span class="dot" style="--c:var(--${r.status})"></span>${Math.round(r.level_pct)}% ${esc(r.energy_source)} remaining</div>` : `<p class="mute">Add a reading to see your estimated range.</p>`);
  if (rangeChanged && r) countUp($("#range b"), +$("#range b").dataset.count);

  if (!groups.length) setHTML($("#alerts"), `<div class="allgood"><span class="dot"></span>Nothing needs your attention right now.</div>`);
  if (groups.length) collapsible("alerts", groups.map(g => `<details class="issue"><summary><span class="dot" style="--c:var(--${g.sev})"></span>
    <span class="t">${esc(g.title)}</span><span class="badge ${g.sev}">${g.sev}</span></summary>
    <div class="body"><span>${esc(g.detail)}</span><button class="ghost small" data-action="dismiss-alerts" data-ids="${esc(g.ids.join(","))}">Dismiss</button></div></details>`),
    4, "items");

  const alertMetrics = new Map(rep.active_alerts.map(a => [a.metric, a.severity]));
  const openSystems = new Set([...document.querySelectorAll("#vitals details[open]")].map(el => el.dataset.system));
  const row = k => { const s = alertMetrics.get(k) || "ok", u = VITALS.find(([m]) => m === k)[2];
    return `<div class="row"><span class="mute">${label(k)}</span><span class="v" ${s !== "ok" ? `style="color:var(--${s})"` : ""}>${lr[k]} <small>${u}</small></span></div>`; };
  setHTML($("#vitals"), lr ? SYSTEMS.map(([name, keys]) => {
    const present = keys.filter(k => lr[k] != null);
    if (!present.length) return "";
    const s = worst(present.map(k => alertMetrics.get(k) || "ok").concat(name === "Engine & fluids" && lr.dtc_codes.length ? ["warning"] : []));
    const tires = present.filter(k => k.startsWith("tire_pressure"));
    const tire = k => { const s = alertMetrics.get(k) || "ok";
      return `<div class="tire"><span class="mute small">${label(k)}</span><b ${s !== "ok" ? `style="color:var(--${s})"` : ""}>${lr[k]} <small>kPa</small></b></div>`; };
    const body = name === "Tires" ? `<div class="tires">${tires.map(tire).join("")}</div>${present.filter(k => !tires.includes(k)).map(row).join("")}`
      : present.map(row).join("");
    return `<details class="sys" data-system="${name}" ${openSystems.has(name) ? "open" : ""} style="--c:var(--${s})"><summary class="sys-head"><span class="dot"></span><span>${name}</span><span class="state">${STATE[s]}</span></summary><div class="sys-body">${body}</div></details>`;
  }).join("") : `<span class="mute">No readings yet. Send one with the car's API key.</span>`);

  collapsible("maint", upcoming.map(m => `<div class="svc"><span class="dot" style="--c:var(--${m.status})"></span><div class="t">
    <div>${esc(m.label)}</div><div class="mute small" title="${esc(m.reason)}">${fmtDate(m.due_date)} · ${dueText(m)}${m.km_remaining == null ? ""
    : ` · ${Math.max(0, Math.round(m.km_remaining)).toLocaleString()} km left`}</div></div>
    <button class="ghost small hide-sm" data-action="prefill-service" data-item="${esc(m.item)}">Log</button></div>`), 6, "items");
  setHTML($("#comingList"), upcoming.length ? upcoming.slice(0, 3).map(m => `<a class="svc" href="#maintenanceCard"><span class="dot" style="--c:var(--${m.status})"></span>
    <div class="t"><div>${esc(m.label)}</div><div class="mute small">${fmtDate(m.due_date)} · ${dueText(m)}</div></div><svg class="icon metric-arrow" aria-hidden="true"><use href="#i-arrow"/></svg></a>`).join("")
    : `<span class="mute">Add a reading to see predicted service dates.</span>`);
  $("#svcItem").innerHTML = rep.maintenance.map(m => `<option value="${m.item}">${esc(m.label)}</option>`).join("");
  collapsible("services", services.map(s => `<div class="svc"><div class="t"><div>${esc(rep.maintenance.find(m => m.item === s.item)?.label || s.item)}</div>
    <div class="mute small">${fmtDate(s.performed_on)} · ${Math.round(s.odometer_km).toLocaleString()} km${s.shop ? ` · ${esc(s.shop)}` : ""}${s.notes ? ` · ${esc(s.notes)}` : ""}</div></div>
    <span class="mute">${s.cost ?? ""}</span></div>`), 6, "services");
  if (!services.length) setHTML($("#services"), `<span class="mute">No services recorded yet.</span>`);
  collapsible("log", log.map(a => `<div class="svc"><span class="dot" style="--c:var(--${a.acknowledged ? "line" : a.severity})"></span><div class="t">
    <div>${esc(a.label)}${a.metric === "dtc" ? ` ${esc(a.value)}` : ""}</div><div class="mute small">${new Date(a.timestamp).toLocaleString()} · ${a.acknowledged ? "resolved" : "open"}</div></div></div>`),
    6, "alerts");
  if (!log.length) setHTML($("#log"), `<span class="mute">No alerts yet.</span>`);
  if (state.nearVin !== state.vin) { state.nearVin = state.vin; state.nearKind = null; loadNearby(); }
  if (state.webVin !== state.vin) { state.webVin = state.vin; loadPhoto(state.vin); loadRecalls(state.vin); }
  loadCosts(state.vin);
  if (lr) { const f = $("#fuelForm"); f.odometer_km.value ||= Math.round(lr.odometer_km); f.filled_on.value ||= new Date().toISOString().slice(0, 10); }
  if (lr) { const f = $("#svcForm"); f.odometer_km.value ||= Math.round(lr.odometer_km); f.performed_on.value ||= new Date().toISOString().slice(0, 10); }
}

export async function dismiss(ids) {
  for (const id of ids) await api(`/api/alerts/${id}/ack`, {method: "POST"});
  refresh();
}

export function prefill(item) {
  $("#svcItem").value = item; $("#logSvc").open = true; $("#svcForm").scrollIntoView({behavior: "smooth", block: "center"});
}
