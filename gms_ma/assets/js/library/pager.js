// Pure pagination math for the library list (no DOM, no state).
// ``size <= 0`` means "All" (a single page).

export const PAGE_SIZES = [10, 25, 50, 100, 200, 0];
export const DEFAULT_PAGE_SIZE = 25;

/** Coerce a stored page-size string to a valid PAGE_SIZES value (or fallback).
 *  ``null``/``""`` (no stored preference) must NOT be read as 0 == "All". */
export function validPageSize(raw, fallback = DEFAULT_PAGE_SIZE) {
  if (raw === null || raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  return PAGE_SIZES.includes(n) ? n : fallback;
}

/** Inclusive page range [start, end] clamped to [1, pages] around `page`. */
export function windowRange(page, pages, radius) {
  const p = clampPage(page, pages);
  const r = Math.max(0, Math.floor(radius) || 0);
  return { start: Math.max(1, p - r), end: Math.min(pages, p + r) };
}

export function pageCount(total, size) {
  if (!size || size <= 0) return 1;
  return Math.max(1, Math.ceil(total / size));
}

export function clampPage(page, pages) {
  const p = Number(page);
  if (isNaN(p) || p < 1) return 1;
  return Math.min(pages, Math.floor(p));
}

export function pageSlice(items, page, size) {
  if (!size || size <= 0) return items.slice();
  const p = clampPage(page, pageCount(items.length, size));
  const start = (p - 1) * size;
  return items.slice(start, start + size);
}

export function rangeLabel(page, size, total) {
  if (!total) return "0 of 0";
  if (!size || size <= 0) return `1\u2013${total} of ${total}`;
  const p = clampPage(page, pageCount(total, size));
  const start = (p - 1) * size + 1;
  const end = Math.min(total, p * size);
  return `${start}\u2013${end} of ${total}`;
}
