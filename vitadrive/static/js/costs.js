import {api} from "./api.js";
import {state} from "./state.js";
import {collapsible, esc, fmtDate, money, monthName, setHTML, $} from "./util.js";

export async function loadCosts(forVin) {
  const [s, logs] = await Promise.all([api(`/api/vehicles/${forVin}/costs`, {quiet: true}), api(`/api/vehicles/${forVin}/fuel`, {quiet: true})])
    .catch(() => [null, []]);
  if (!s || forVin !== state.vin) return;
  const ev = s.unit === "kWh", what = ev ? "charge" : "fill-up";
  $("#costTitle").textContent = ev ? "Charging costs" : "Fuel costs";
  $("#logFuel summary").textContent = `Log a ${what}`;
  $("#fuelListTitle").textContent = `${ev ? "Charging" : "Fill-up"} history`;
  $("#fuelForm").amount.placeholder = ev ? "kWh" : "Litres";
  const kpi = (v, l, big) => `<div class="kpi${big ? " big" : ""}"><b>${v}</b><span>${l}</span></div>`;
  setHTML($("#costKpis"), !s.fills ? `<span class="mute">No ${what}s logged yet. Log one below; after two you'll see your cost per km.</span>`
    : kpi(s.cost_per_km == null ? "–" : money(s.cost_per_km, s.cost_per_km < 0.1 ? 3 : 2), "per km driven", true)
      + kpi(money(s.this_month_cost), "spent this month")
      + kpi(s.avg_price_per_unit == null ? "–" : money(s.avg_price_per_unit), `average per ${s.unit}`)
      + kpi(s.consumption_per_100km == null ? "–" : s.consumption_per_100km.toFixed(1), `${s.unit}/100 km, real`));
  const max = Math.max(...s.months.map(m => m.cost), 1);
  setHTML($("#costBars"), !s.fills ? "" : s.months.map((m, i) => `<div class="bar${i === s.months.length - 1 ? " now" : ""}" title="${money(m.cost)}">
    <span>${m.cost ? Math.round(m.cost).toLocaleString() : ""}</span><i style="height:calc((100% - 40px) * ${m.cost / max});animation-delay:${i * 60}ms"></i>${monthName(m.month)}</div>`).join(""));
  if (logs.length) collapsible("fuelList", logs.map(l => `<div class="svc"><div class="t"><div>${l.amount} ${s.unit}${l.station ? ` · ${esc(l.station)}` : ""}</div>
    <div class="mute small">${fmtDate(l.filled_on)} · ${Math.round(l.odometer_km).toLocaleString()} km</div></div>
    <span class="mute">${money(l.cost)}</span><button class="ghost small" data-action="delete-fuel" data-id="${esc(l.id)}">Delete</button></div>`), 5, "entries");
  else setHTML($("#fuelList"), `<span class="mute">Nothing logged yet.</span>`);
}

export async function deleteFuel(id) {
  if (confirm("Delete this entry?")) {
    await api(`/api/vehicles/${state.vin}/fuel/${id}`, {method: "DELETE"});
    loadCosts(state.vin);
  }
}
