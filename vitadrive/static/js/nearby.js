import {api} from "./api.js";
import {state} from "./state.js";
import {GEO_ERR, NEAR_KINDS} from "./constants.js";
import {esc, $} from "./util.js";

const saveLoc = l => { state.nearLoc = l; localStorage.setItem("vd-near-loc", JSON.stringify(l)); };

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  return state.leafletP ||= new Promise((ok, fail) => {
    const css = Object.assign(document.createElement("link"), {rel: "stylesheet", href: "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
      integrity: "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=", crossOrigin: ""});
    const js = Object.assign(document.createElement("script"), {src: "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
      integrity: "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=", crossOrigin: "", onload: ok, onerror: fail});
    document.head.append(css, js);
  });
}

function nearFail(err, retry) {
  const msg = err.name === "TimeoutError" || err.name === "AbortError"
    ? "The map service took too long to answer." : err.message;
  $("#nearWhy").innerHTML = `${esc(msg)} <button class="link small" id="nearRetry">Try again</button>`;
  $("#nearRetry").onclick = retry;
}

const nearApi = qs => api(`/api/vehicles/${state.vin}/nearby?${qs}`, {quiet: true, signal: AbortSignal.timeout(60000)});

export async function loadNearby(kind = state.nearKind) {
  const seq = ++state.nearSeq, qs = new URLSearchParams(kind ? {kind} : {});
  if (state.nearLoc) { qs.set("lat", state.nearLoc.lat); qs.set("lon", state.nearLoc.lon); }
  if (state.nearLoc) $("#nearWhy").textContent = `Searching near ${state.nearLoc.label}…`;
  try {
    const res = await nearApi(qs);
    if (seq === state.nearSeq) renderNearby(res);
  } catch (err) { if (seq === state.nearSeq) nearFail(err, () => loadNearby(kind)); }
}

function renderNearby(res) {
  state.nearKind = res.kind;
  const needs = new Map(res.needs.map(n => [n.kind, n.reason]));
  const kinds = [...needs.keys(), ...Object.keys(NEAR_KINDS).filter(k => !needs.has(k))];
  $("#nearKinds").innerHTML = kinds.map(k => `<button type="button" class="chip kind${k === res.kind ? " on" : ""}${needs.has(k) ? " need" : ""}"
    data-kind="${k}" ${needs.has(k) ? `title="For: ${esc(needs.get(k))}"` : ""}>${NEAR_KINDS[k]}</button>`).join("");
  const why = needs.get(res.kind), noun = res.label.toLowerCase();
  if (res.lat == null) {
    $("#nearBody").hidden = true;
    $("#nearWhy").textContent = `${why ? `For ${why.toLowerCase()}: ` : ""}find the closest ${noun} using your location or a city / postcode.`;
    return;
  }
  const where = state.nearLoc?.label || "you";
  $("#nearWhy").innerHTML = res.places.length
    ? `${why ? `For <b>${esc(why.toLowerCase())}</b> · ` : ""}${Math.min(res.places.length, 6)} closest ${esc(noun)}s near ${esc(where)}
       · <button class="link small" id="nearChange">Change location</button>`
    : `No ${esc(noun)}s found within ${res.radius_km} km of ${esc(where)}. <button class="link small" id="nearChange">Change location</button>`;
  $("#nearChange").onclick = () => $("#nearForm").q.focus();
  $("#nearBody").hidden = !res.places.length;
  if (!res.places.length) return;
  const dir = p => `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}`;
  $("#nearList").innerHTML = res.places.slice(0, 6).map((p, i) => `<div class="place" data-i="${i}"><span class="num">${i + 1}</span>
    <div class="t"><b title="${esc(p.name)}">${esc(p.name)}</b><div class="mute small">${[p.address, p.opening_hours].filter(Boolean).map(esc).join(" · ")
      || "No address listed"}</div>
    <div class="acts"><a href="${dir(p)}" target="_blank" rel="noopener">Directions</a>${p.phone ? `<a href="tel:${esc(p.phone.replace(/[^+\d]/g, ""))}">Call</a>` : ""}${
      p.website ? `<a href="${esc(p.website)}" target="_blank" rel="noopener noreferrer">Website</a>` : ""}</div></div>
    <span class="km">${p.distance_km < 1 ? `${Math.round(p.distance_km * 1000)} m` : `${p.distance_km.toFixed(1)} km`}</span></div>`).join("");
  loadLeaflet().then(() => {
    if (!state.nearMap) {
      state.nearMap = L.map("nearMap", {scrollWheelZoom: false});
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(state.nearMap);
    }
    state.nearLayer?.remove();
    state.nearLayer = L.layerGroup().addTo(state.nearMap);
    L.circleMarker([res.lat, res.lon], {radius: 8, color: "#58a6ff", fillColor: "#58a6ff", fillOpacity: .9, weight: 3})
      .bindTooltip(state.nearLoc?.label === "your location" ? "You" : esc(where)).addTo(state.nearLayer);
    const markers = res.places.slice(0, 6).map((p, i) => L.marker([p.lat, p.lon], {icon: L.divIcon({className: "", html: `<div class="pin">${i + 1}</div>`,
      iconSize: [24, 24], iconAnchor: [12, 12]})}).bindPopup(`<b>${esc(p.name)}</b><br><a href="${dir(p)}" target="_blank" rel="noopener">Directions</a>`).addTo(state.nearLayer));
    state.nearMap.invalidateSize();
    state.nearBounds = L.featureGroup(markers).getBounds().extend([res.lat, res.lon]);
    state.nearMap.fitBounds(state.nearBounds, {padding: [30, 30], maxZoom: 15});
    document.querySelectorAll("#nearList .place").forEach(el => el.onclick = e => {
      if (e.target.closest("a")) return;
      const m = markers[+el.dataset.i]; state.nearMap.flyTo(m.getLatLng(), 15, {duration: .6}); m.openPopup();
    });
  }).catch(() => { $("#nearMap").innerHTML = `<div class="mute small" style="padding:14px">Map couldn't load; the list still works.</div>`; });
}

export function initNearbyEvents() {
  $("#nearKinds").onclick = e => { const b = e.target.closest("[data-kind]"); if (b) loadNearby(b.dataset.kind); };
  $("#nearGeo").onclick = () => {
    if (!navigator.geolocation || !window.isSecureContext) {
      $("#nearWhy").textContent = "Location isn't available here. Type a city or postcode instead."; return;
    }
    ++state.nearSeq;
    $("#nearWhy").textContent = "Finding your location…";
    navigator.geolocation.getCurrentPosition(
      p => { saveLoc({lat: +p.coords.latitude.toFixed(4), lon: +p.coords.longitude.toFixed(4), label: "your location"}); loadNearby(); },
      err => { $("#nearWhy").textContent = GEO_ERR[err.code] || GEO_ERR[2]; },
      {enableHighAccuracy: false, timeout: 20000, maximumAge: 300000});
  };
  $("#nearForm").onsubmit = async e => {
    e.preventDefault();
    const form = e.target, q = form.q.value.trim(), seq = ++state.nearSeq;
    const qs = new URLSearchParams({q, ...(state.nearKind ? {kind: state.nearKind} : {})});
    $("#nearWhy").textContent = `Searching near ${q}…`;
    try {
      const res = await nearApi(qs);
      if (seq !== state.nearSeq) return;
      saveLoc({lat: res.lat, lon: res.lon, label: q}); form.reset(); renderNearby(res);
    } catch (err) { if (seq === state.nearSeq) nearFail(err, () => form.requestSubmit()); }
  };
}
