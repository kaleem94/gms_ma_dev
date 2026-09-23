// Theme controller: resolves auto/light/dark/night, applies it to the document
// and exposes the resolved canvas palette (read from the CSS custom properties
// defined in assets/css/tokens.css) so canvas drawing stays in sync.

export const PAL_KEYS = [
  "0f1115", "171a21", "1e232c", "1e232e", "1c2230", "2a2f3a", "2b3342",
  "dfe5ee", "8b94a3", "9aa4b2", "4da3ff", "1b2330", "2c3550", "cfe3ff", "ffb454",
  "ffd9a0", "ff8f8f", "f2c94c", "161b24", "1a1f2a", "1c2a41", "1c212b", "20242c",
  "262b34", "333a46", "ff5252", "d8a04a", "ffe9a0", "fff", "14171d", "191d25",
  "c7d0dd", "6b7686", "2b323e", "101318", "232833", "141920", "171c26", "1f2633",
  "7fd0ff", "132638", "1d3a57", "c9a8ff", "6b4da3", "ffd98a", "8a6a1f", "08121f",
  "0c3b2e", "bfffd9", "1f7a56", "3b0c0c", "ffc9c9", "a03232", "9fb6cf",
];

export class ThemeController {
  constructor(settings) {
    this.settings = settings;
    this.palette = {};
    this._media = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  }

  effective() {
    const theme = this.settings.get("theme");
    if (theme !== "auto") return theme;
    return this._media && this._media.matches ? "dark" : "light";
  }

  cssVar(name) {
    return (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim();
  }

  apply() {
    document.documentElement.setAttribute("data-theme", this.effective());
    this.refreshPalette();
  }

  refreshPalette() {
    const p = {};
    for (const k of PAL_KEYS) p[k] = this.cssVar("--s" + k);
    this.palette = p;
  }

  // Live-follow the OS setting while the user has not overridden it.
  bindMedia(cb) {
    if (this._media && this._media.addEventListener) {
      this._media.addEventListener("change", () => {
        if (this.settings.get("theme") === "auto") {
          this.apply();
          if (cb) cb();
        }
      });
    }
  }
}
