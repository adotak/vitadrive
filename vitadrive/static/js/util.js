import {state} from "./state.js";

export const $ = s => document.querySelector(s);
export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
export const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

export function setHTML(el, html) {
  if (el._html === html) return false;
  el._html = html; el.innerHTML = html; return true;
}

export function countUp(el, to) {
  const from = +el.dataset.n || 0, fmt = n => Math.round(n).toLocaleString();
  el.dataset.n = to;
  if (reduceMotion.matches || from === to) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  const step = t => { const p = Math.min(1, (t - t0) / 900); el.textContent = fmt(from + (to - from) * (1 - (1 - p) ** 3)); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

export const badge = s => `<span class="badge ${s}">${s}</span>`;
export const money = (n, d = 2) => n.toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
export const monthName = ym => new Date(`${ym}-01T00:00`).toLocaleString(undefined, {month: "short"});
export const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"});
export const dueText = m => m.days_remaining < 0 ? `overdue by ${-m.days_remaining} days` : m.days_remaining === 0 ? "due today"
  : `in ${m.days_remaining} days`;

export function collapsible(id, rows, n, noun) {
  const el = $("#" + id), open = state.expanded.has(id);
  setHTML(el, rows.map((r, i) => i < n ? r : r.replace(/^<(\w+) class="/, '<$1 class="extra ').replace(/^<(\w+)>/, '<$1 class="extra">')).join("")
    + (rows.length > n ? `<button class="link" data-action="toggle-more" data-id="${esc(id)}">${open ? "Show less" : `Show all ${rows.length} ${noun}`}</button>` : ""));
  open ? el.removeAttribute("data-collapsed") : el.setAttribute("data-collapsed", "");
}
