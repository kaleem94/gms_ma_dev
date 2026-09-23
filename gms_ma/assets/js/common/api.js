// Thin API client for the viewer's JSON endpoints.  Keeping every fetch in one
// place means views depend on this small interface, not on URL strings.
export class ApiClient {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  // Mirrors the pages' original `post` helper: returns parsed JSON (even for
  // non-2xx) and rethrows network errors so callers can toast.
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  }

  songs() { return this.get("/api/songs"); }
  song(name) { return this.get("/api/song?name=" + encodeURIComponent(name)); }
  library(queryString) {
    return this.get("/api/library" + (queryString ? "?" + queryString : ""));
  }
  midiPorts() { return this.get("/api/midi-ports"); }
  stop() { return this.post("/api/stop", {}); }
  play(body) { return this.post("/api/play", body); }
  playPattern(body) { return this.post("/api/play-pattern", body); }
  libraryPlay(body) { return this.post("/api/library/play", body); }
  clipboard(body) { return this.post("/api/library/clipboard", body); }
  copyCh1(body) { return this.post("/api/library/copy-ch1", body); }
  sanitize(body) { return this.post("/api/library/sanitize", body); }
}
