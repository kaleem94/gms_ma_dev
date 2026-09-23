// Shared pattern download / clipboard actions (library + timeline).
// Endpoints: GET /api/library/download; POST /api/library/{clipboard,copy-ch1,sanitize}.
// `bpm` (optional) retempoes the exported MIDI so it plays at the current speed.

export function downloadUrl({ pattern_id, render, bpm, mode } = {}) {
  const q = new URLSearchParams({ pattern_id: String(pattern_id) });
  if (render && render !== "original") q.set("render", render);
  if (bpm) q.set("bpm", String(bpm));
  if (mode) q.set("mode", mode);
  return "/api/library/download?" + q.toString();
}

export function downloadMidi(opts = {}) {
  const a = document.createElement("a");
  a.href = downloadUrl(opts);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function normRender(render) {
  return render && render !== "original" ? render : null;
}

async function post(url, body, toast) {
  try {
    const r = await fetch(url, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok && toast) toast(j.error || "request failed", true);
    return j;
  } catch (e) {
    if (toast) toast("request failed: " + e, true);
    return null;
  }
}

export async function copyMidi({ pattern_id, render, bpm, clipboardOk = true, toast } = {}) {
  if (!clipboardOk) { if (toast) toast("Clipboard file copy unavailable", true); return null; }
  const r = await post("/api/library/clipboard",
    { pattern_id, render: normRender(render), bpm: bpm || null }, toast);
  if (r && r.ok && toast) toast("MIDI copied to clipboard — paste into a DAW or Explorer");
  return r;
}

export async function copyDawCh1({ pattern_id, render, bpm, clipboardOk = true, toast } = {}) {
  if (!clipboardOk) { if (toast) toast("Clipboard file copy unavailable", true); return null; }
  const r = await post("/api/library/copy-ch1",
    { pattern_id, render: normRender(render), bpm: bpm || null }, toast);
  if (r && r.ok && toast) toast(`DAW ch1 MIDI copied to clipboard (${r.filename})`);
  return r;
}

export async function dawExport({ pattern_id, render, bpm, toast } = {}) {
  const r = await post("/api/library/sanitize",
    { pattern_id, render: normRender(render), bpm: bpm || null }, toast);
  if (!r) return;
  if (r.error) { if (toast) toast("export failed: " + r.error, true); return; }
  if (toast) toast(`DAW ch1 export written as ${r.filename}${r.clipped ? " · copied to clipboard" : ""}`);
  downloadMidi({ pattern_id, render, bpm, mode: "ch1" });
}
