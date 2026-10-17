import {state} from "./state.js";

export async function api(path, opts = {}) {
  const headers = {...(opts.headers || {})};
  if (state.authEnabled && window.Clerk?.session) headers.Authorization = `Bearer ${await window.Clerk.session.getToken()}`;
  if (opts.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {...opts, headers});
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const msg = typeof body.detail === "string" ? body.detail : body.error?.message || r.statusText;
    if (!opts.quiet) alert(msg);
    throw new Error(msg);
  }
  return r.status === 204 ? null : opts.blob ? r.blob() : r.json();
}
