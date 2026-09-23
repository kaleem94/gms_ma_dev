// Shared transient toast (used by the library page's action feedback).
import { $ } from "../dom.js";

export function createToast(rootId = "toast") {
  const el = $(rootId);
  return function toast(msg, isErr) {
    if (!el) return;
    el.textContent = msg;
    el.className = isErr ? "err" : "";
    el.style.opacity = 1;
    clearTimeout(el._h);
    el._h = setTimeout(() => { el.style.opacity = 0; }, 3000);
  };
}
