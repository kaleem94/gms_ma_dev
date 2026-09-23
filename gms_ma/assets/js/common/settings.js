// Settings store: one observable source of truth, persisted to localStorage.
// Both the timeline and library pages use the same key so preferences are shared.
import { readJson, writeJson } from "./storage.js";
import { clamp01 } from "./format.js";

export const SETTINGS_KEY = "gms_ma.settings";
// Pre-rename key, read once so existing preferences survive the rename.
export const LEGACY_SETTINGS_KEY = "midi_analyzer.settings";
export const SET_DEFAULTS = {
  theme: "auto",        // auto | light | dark | night
  arrOpacity: 1.0,      // arrangement block opacity
  motifOpacity: 0.85,   // motif hit opacity
  dimOthers: 0.6,       // fade of non-selected blocks while one is selected
  motifLayout: "lanes", // motif view overlap handling: lanes | largest
  showHarmony: true,    // show chord / harmony names on the timeline
};
export const THEMES = ["auto", "light", "dark", "night"];
export const MOTIF_LAYOUTS = ["lanes", "largest"];

export class SettingsStore {
  constructor(key = SETTINGS_KEY) {
    this.key = key;
    this.state = { ...SET_DEFAULTS };
    this._subs = new Set();
    this.load();
  }

  load() {
    let saved = readJson(this.key);
    const migrated = !saved && readJson(LEGACY_SETTINGS_KEY);
    if (migrated) saved = migrated;
    if (saved) {
      for (const k in SET_DEFAULTS) {
        if (saved[k] !== undefined) this.state[k] = saved[k];
      }
    }
    this._normalize();
    if (migrated) this.save();
  }

  _normalize() {
    if (!THEMES.includes(this.state.theme)) this.state.theme = "auto";
    if (!MOTIF_LAYOUTS.includes(this.state.motifLayout)) this.state.motifLayout = "lanes";
    this.state.showHarmony = !!this.state.showHarmony;
    for (const k of ["arrOpacity", "motifOpacity", "dimOthers"]) {
      this.state[k] = clamp01(this.state[k]);
    }
  }

  all() { return this.state; }
  get(key) { return this.state[key]; }

  set(key, value) {
    this.state[key] = value;
    this._normalize();
    this.save();
    this._emit();
  }

  patch(obj) {
    Object.assign(this.state, obj);
    this._normalize();
    this.save();
    this._emit();
  }

  reset() {
    // mutate in place: callers keep a live reference from all()
    for (const k in SET_DEFAULTS) this.state[k] = SET_DEFAULTS[k];
    this.save();
    this._emit();
  }

  save() { writeJson(this.key, this.state); }

  subscribe(fn) {
    this._subs.add(fn);
    return () => this._subs.delete(fn);
  }

  _emit() { for (const fn of this._subs) fn(this.state); }
}
