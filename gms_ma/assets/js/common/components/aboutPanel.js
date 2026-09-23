// Shared About popover (info button + panel).  Mirrors settingsPanel so both
// pages get the same project info from one source; version/license are fetched
// from /api/about and fall back to static values if the request fails.
const FALLBACK = {
  name: "gms-ma",
  tagline: "GMS MIDI analyzer",
  version: "",
  license: "Apache-2.0",
  license_url: "/license",
  repo: "https://github.com/kaleem94/gms-ma",
  disclaimer: "",
};

const TIPS = [
  "Hover a block for details; click to lock the selection",
  "Drag the ruler (or the seek bar) to scrub playback",
  "Open Settings (gear) to switch theme and tune opacities",
];

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function render(panel, info, isLibrary) {
  const other = isLibrary
    ? { href: "/", label: "Song timeline" }
    : { href: "/library", label: "Loop library" };
  const version = info.version ? `Version ${esc(info.version)} · ` : "";
  panel.innerHTML = `
    <div class="ma-about-name">${esc(info.name)}</div>
    <div class="ma-about-tag">${esc(info.tagline)}</div>
    <p class="ma-about-desc">A computational-musicology workbench that decomposes
      General-MIDI songs into repeating units &mdash; cover loops plus a grid-free
      motif catalogue &mdash; and visualises form, repetition, texture, key and
      style, with live MIDI audition. Built on the Python standard library and
      <code>mido</code>; no numpy or ML stack.</p>
    <ul class="ma-about-tips">${TIPS.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
    ${info.disclaimer ? `<p class="ma-about-disclaimer">${esc(info.disclaimer)}</p>` : ""}
    <div class="ma-about-meta">${version}<a href="${esc(info.license_url)}"
      target="_blank" rel="noopener">${esc(info.license)}</a></div>
    <div class="ma-about-nav">See also:
      <a href="${esc(other.href)}">${esc(other.label)}</a>
      &middot; <a href="${esc(info.repo)}" target="_blank" rel="noopener">GitHub</a></div>`;
}

export function mountAboutPanel({ container, isLibrary } = {}) {
  const host = container || document.body;
  const btn = document.createElement("button");
  btn.id = "ma-aboutbtn";
  btn.title = "About gms-ma";
  btn.setAttribute("aria-label", "About gms-ma");
  btn.innerHTML = "&#9432;";
  host.appendChild(btn);

  const panel = document.createElement("div");
  panel.id = "ma-aboutpanel";
  panel.className = "hide";
  document.body.appendChild(panel);

  const info = { ...FALLBACK };
  const apply = (data) => {
    if (!data || typeof data !== "object") return;
    for (const k in FALLBACK) if (data[k] !== undefined) info[k] = data[k];
    render(panel, info, !!isLibrary);
  };
  render(panel, info, !!isLibrary);

  fetch("/api/about", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .then(apply)
    .catch(() => { /* keep static fallback */ });

  btn.onclick = (e) => {
    e.stopPropagation();
    const settings = document.getElementById("ma-setpanel");
    if (settings) settings.classList.add("hide");
    panel.classList.toggle("hide");
  };
  document.addEventListener("click", (e) => {
    if (!panel.classList.contains("hide") &&
        !e.target.closest("#ma-aboutpanel") && !e.target.closest("#ma-aboutbtn")) {
      panel.classList.add("hide");
    }
  });

  return { panel, button: btn, apply };
}
