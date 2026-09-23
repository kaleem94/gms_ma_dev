// Tiny DOM + string helpers shared by every page module.
export const $ = (id) => document.getElementById(id);

const ESC_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};

export function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}

export function on(el, ev, fn, opts) {
  if (el) el.addEventListener(ev, fn, opts);
  return el;
}

export function make(tag, attrs, html) {
  const node = document.createElement(tag);
  if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (html != null) node.innerHTML = html;
  return node;
}
