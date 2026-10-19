import {api} from "./api.js";
import {state} from "./state.js";
import {collapsible, esc, fmtDate, $} from "./util.js";

export async function loadPhoto(forVin) {
  const el = $("#heroPhoto"), img = $("#heroImage"), credit = $("#photoCredit");
  el.classList.remove("on"); credit.hidden = true;
  const p = await api(`/api/vehicles/${forVin}/photo`, {quiet: true}).catch(() => null);
  if (!p || forVin !== state.vin) return;
  img.onload = () => { if (forVin !== state.vin) return; el.style.backgroundImage = "none"; el.classList.add("on");
    credit.title = p.title; credit.hidden = !p.page_url.startsWith("https://"); if (!credit.hidden) credit.href = p.page_url; };
  img.crossOrigin = "anonymous";
  img.src = p.image;
}

export async function loadRecalls(forVin) {
  const el = $("#recalls");
  el.innerHTML = `<span class="mute">Checking the recall database…</span>`;
  let d;
  try { d = await api(`/api/vehicles/${forVin}/recalls`, {quiet: true}); }
  catch (e) {
    if (forVin === state.vin) el.innerHTML = `<span class="mute">${esc(e.message)}</span> <button class="link" data-action="reload-recalls">Try again</button>`;
    return;
  }
  if (forVin !== state.vin) return;
  const car = `${esc(d.year)} ${esc(d.make)} ${esc(d.model)}`, rs = d.recalls;
  const urgent = rs.some(r => r.park_it || r.park_outside), sev = !rs.length ? "ok" : urgent ? "critical" : "warning";
  const head = `<div class="recall-sum"><span class="dot" style="--c:var(--${sev})"></span><b>${rs.length
    ? `${rs.length} ${rs.length === 1 ? "recall" : "recalls"} issued for the ${car}` : `No recalls on record for the ${car}`}</b></div>`;
  const flags = r => [r.park_it && `<span class="badge critical">Don't drive</span>`, r.park_outside && `<span class="badge critical">Park outside</span>`,
    r.over_the_air && `<span class="badge info">Fixed over the air</span>`].filter(Boolean).join("");
  const rows = state.recallRows = rs.map(r => `<details class="issue"><summary><span class="dot" style="--c:var(--${r.park_it || r.park_outside ? "critical" : "warning"})"></span>
    <span class="t">${esc(r.component)}</span><span class="mute small">${r.reported_on ? fmtDate(r.reported_on) : ""}</span></summary>
    <div class="body col">${flags(r) ? `<div class="flags">${flags(r)}</div>` : ""}<p>${esc(r.summary)}</p>
    ${r.remedy ? `<p class="mute small"><b>Fix:</b> ${esc(r.remedy)}</p>` : ""}<span class="mute small">Campaign ${esc(r.campaign)}</span></div></details>`);
  el.innerHTML = head + `<div id="recallList"></div><p class="mute small" style="margin:10px 0 0">${rs.length
    ? "Not every car of a model is affected. " : ""}Enter your VIN at <a href="${esc(d.lookup_url)}" target="_blank" rel="noopener">nhtsa.gov/recalls</a>
    to see open recalls for your exact car. Source: ${esc(d.source)}.</p>`;
  if (rs.length) collapsible("recallList", rows, 3, "recalls");
}
