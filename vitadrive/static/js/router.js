import {state} from "./state.js";
import {armAll, motion, onScroll} from "./motion.js";
import {$} from "./util.js";

const views = [...document.querySelectorAll(".view")];
const navLinks = [...document.querySelectorAll(".nav-links a")];

export function route({focus = false, force = false} = {}) {
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  const view = target ? target.closest(".view") : views[0];
  if (!view) return;
  const changed = view !== state.currentView;
  if (changed) {
    state.currentView = view;
    armAll(view);
    for (const v of views) v.hidden = v !== view;
    for (const a of navLinks) {
      const on = a.getAttribute("href") === `#${view.id}`, rail = a.parentElement;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      if (on && rail.scrollWidth > rail.clientWidth) rail.scrollTo({left: a.offsetLeft - rail.offsetLeft - 14, behavior: motion()});
    }
    $("#currentSection").textContent = view.dataset.title;
    document.title = view === views[0] ? "VitaDrive" : `${view.dataset.title} · VitaDrive`;
    if (view.id === "help" && state.nearMap) requestAnimationFrame(() => {
      state.nearMap.invalidateSize();
      if (state.nearBounds) state.nearMap.fitBounds(state.nearBounds, {padding: [30, 30], maxZoom: 15});
    });
  }
  if (target && target !== view) requestAnimationFrame(() => target.scrollIntoView({behavior: motion(), block: "start"}));
  else if (changed || force) window.scrollTo({top: 0, behavior: changed ? "instant" : motion()});
  if (focus && changed) view.querySelector("h1")?.focus({preventScroll: true});
  requestAnimationFrame(onScroll);
}

export function initRouter() {
  window.addEventListener("hashchange", () => route({focus: true}));
  document.addEventListener("click", e => {
    const a = e.target.closest('a[href^="#"]');
    if (a && a.getAttribute("href") === location.hash) { e.preventDefault(); route({force: true}); }
    const s = e.target.closest("summary"), d = s?.parentElement;
    if (d && !d.open) { d.classList.remove("revealing"); void d.offsetWidth; d.classList.add("revealing"); }
  });
  route();
}
