// Shared compact transport bar (bottom of the library and timeline pages).
// Owns its DOM and local position clock; the host supplies the source and
// optional callbacks.  Pattern playback uses /api/library/play (supports seek
// via `t`, holds for `end_s`, reports `total_s`); full songs use /api/play.
import { fmtSec } from "../format.js";

const BAR_HTML = `
  <button data-mb="play" disabled title="Play / pause">&#9654;</button>
  <span data-mb="info">Select a loop to play</span>
  <span class="t" data-mb="cur">0:00</span>
  <input data-mb="seek" type="range" min="0" max="1000" step="10" value="0" title="Seek">
  <span class="t" data-mb="tot">0:00</span>
  <label class="spd" title="Playback speed (0.25–3×)">
    <input data-mb="speed" type="range" min="0.25" max="3" step="0.05" value="1">
    <span data-mb="speed-v">1.00×</span>
  </label>
  <button data-mb="stop" title="Stop and reset" disabled>&#9632;</button>`;

export function mountMiniPlayer({ container, id = "mbar", onTick, onEnded, onError, onSpeed } = {}) {
  const bar = document.createElement("div");
  bar.id = id;
  bar.className = "mbar";
  bar.innerHTML = BAR_HTML;
  (container || document.body).appendChild(bar);

  const playBtn = bar.querySelector('[data-mb="play"]');
  const infoEl = bar.querySelector('[data-mb="info"]');
  const curEl = bar.querySelector('[data-mb="cur"]');
  const seekEl = bar.querySelector('[data-mb="seek"]');
  const totEl = bar.querySelector('[data-mb="tot"]');
  const stopBtn = bar.querySelector('[data-mb="stop"]');
  const speedEl = bar.querySelector('[data-mb="speed"]');
  const speedV = bar.querySelector('[data-mb="speed-v"]');

  const st = { pid: null, songName: null, render: "original", totalMs: 0, pos: 0,
               running: false, last: 0, scrubbing: false, label: "",
               device: 0, tracks: null, speed: 1.0 };

  const fail = (msg) => { if (onError) onError(msg); };

  function update() {
    const has = !!(st.pid || st.songName);
    playBtn.textContent = st.running ? "❚❚" : "▶";
    playBtn.disabled = !has;
    stopBtn.disabled = !has;
    curEl.textContent = fmtSec(st.pos / 1000);
    totEl.textContent = fmtSec(st.totalMs / 1000);
    infoEl.textContent = st.label || "Select a loop to play";
    seekEl.max = Math.max(1, st.totalMs);
    if (!st.scrubbing) seekEl.value = Math.min(st.totalMs, st.pos);
    if (speedV) speedV.textContent = st.speed.toFixed(2) + "×";
    if (speedEl) speedEl.value = String(st.speed);
  }

  async function netStop() {
    try { await fetch("/api/stop", { method: "POST", body: "{}" }); } catch (e) { /* ignore */ }
  }

  async function stream(tSec) {
    const isSong = !!st.songName;
    const url = isSong ? "/api/play" : "/api/library/play";
    const body = isSong ? { song: st.songName } : { pattern_id: st.pid };
    if (st.device != null) body.device = st.device;
    if (st.speed && st.speed !== 1) body.speed = st.speed;
    if (isSong && st.tracks && st.tracks.length) body.tracks = st.tracks;
    if (!isSong && st.render && st.render !== "original") body.render = st.render;
    if (tSec > 0) body.t = tSec;
    try {
      const r = await fetch(url, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) { fail(j.error || "play failed"); st.running = false; update(); return false; }
      if (j.total_s != null && j.total_s > 0) st.totalMs = Math.round(j.total_s * 1000);
      st.pos = Math.min(tSec * 1000, st.totalMs);
      st.running = true; st.last = performance.now();
    } catch (e) {
      fail("play failed: " + e); st.running = false; update(); return false;
    }
    update();
    return true;
  }

  function playPattern({ pattern_id, render, label, total_s, device } = {}) {
    st.pid = pattern_id; st.songName = null;
    st.render = (render && render !== "original") ? render : "original";
    st.totalMs = total_s ? Math.round(total_s * 1000) : 0;
    st.label = label || ""; st.pos = 0;
    st.device = device != null ? device : 0;
    st.tracks = null;
    return stream(0);
  }

  function playSong({ song, label, total_s, tracks, device, start } = {}) {
    st.pid = null; st.songName = song; st.render = "original";
    st.totalMs = total_s ? Math.round(total_s * 1000) : 0;
    st.label = label || "";
    st.pos = Math.max(0, (start || 0) * 1000);
    st.device = device != null ? device : 0;
    st.tracks = (tracks && tracks.length) ? tracks : null;
    return stream(start || 0);
  }

  async function pause() {
    if (!st.running) return;
    st.running = false; await netStop(); update();
  }

  async function toggle() {
    if (!st.pid && !st.songName) return;
    if (st.running) { await pause(); return; }
    if (st.pos >= st.totalMs) st.pos = 0;   // reached the end -> start over
    st.last = performance.now();
    await stream(st.pos / 1000);
  }

  async function stop() {
    st.running = false; st.pos = 0;
    await netStop(); update();
  }

  async function seekTo(sec, restart = true) {
    st.pos = Math.min(st.totalMs, Math.max(0, sec * 1000));
    if (restart && st.running) {
      st.running = false;
      await netStop();
      st.last = performance.now();
      await stream(st.pos / 1000);
    } else update();
  }

  function seek(sec) {
    st.pos = Math.min(st.totalMs, Math.max(0, sec * 1000));
    update();
  }

  let wasRunning = false;
  function tick(now) {
    if (st.running && !st.scrubbing) {
      st.pos += (now - st.last) * st.speed;
      if (st.pos >= st.totalMs) { st.pos = st.totalMs; st.running = false; }
    }
    st.last = now;
    if (st.running || wasRunning) {
      update();
      if (onTick) onTick(st);
    }
    if (wasRunning && !st.running && onEnded) onEnded(st);
    wasRunning = st.running;
    requestAnimationFrame(tick);
  }

  playBtn.onclick = toggle;
  stopBtn.onclick = stop;
  seekEl.addEventListener("input", () => {
    if (!st.pid && !st.songName) return;
    st.scrubbing = true;
    st.pos = Math.min(st.totalMs, Number(seekEl.value) || 0);
    curEl.textContent = fmtSec(st.pos / 1000);
  });
  seekEl.addEventListener("change", async () => {
    st.scrubbing = false;
    if (!st.pid && !st.songName) { update(); return; }
    st.pos = Math.min(st.totalMs, Number(seekEl.value) || 0);
    const was = st.running;
    st.running = false;
    if (was) { await netStop(); st.last = performance.now(); await stream(st.pos / 1000); }
    else update();
  });
  if (speedEl) {
    speedEl.addEventListener("input", () => {
      st.speed = Math.min(3, Math.max(0.25, Number(speedEl.value) || 1));
      if (speedV) speedV.textContent = st.speed.toFixed(2) + "×";
      if (onSpeed) onSpeed(st.speed);
    });
    speedEl.addEventListener("change", async () => {
      if (st.running) await seekTo(st.pos / 1000, true);   // re-stream at the new speed
    });
  }

  update();
  requestAnimationFrame(tick);
  return { el: bar, playPattern, playSong, toggle, pause, stop, seek, seekTo, state: () => st };
}
