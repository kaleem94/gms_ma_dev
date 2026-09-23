// Pure formatting / colour helpers (no DOM, no state).

export function fmtSec(s) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), x = Math.floor(s % 60);
  return m + ":" + String(x).padStart(2, "0");
}

export function hashHue(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 360;
}

// Alias kept for callers that use the shorter name.
export const hue = hashHue;

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

export function clamp01(v) {
  const n = Number(v);
  return isNaN(n) ? 0.6 : Math.min(1, Math.max(0.05, n));
}
