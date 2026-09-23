// Safe localStorage JSON wrapper (never throws in private/blocked contexts).
export function readJson(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

export function writeJson(key, obj) {
  try {
    localStorage.setItem(key, JSON.stringify(obj));
  } catch (e) {
    /* storage unavailable */
  }
}
