// Shared settings popover (gear button + panel).  Owns its DOM so both pages
// get the exact same UI from one source; the host app only reacts via onChange.
import { clamp01 } from "../format.js";

const PANEL_HTML = `
  <div class="ms-title">Settings</div>
  <label class="ms-row">Theme
    <select id="ma-set-theme">
      <option value="auto">Auto (browser)</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
      <option value="night">Night</option>
    </select>
  </label>
  <div class="ms-row"><span>Arrangement blocks</span>
    <input type="range" id="ma-set-arr" min="0.05" max="1" step="0.05">
    <output id="ma-set-arr-v" class="ms-v"></output></div>
  <div class="ms-row"><span>Motif hits</span>
    <input type="range" id="ma-set-mot" min="0.05" max="1" step="0.05">
    <output id="ma-set-mot-v" class="ms-v"></output></div>
  <div class="ms-row"><span>Dim others while selected</span>
    <input type="range" id="ma-set-dim" min="0.05" max="1" step="0.05">
    <output id="ma-set-dim-v" class="ms-v"></output></div>
  <label class="ms-row">Motif overlap
    <select id="ma-set-motif-layout">
      <option value="lanes">Stack lanes</option>
      <option value="largest">Largest only</option>
    </select>
  </label>
  <label class="ms-row"><input type="checkbox" id="ma-set-harmony"> Show chords &amp; harmony</label>
  <button id="ma-set-reset" class="ms-reset">Reset to defaults</button>`;

const SLIDERS = [
  ["arrOpacity", "ma-set-arr"],
  ["motifOpacity", "ma-set-mot"],
  ["dimOthers", "ma-set-dim"],
];

export function mountSettingsPanel({ settings, theme, container, onChange }) {
  const host = container || document.body;
  const btn = document.createElement("button");
  btn.id = "ma-setbtn";
  btn.title = "Settings";
  btn.innerHTML = "&#9881;";
  host.appendChild(btn);

  const panel = document.createElement("div");
  panel.id = "ma-setpanel";
  panel.className = "hide";
  panel.innerHTML = PANEL_HTML;
  document.body.appendChild(panel);

  const sync = () => {
    const th = panel.querySelector("#ma-set-theme");
    if (th) th.value = settings.get("theme");
    const ml = panel.querySelector("#ma-set-motif-layout");
    if (ml) ml.value = settings.get("motifLayout");
    const hz = panel.querySelector("#ma-set-harmony");
    if (hz) hz.checked = !!settings.get("showHarmony");
    for (const [key, id] of SLIDERS) {
      const input = panel.querySelector("#" + id);
      const out = panel.querySelector("#" + id + "-v");
      if (input) input.value = String(settings.get(key));
      if (out) out.textContent = Math.round(settings.get(key) * 100) + "%";
    }
  };

  const changed = (key) => {
    if (theme) theme.apply();
    sync();
    if (onChange) onChange(key);
  };

  const th = panel.querySelector("#ma-set-theme");
  if (th) th.onchange = () => { settings.set("theme", th.value); changed("theme"); };

  const ml = panel.querySelector("#ma-set-motif-layout");
  if (ml) ml.onchange = () => { settings.set("motifLayout", ml.value); changed("motifLayout"); };

  const hz = panel.querySelector("#ma-set-harmony");
  if (hz) hz.onchange = () => { settings.set("showHarmony", hz.checked); changed("showHarmony"); };

  for (const [key, id] of SLIDERS) {
    const input = panel.querySelector("#" + id);
    if (input) input.oninput = () => {
      settings.set(key, clamp01(parseFloat(input.value)));
      changed(key);
    };
  }

  const reset = panel.querySelector("#ma-set-reset");
  if (reset) reset.onclick = () => { settings.reset(); changed("theme"); };

  btn.onclick = (e) => {
    e.stopPropagation();
    const about = document.getElementById("ma-aboutpanel");
    if (about) about.classList.add("hide");
    panel.classList.toggle("hide");
  };
  document.addEventListener("click", (e) => {
    if (!panel.classList.contains("hide") &&
        !e.target.closest("#ma-setpanel") && !e.target.closest("#ma-setbtn")) {
      panel.classList.add("hide");
    }
  });

  sync();
  return { sync, panel, button: btn };
}
