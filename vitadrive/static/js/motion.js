import {state} from "./state.js";
import {reduceMotion, $} from "./util.js";

const REVEAL = ".view > *, .glance-item, .svc, .issue, .sys, .kpi, .place";
let revealer;

export function arm(el) {
  if (reduceMotion.matches) return;
  clearTimeout(el._sr);
  el.classList.remove("sr-in"); el.classList.add("sr");
  revealer.observe(el);
}

export const armAll = root => root.querySelectorAll(REVEAL).forEach(arm);
export const motion = () => reduceMotion.matches ? "instant" : "smooth";

function onScroll() {
  state.scrollFrame = false;
  const max = document.documentElement.scrollHeight - innerHeight;
  $(".scroll-progress").style.setProperty("--p", max > 0 ? Math.min(1, scrollY / max) : 0);
  document.body.classList.toggle("scrolled", scrollY > 8);
}

export function initMotion() {
  revealer = new IntersectionObserver(entries => {
    let k = 0;
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const el = e.target, delay = Math.min(k++, 6) * 70;
      revealer.unobserve(el);
      el.style.setProperty("--d", delay + "ms");
      el.classList.add("sr-in");
      clearTimeout(el._sr);
      el._sr = setTimeout(() => el.classList.remove("sr", "sr-in"), 750 + delay);
    }
  }, {rootMargin: "0px 0px -6% 0px"});
  new MutationObserver(records => {
    for (const r of records) for (const n of r.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.matches(REVEAL)) arm(n);
      armAll(n);
    }
  }).observe($("#main"), {childList: true, subtree: true});
  addEventListener("scroll", () => { if (!state.scrollFrame) { state.scrollFrame = true; requestAnimationFrame(onScroll); } }, {passive: true});
  addEventListener("resize", onScroll, {passive: true});
}

export {onScroll};
