import {api} from "./api.js";
import {state} from "./state.js";
import {collapsible, esc, fmtDate, setHTML, $} from "./util.js";

function renderTrips(data) {
  const vs = data.vs_rated_pct;
  const vsLabel = vs == null ? "—" : `${vs > 0 ? "+" : ""}${vs.toFixed(1)}%`;
  setHTML($("#tripKpis"), [
    `<div class="kpi big"><b>${data.km_per_day.toFixed(1)}</b><span>km per day</span></div>`,
    `<div class="kpi"><b>${data.trips}</b><span>trips in the last 30 days</span></div>`,
    `<div class="kpi"><b>${vsLabel}</b><span>vs rated consumption</span></div>`,
  ].join(""));
  setHTML($("#tripInsights"), data.insights.length
    ? data.insights.map(item => `<div class="trip-insight ${esc(item.kind)}">${esc(item.text)}</div>`).join("")
    : `<p class="mute">Insights will appear as you record trips.</p>`);

  const weeks = data.weekly || [];
  const values = weeks.map(week => week.consumption).filter(value => value != null);
  const max = Math.max(...values, data.rated_consumption || 0, 1);
  setHTML($("#tripTrend"), values.length ? `<div class="trip-trend" role="img" aria-label="Weekly real consumption in ${esc(data.unit)}">
    ${weeks.map(week => {
      const value = week.consumption;
      const height = value == null ? 0 : Math.max(4, value / max * 100);
      const label = new Date(`${week.week_start}T00:00:00`).toLocaleDateString(undefined, {month: "short", day: "numeric"});
      return `<div class="trip-week${value != null ? " has-data" : ""}" title="${esc(label)}${value == null ? ": no trips" : `: ${value.toFixed(1)} ${esc(data.unit)}`}">
        <span class="trip-week-value">${value == null ? "—" : value.toFixed(1)}</span>
        <i style="--bar-height:${height}%"></i><span class="trip-week-label">${esc(label)}</span></div>`;
    }).join("")}
  </div><div class="trip-trend-legend"><span>${esc(data.unit)}</span>${data.rated_consumption == null ? "" : `<span>Rated ${data.rated_consumption.toFixed(1)}</span>`}</div>`
    : `<p class="mute trip-empty">Trip data will build your weekly efficiency trend.</p>`);

  const recent = data.recent || [];
  if (!recent.length) {
    setHTML($("#tripList"), `<p class="mute trip-empty">No trips recorded yet. Trips will appear as your vehicle sends readings.</p>`);
    return;
  }
  collapsible("tripList", recent.map(trip => {
    const when = new Date(trip.started_at);
    const time = when.toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"});
    const duration = Math.max(1, Math.round(trip.duration_s / 60));
    const consumption = trip.consumption == null ? "—" : `${trip.consumption.toFixed(1)} ${esc(data.unit)}`;
    return `<div class="svc trip-row"><div class="t"><div>${fmtDate(trip.started_at.slice(0, 10))} · ${esc(time)}</div>
      <div class="mute small">${trip.distance_km.toFixed(1)} km · ${duration} min · avg ${trip.avg_speed_kmh.toFixed(0)} km/h · max ${trip.max_speed_kmh.toFixed(0)} km/h</div></div>
      <span class="mute trip-consumption">${consumption}</span></div>`;
  }), 5, "trips");
}

export async function loadTrips(forVin) {
  if (!forVin) return;
  let data;
  try {
    data = await api(`/api/vehicles/${encodeURIComponent(forVin)}/trips?days=30`, {quiet: true});
  } catch {
    return;
  }
  if (forVin !== state.vin) return;
  renderTrips(data);
}

export function toggleTrips() {
  state.expanded.has("tripList") ? state.expanded.delete("tripList") : state.expanded.add("tripList");
  loadTrips(state.vin);
}
