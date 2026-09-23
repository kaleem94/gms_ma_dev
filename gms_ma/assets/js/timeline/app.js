// Timeline app: composition root for the song-structure viewer.
import { $, esc } from "../common/dom.js";
import { fmtSec, hashHue } from "../common/format.js";
import { SettingsStore } from "../common/settings.js";
import { ThemeController } from "../common/theme.js";
import { ApiClient } from "../common/api.js";
import { createToast } from "../common/components/toast.js";
import { mountSettingsPanel } from "../common/components/settingsPanel.js";
import { mountAboutPanel } from "../common/components/aboutPanel.js";
import { mountMiniPlayer } from "../common/components/miniPlayer.js";
import { downloadMidi, copyMidi, copyDawCh1, dawExport } from "../common/exportActions.js";
import { gmLabel, meaningfulName } from "../common/gm.js";
import { layoutMotifs, groupByLayer, summarizeLane, timeTicks } from "./layout.js";

const api = new ApiClient();
const settings = new SettingsStore();
const theme = new ThemeController(settings);
const SET = settings.all();   // live object: settings.set() mutates in place
let PAL = {};

const toast = createToast("toast");
const mini = mountMiniPlayer({
  onError: (m) => toast(m, true),
  onTick: (st) => {
    // whole-song playback: mirror the transport position onto the timeline
    if (st.songName && !seeking){
      t0 = st.pos / 1000;
      if (playing !== st.running) setPlay(st.running);
      updatePos();
    }
    render();
  },
  onEnded: (st) => {
    if (st.songName){ setPlay(false); t0 = 0; updatePos(); render(); }
  },
  onSpeed: () => { syncExportBpm(); },
});

const canvas = $("canvas"), ctx = canvas.getContext("2d");
let state = null;            // manifest for current song
let selection = null;        // {track, block} locked selection (placement/motif)
let hover = null;            // {track, block} transient hover preview
let motifRows = [];          // Motifs-view layout (cached by ensureMotifRows)
let motifRowsFor = null;     // cache key: the `state` the layout was built for
let motifRowsLayout = null;  // cache key: SET.motifLayout at build time
let viewMode = "arrangement";// 'arrangement' | 'motifs' | 'repetition'
let t0 = 0, playing = false;
let auditionMotif = null;    // {track, block} loaded in the mini player (highlight)
let clipOk = false;          // clipboard file-copy available
let exportRender = "original";  // selected render for export
let exportBpm = 120, exportBpmEdited = false;
const colH = 34, rowPad = 4, rulerH = 30, labelW = 210;
let pxPerBar = 90;               // horizontal zoom: pixels per displayed bar
const MIN_PX = 14, MAX_PX = 900;
const MIN_LABEL_PX = 60;   // min gap between ruler time labels (adaptive)
const MIN_GRID_PX = 24;    // below this per-bar zoom, only label gridlines
const repHeadH = 26, repStripeH = 16;
const motifLaneH = 16, motifArrH = 12, motifGroupGap = 8;
const trackHeadH = 30, layerHeadH = 14, layerGap = 4;

function applyTheme(){
  theme.apply();
  PAL = theme.palette;
  render(); showPatternInfo();
}

// -- playback mix (mute / solo), session-wide ------------------------------
const muted = new Set();   // track ids muted
let solo = null;           // track id soloed, or null
function isOn(id){ return solo != null ? id === solo : !muted.has(id); }
function enabledIds(){
  if (!state) return [];
  if (solo != null) return [solo];
  return state.tracks.map(t => t.id).filter(id => !muted.has(id));
}
function allEnabled(){ return solo == null && muted.size === 0; }

async function loadSongs(){
  const list = await api.songs();
  const sel = $("songSel"); sel.innerHTML = "";
  for (const s of list){ const o=document.createElement("option"); o.value=s.name; o.text=s.name; sel.appendChild(o); }
  if (list.length) {
    const want = new URLSearchParams(location.search).get("song");
    const match = list.find(s => s.name === want);
    await loadSong(match ? match.name : list[0].name);
  }
  else { $("err") && (document.body.innerHTML = "<div id=err>No songs indexed yet — run `index` first.</div>"); }
}
async function loadPorts(){
  const p = await api.midiPorts();
  const sel = $("devSel"); sel.innerHTML = "";
  if (!p.available){ const o=document.createElement("option"); o.text="(no MIDI output)"; sel.appendChild(o); sel.disabled=true; }
  else { p.outputs.forEach((n,i)=>{ const o=document.createElement("option"); o.value=i; o.text=n; sel.appendChild(o); }); }
  clipOk = !!p.clipboard;
  $("btnPlay").disabled = !p.available;
}
async function loadSong(name){
  const m = await api.song(name);
  state = m; t0 = 0; selection = null; hover = null;
  auditionMotif = null; playing = false; mini.stop();
  pruneMix(); renderStats(); zoomFit(); updatePos();
}
function pruneMix(){
  if (!state) return;
  const ids = new Set(state.tracks.map(t => t.id));
  for (const id of Array.from(muted)) if (!ids.has(id)) muted.delete(id);
  if (solo != null && !ids.has(solo)) solo = null;
}

function barTick(i){ return state.bars[i][0]; }
function barIndexOfTick(tick){
  const bars = state.bars; if (!bars.length) return 0;
  let lo=0, hi=bars.length-1;
  while (lo<hi){ const mid=(lo+hi+1)>>1; if (bars[mid][0]<=tick) lo=mid; else hi=mid-1; }
  return lo;
}
function tickX(tick){
  const bars = state.bars;
  const lo = barIndexOfTick(tick);
  const a = barTick(lo), b = (lo+1<bars.length)? barTick(lo+1) : state.song.total_ticks;
  const span = Math.max(1, b-a);
  return labelW + (lo + (tick-a)/span) * pxPerBar;
}
function xTick(x){
  const px = Math.max(0, x - labelW); const bar = px / pxPerBar;
  const bars = state.bars;
  const idx = Math.min(Math.max(0, Math.floor(bar)), bars.length-1);
  const a = barTick(idx), b = (idx+1<bars.length)? barTick(idx+1) : state.song.total_ticks;
  return Math.round(a + (bar-idx) * (b-a));
}
// -- time model --------------------------------------------------------------
// state.bars = [[tick, sec], ...] at each bar start.  t0 is held in SECONDS
// everywhere in the UI; helpers convert to/from ticks for drawing + seeking.
function timePts(){
  const p = state.bars.map(b=>({t:b[0], s:b[1]}));
  const T = state.song.total_ticks, S = state.song.seconds;
  if (!p.length || p[p.length-1].t < T) p.push({t:T, s:S});
  else p[p.length-1] = {t:T, s:S};
  return p;
}
function secAtTick(tick, pts){
  const p = pts || timePts();
  if (tick <= p[0].t) return p[0].s;
  let lo=0, hi=p.length-1;
  while (lo<hi){ const mid=(lo+hi+1)>>1; if (p[mid].t<=tick) lo=mid; else hi=mid-1; }
  const a=p[lo], b=p[lo+1];
  if (!b) return a.s;
  const f = (tick-a.t)/Math.max(1, b.t-a.t);
  return a.s + f*(b.s-a.s);
}
function tickAtSec(sec, pts){
  const p = pts || timePts();
  if (sec <= p[0].s) return p[0].t;
  let lo=0, hi=p.length-1;
  while (lo<hi){ const mid=(lo+hi+1)>>1; if (p[mid].s<=sec) lo=mid; else hi=mid-1; }
  const a=p[lo], b=p[lo+1];
  if (!b) return a.t;
  const f = (sec-a.s)/Math.max(1e-9, b.s-a.s);
  return Math.round(a.t + f*(b.t-a.t));
}
function tagChips(tags){
  const out = [];
  for (const kind in tags){
    for (const t of tags[kind]){
      out.push(`<span class="tchip ${t.source==="manual"?"manual":""}" title="${kind} · conf ${t.confidence} · ${t.source}">${kind}:${t.value}</span>`);
    }
  }
  return out.join("") || '<span class="stat">no tags</span>';
}
function trackPlacements(trackId){ return state.placements.filter(p=>p.track_id===trackId); }
function motifBlocksOf(trackId){
  const tr = state.tracks.find(t=>t.id===trackId);
  if (!tr) return [];
  const out = [];
  for (const fam of tr.motifs || []){
    const hits = fam.hits || [];
    const layer = tagValue(fam.tags, "layer") || "mixed";
    const chord = tagValue(fam.tags, "chord");
    const harmony = tagValue(fam.tags, "harmony");
    for (const h of hits){
      const endT = (h.end_tick != null) ? h.end_tick : h.start_tick + fam.length_ticks;
      out.push({pattern_id: fam.id, content_id: fam.content_id,
                start_tick: h.start_tick,
                length_ticks: Math.max(1, endT - h.start_tick),
                bar_index: h.bar_index != null ? h.bar_index : 0,
                length_bars: fam.length_bars, notes_count: fam.notes_count,
                kind: "motif", occ: hits.length, sim: h.sim, shift: h.shift,
                layer, chord, harmony});
    }
  }
  return out;
}
// Motifs view layout: one instrument group per track = motif lane(s) stacked,
// then a thin arrangement strip below.  Rows grow as tall as the lane count.
function layoutMotifRows(){
  if (!state) return [];
  const rows = [];
  let top = rulerH;
  for (const tr of state.tracks){
    const hits = motifBlocksOf(tr.id);
    if (!hits.length) continue;
    const lanes = layoutMotifs(hits, SET.motifLayout);
    const laneTop = top + trackHeadH;
    const lanesH = Math.max(1, lanes.length) * motifLaneH;
    const arrTop = laneTop + lanesH;
    const height = trackHeadH + lanesH + motifArrH + motifGroupGap;
    rows.push({ tr, top, laneTop, lanes, laneH: motifLaneH,
                arrTop, arrH: motifArrH, height,
                arrangement: trackPlacements(tr.id) });
    top += height;
  }
  return rows;
}
// Cache the layout: rebuild only when the song or the motif-layout setting
// changes, so per-frame playback highlighting stays cheap.
function ensureMotifRows(){
  if (motifRowsFor === state && motifRowsLayout === SET.motifLayout) return motifRows;
  motifRows = layoutMotifRows();
  motifRowsFor = state;
  motifRowsLayout = SET.motifLayout;
  return motifRows;
}
// Harmony view layout: per track, a section per layer (Mixed / Melody /
// Harmony) with its families' hits packed into lanes.
let harmonyRows = [];
let harmonyRowsFor = null;
let harmonyRowsLayout = null;
function layoutHarmonyRows(){
  if (!state) return [];
  const rows = [];
  let top = rulerH;
  for (const tr of state.tracks){
    const blocks = motifBlocksOf(tr.id);
    if (!blocks.length) continue;
    const groups = groupByLayer(blocks);
    const sections = [];
    let y = top + trackHeadH;
    for (const layer of ["mixed", "melody", "harmony"]){
      const layerBlocks = groups[layer];
      if (!layerBlocks.length) continue;
      const lanes = layoutMotifs(layerBlocks, SET.motifLayout);
      sections.push({ layer, blocks: layerBlocks, lanes,
                      summary: summarizeLane(layerBlocks),
                      headerTop: y, laneTop: y + layerHeadH, laneH: motifLaneH,
                      height: layerHeadH + lanes.length * motifLaneH });
      y += layerHeadH + lanes.length * motifLaneH + layerGap;
    }
    if (!sections.length) continue;
    rows.push({ tr, top, height: y - top, sections });
    top = y + motifGroupGap;
  }
  return rows;
}
function ensureHarmonyRows(){
  if (harmonyRowsFor === state && harmonyRowsLayout === SET.motifLayout) return harmonyRows;
  harmonyRows = layoutHarmonyRows();
  harmonyRowsFor = state;
  harmonyRowsLayout = SET.motifLayout;
  return harmonyRows;
}
// Motif hits under the global playhead during whole-song playback.
function activeMotifHits(){
  const out = new Set();
  if (!state || !playing) return out;
  const rows = viewMode === "harmony" ? harmonyRows : motifRows;
  const tick = tickAtSec(t0);
  for (const r of rows){
    const lanes = r.lanes || (r.sections || []).flatMap(s => s.lanes);
    for (const lane of lanes){
      for (const b of lane){
        if (tick >= b.start_tick && tick < b.start_tick + b.length_ticks) out.add(b);
      }
    }
  }
  return out;
}
// Hit-test a motif/harmony lane stack: motif blocks select/play; the
// arrangement strip (and empty space) is left for scrubbing.
function motifHitAt(y, tick, rows){
  for (const r of rows){
    if (y < r.top || y >= r.top + r.height) continue;
    if (r.arrTop != null && y >= r.arrTop) return null;   // arrangement strip
    const lane = r.lanes
      ? r.lanes[Math.floor((y - r.laneTop) / r.laneH)]
      : laneAtSection(r, y);
    if (!lane) return null;
    for (const b of lane){
      if (tick >= b.start_tick && tick < b.start_tick + b.length_ticks) {
        return { block: b, track: r.tr };
      }
    }
    return null;
  }
  return null;
}
function laneAtSection(row, y){
  for (const s of row.sections){
    if (y >= s.laneTop && y < s.laneTop + s.lanes.length * s.laneH){
      return s.lanes[Math.floor((y - s.laneTop) / s.laneH)];
    }
  }
  return null;
}
function buildRows(){
  return state.tracks
    .map(tr=>({ tr, blocks: trackPlacements(tr.id).slice().sort((a,b)=>a.bar_index-b.bar_index) }))
    .filter(r=>r.blocks.length);
}
function patternOf(tr, block){
  return (tr.patterns||[]).find(p=>p.id===block.pattern_id) || null;
}
// tag_groups returns {kind: [{value, source, confidence}, ...]}; read the value.
function tagValue(tags, kind){
  const list = tags && tags[kind];
  return (list && list.length) ? list[0].value : "";
}
function trackTitle(tr){ return `Ch ${tr.channel}: ${gmLabel(tr.program, tr.is_drums)}`; }
function trackSub(tr){ return meaningfulName(tr.name); }
function blockDescriptor(tr, block){
  const kind = block.kind === "motif" ? "motif"
             : (block.kind === "variant" ? "variant" : "loop");
  return `${tr.is_drums ? "drum" : "melody"} ${kind}`;
}
// One-line breakdown for the bottom hover bar.
function describeBlock(tr, block){
  const p = patternOf(tr, block);
  const notes = p ? p.notes_count : (block.notes_count || 0);
  const occ = (block.occ != null) ? block.occ : countOccurrences(block.content_id);
  let extra = "";
  if (block.layer && block.layer !== "mixed") extra += " · " + block.layer;
  if (SET.showHarmony){
    const chord = tagValue(p && p.tags, "chord");
    const harmony = tagValue(p && p.tags, "harmony");
    if (chord) extra += " · " + chord;
    if (harmony && harmony !== chord) extra += " · " + harmony;
  }
  return `${block.length_bars}-bar ${blockDescriptor(tr, block)} · ${notes} notes · repeats ${occ}× in track${extra}`;
}
function setHoverBar(text){
  const el = $("hoverbar");
  if (el) el.textContent = text || "";
}
function sameSpot(a, b){
  return !!a && !!b && a.track.id === b.track.id &&
         a.block.pattern_id === b.block.pattern_id &&
         a.block.start_tick === b.block.start_tick;
}
function isSelected(tr, block){
  return !!selection && selection.track.id === tr.id &&
         selection.block.pattern_id === block.pattern_id &&
         selection.block.start_tick === block.start_tick;
}
function clearSelection(){
  if (!selection) return;
  selection = null;
  exportBpmEdited = false;
  render(); showPatternInfo();
}
// Cursor-following popup (pointer-events:none) for quick browsing.
function setHoverTip(hit, clientX, clientY){
  const el = $("hovertip");
  if (!el) return;
  if (!hit){ el.classList.add("hide"); el.innerHTML = ""; return; }
  el.innerHTML =
    `<div><b>${esc(trackTitle(hit.track))}</b></div>` +
    `<div>${esc(describeBlock(hit.track, hit.block))}</div>` +
    `<div class="tip-hint">click to lock</div>`;
  el.classList.remove("hide");
  const main = $("scroller");
  if (!main) return;
  const rect = main.getBoundingClientRect();
  let x = clientX - rect.left + main.scrollLeft + 14;
  let y = clientY - rect.top + main.scrollTop + 14;
  x = Math.min(x, main.scrollLeft + main.clientWidth - el.offsetWidth - 8);
  y = Math.min(y, main.scrollTop + main.clientHeight - el.offsetHeight - 8);
  el.style.left = Math.max(4, x) + "px";
  el.style.top = Math.max(4, y) + "px";
}
function renderStats(){
  const m = state, tags = m.tags;
  let chips = tagChips(tags);
  const s = m.stats, song = m.song;
  $("songStats").innerHTML =
    `<b>${m.song.name}</b> &nbsp; ${song.bpm}bpm · ${song.notes} notes ·
     placements ${s.placements} / unique ${s.unique_cover}
     (repeat ×${s.repetition_factor}, compress ${s.compression_ratio})` +
    (s.motif_families ? ` · ${s.motif_families} motif families / ${s.motif_hits} hits` : "");
  const side = $("side");
  let html = "<h3>Song</h3>" + chips;
  html += `<div class="row"><b>Duration</b> ${fmtSec(song.seconds)}s · ${m.bars.length} bars</div>`;
  html += "<hr style='border-color:var(--line)'>";
  html += "<h3>Playback mix</h3>";
  html += `<div class="mixbtns">
    <button data-act="all" title="Unmute all">All on</button>
    <button data-act="solooff" title="Leave solo mode">Clear solo</button>
    <button data-act="reset" title="Clear mute + solo">Reset</button>
    <span class="stat" id="mixnote"></span></div>`;
  html += "<div id='mixlist'>";
  for (const r of buildRows()){
    const tr = r.tr;
    const on = isOn(tr.id);
    html += `<div class="mixrow${on ? "" : " off"}">
      <label class="mchk"><input type="checkbox" data-track="${tr.id}"${on ? " checked" : ""}></label>
      <span class="mtext"><b>${esc(trackTitle(tr))}</b>
        <span class="stat">${trackSub(tr) ? esc(trackSub(tr)) + " · " : ""}${tr.is_drums ? "drums" : ""}${tr.period_bars ? " · " + tr.period_bars + "b loop" : ""}</span></span>
      <button class="msolo${solo === tr.id ? " active" : ""}" data-solo="${tr.id}" title="Solo this instrument">S</button>
    </div>`;
  }
  html += "</div>";
  html += "<hr style='border-color:var(--line)'>";
  html += "<h3>Selected pattern</h3><div id='patInfo'><span class='stat'>hover or click a block…</span></div>";
  side.innerHTML = html;
}
function showPatternInfo(){
  const el = $("patInfo"); if (!el) return;
  const shown = selection || hover;
  if (!shown){ el.innerHTML = "<span class='stat'>hover to preview · click a block to lock</span>"; return; }
  const {block, track:tr} = shown;
  const locked = !!selection;
  const p = patternOf(tr, block);
  const durSec = block.length_ticks * (state.song.seconds/state.song.total_ticks);
  let html = `<div class="row"><b>${esc(trackTitle(tr))}</b>${trackSub(tr) ? " · " + esc(trackSub(tr)) : ""}</div>`;
  html += `<div class="row">bars ${block.length_bars} · ${block.length_ticks} ticks (~${fmtSec(durSec)}) · start bar ${block.bar_index}</div>`;
  if (p){
    html += `<div class="row">confidence ${p.confidence} · ${p.notes_count} notes · <span style="font-family:monospace">${p.content_id}</span></div>`;
    html += auditionButtons(p.id, p.stems);
    html += tagChips(p.tags);
    if (SET.showHarmony){
      const layer = tagValue(p.tags, "layer");
      const chord = tagValue(p.tags, "chord");
      const harmony = tagValue(p.tags, "harmony");
      if (layer || chord || harmony){
        html += `<h3>Harmony</h3>`;
        if (layer) html += `<div class="row"><b>layer</b> ${esc(layer)}</div>`;
        if (chord) html += `<div class="row"><b>chord</b> ${esc(chord)}</div>`;
        if (harmony) html += `<div class="row"><b>progression</b> ${esc(harmony)}</div>`;
      }
    }
    html += renderExport(p);
  } else {
    html += `<div class="row"><span style="font-family:monospace">${block.content_id}</span></div>`;
    html += auditionButtons(block.pattern_id, []);
  }
  const occ = (block.occ != null) ? block.occ : countOccurrences(block.content_id);
  html += `<div class="row" style="margin-top:6px"><b>Occurrences:</b> ${occ}× ${block.kind === "motif" ? "motif hit(s)" : "in this song"}</div>`;
  if (block.kind === "motif"){
    const sim = (block.sim != null) ? block.sim.toFixed(2) : "—";
    html += `<div class="row">family sim ${sim}${block.shift ? ` · transposed ${block.shift > 0 ? "+" : ""}${block.shift}` : ""}</div>`;
  }
  if (locked){
    html += `<div class="row" style="margin-top:8px">
      <button onclick="clearSelection()">Clear selection</button>
      <span class="stat" style="margin-left:6px">locked</span></div>`;
  } else {
    html += `<div class="row stat" style="margin-top:6px">click to lock</div>`;
  }
  el.innerHTML = html;
}
function auditionButtons(pid, stems){
  const entries = [{r:null, l:"Original"}];
  for (const st of (stems||[])) entries.push({r:st.render, l: st.render[0].toUpperCase()+st.render.slice(1)});
  return `<div style="margin:6px 0">` + entries.map(e =>
    `<button onclick="auditionShown(${pid}, ${e.r ? "'"+e.r+"'" : "null"})" style="margin-right:4px">&#9654; ${e.l}</button>`).join("") + `</div>`;
}
// -- export (download / copy, with tempo synced to the current speed) ---------
function baseBpm(){
  return (state && state.song && state.song.bpm) ? Math.round(state.song.bpm) : 120;
}
function exportBpmValue(){
  return exportBpmEdited ? exportBpm
    : Math.max(1, Math.round(baseBpm() * mini.state().speed));
}
function syncExportBpm(){
  const el = $("exp-bpm");
  if (el && !exportBpmEdited) el.value = String(exportBpmValue());
}
function renderLabel(r){ return r === "original" ? "Original" : r[0].toUpperCase() + r.slice(1); }
function renderExport(p){
  const stems = (p && p.stems) ? p.stems.map(s => s.render) : [];
  if (exportRender !== "original" && !stems.includes(exportRender)) exportRender = "original";
  const renders = ["original", ...stems];
  const seg = renders.map(r =>
    `<button class="exp-render${r === exportRender ? " on" : ""}" data-render="${r}">${renderLabel(r)}</button>`).join("");
  return `<h3>Export</h3>
    <div class="seg" id="exp-renders">${seg}</div>
    <div class="row" style="margin-top:6px">Export BPM
      <input id="exp-bpm" type="number" min="20" max="400" step="1" value="${exportBpmValue()}" style="width:72px">
      <span class="stat">· speed ${mini.state().speed.toFixed(2)}×</span></div>
    <div style="margin:6px 0">
      <button data-exp="download">&#8595; Download MIDI</button>
      <button data-exp="copy" ${clipOk ? "" : "disabled"} title="Copy this MIDI to the clipboard">&#128203; Copy MIDI</button>
      <button data-exp="copych1" ${clipOk ? "" : "disabled"} title="Rewrite to channel 1 and copy">&#128203; Copy DAW ch1</button><br>
      <button data-exp="daw" style="margin-top:4px" title="Rewrite to channel 1, copy and download">DAW ch1 export</button>
    </div>`;
}
function doExport(kind){
  const shown = selection || hover;
  if (!shown) return;
  const pid = shown.block.pattern_id;
  const render = exportRender && exportRender !== "original" ? exportRender : null;
  const bpm = Number(exportBpmValue()) || null;
  const opts = { pattern_id: pid, render, bpm, clipboardOk: clipOk, toast };
  if (kind === "download") downloadMidi({ pattern_id: pid, render, bpm });
  else if (kind === "copy") copyMidi(opts);
  else if (kind === "copych1") copyDawCh1(opts);
  else if (kind === "daw") dawExport(opts);
}
function countOccurrences(cid){
  let n=0; for (const pl of state.placements) if (pl.content_id===cid) n++;
  return n;
}
function render(){
  if (!state) return;
  renderLegend();
  if (viewMode === "repetition") renderRepetition();
  else if (viewMode === "harmony") renderHarmony();
  else if (viewMode === "motifs") renderMotifs();
  else renderArrangement();
}
// -- legend ----------------------------------------------------------------
const LEGEND_KEY = "gms_ma.legendOpen";
const LEGACY_LEGEND_KEY = "midi_analyzer.legendOpen";
function legendWasOpen(){
  try {
    const raw = localStorage.getItem(LEGEND_KEY);
    if (raw === null) return localStorage.getItem(LEGACY_LEGEND_KEY) === "1";
    return raw === "1";
  } catch (e) { return false; }
}
function setLegendOpen(open){
  const el = $("legend");
  if (el) el.classList.toggle("hide", !open);
  try { localStorage.setItem(LEGEND_KEY, open ? "1" : "0"); } catch (e) { /* ignore */ }
}
function renderLegend(){
  const v = $("legendView");
  if (!v) return;
  v.textContent = viewMode === "harmony"
    ? "Harmony: one section per layer (mixed / melody / harmony) with chord + progression labels; click a motif to audition it."
    : viewMode === "motifs"
      ? "Motifs: overlapping motifs are stacked into lanes; the strip below is the arrangement. Click a motif to play it."
      : viewMode === "repetition"
        ? "Repetition: each tile row is an extracted loop level; grey cells are one-offs."
        : "Arrangement: each block is one loop placement; click a block to audition it.";
}
// Adaptive seconds ruler: labels are picked so they never sit closer than
// MIN_LABEL_PX, so zooming rearranges them instead of crowding/stretching.
function timeLabels(){
  const totalSec = state.song.seconds;
  const totalBars = Math.max(1, state.bars.length);
  const secPerPx = totalSec / Math.max(1, totalBars * pxPerBar);
  const pts = timePts();
  const out = [];
  let lastX = -Infinity;
  for (const sec of timeTicks(totalSec, secPerPx, MIN_LABEL_PX)){
    const x = tickX(tickAtSec(sec, pts));
    if (x - lastX < MIN_LABEL_PX) continue;   // tempo changes: keep gaps >= min
    out.push({ sec, x });
    lastX = x;
  }
  return out;
}
function drawRuler(W){
  ctx.fillStyle = PAL["20242c"]; ctx.fillRect(0,0,W,rulerH);
  ctx.font = "11px Segoe UI"; ctx.fillStyle = PAL["8b94a3"]; ctx.textAlign = "center";
  const labels = timeLabels();
  for (const L of labels) ctx.fillText(fmtSec(L.sec), L.x, 18);
  return labels;
}
function drawGrid(totalBars, y0, y1, labels){
  ctx.lineWidth = 1;
  if (pxPerBar >= MIN_GRID_PX){        // faint line per bar only when zoomed in
    ctx.strokeStyle = "rgba(255,255,255,0.05)";
    ctx.beginPath();
    for (let i=0;i<=totalBars;i++){
      const x = Math.round(labelW + i*pxPerBar) + 0.5;
      ctx.moveTo(x, y0); ctx.lineTo(x, y1);
    }
    ctx.stroke();
  }
  if (labels && labels.length){        // stronger line at each time label
    ctx.strokeStyle = "rgba(255,255,255,0.09)";
    ctx.beginPath();
    for (const L of labels){
      const x = Math.round(L.x) + 0.5;
      ctx.moveTo(x, y0); ctx.lineTo(x, y1);
    }
    ctx.stroke();
  }
}
function renderArrangement(){
  if (!state) return;
  const m = state;
  const rows = buildRows();
  const totalBars = m.bars.length;
  const W = Math.max(canvas.parentElement.clientWidth, labelW + totalBars*pxPerBar + 20);
  const H = rulerH + rows.length*colH + 20;
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0,0,W,H);
  // ruler
  const labels = drawRuler(W);
  ctx.strokeStyle = PAL["333a46"];
  ctx.beginPath(); ctx.moveTo(0,rulerH+0.5); ctx.lineTo(W,rulerH+0.5); ctx.stroke();
  drawGrid(totalBars, rulerH, H, labels);
  // rows: one block per *placement* (repeats are drawn at every occurrence)
  rows.forEach((r, ri)=>{
    const tr = r.tr;
    const dim = isOn(tr.id) ? 1 : 0.22;
    const y = rulerH + ri*colH;
    ctx.globalAlpha = dim;
    ctx.fillStyle = PAL["20242c"]; ctx.fillRect(0,y,labelW-6,colH-rowPad);
    ctx.fillStyle = tr.is_drums ? PAL["d8a04a"] : PAL["dfe5ee"];
    ctx.textAlign = "left"; ctx.font = "12px Segoe UI";
    ctx.fillText(trackTitle(tr).slice(0,30), 8, y+15);
    ctx.font = "10px Segoe UI"; ctx.fillStyle = PAL["8b94a3"];
    ctx.fillText(trackSub(tr).slice(0,22), 8, y+28);
    ctx.globalAlpha = 1;
    for (const b of r.blocks){
      const x = tickX(b.start_tick);
      const endX = tickX(b.start_tick + b.length_ticks);
      const w = Math.max(3, endX - x);
      const cid = b.content_id;
      const p = patternOf(tr, b);
      const silent = p && !p.notes_count;
      if (silent){
        ctx.globalAlpha = dim;
        ctx.fillStyle = PAL["1c212b"]; ctx.fillRect(x, y+rowPad/2+2, w-2, colH-rowPad-4);
        ctx.strokeStyle = "rgba(255,255,255,0.04)"; ctx.lineWidth = 1;
        ctx.strokeRect(x, y+rowPad/2+2, w-2, colH-rowPad-4);
        ctx.globalAlpha = 1;
        continue;
      }
      const hue = hashHue(cid);
      const locked = isSelected(tr, b);
      const sel = hover && hover.block.pattern_id===b.pattern_id && hover.block.start_tick===b.start_tick;
      ctx.globalAlpha = (locked ? 1 : (selection ? SET.dimOthers : SET.arrOpacity)) * dim;
      ctx.fillStyle = `hsl(${hue},55%,${(sel||locked)?58:42}%)`;
      ctx.fillRect(x, y+rowPad/2+2, w-2, colH-rowPad-4);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = locked? PAL["ffe9a0"] : (sel? PAL["fff"] : "rgba(255,255,255,0.08)");
      ctx.lineWidth = (locked||sel)?2:1;
      ctx.strokeRect(x, y+rowPad/2+2, w-2, colH-rowPad-4);
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle=PAL["262b34"]; ctx.beginPath(); ctx.moveTo(0,y+colH+0.5); ctx.lineTo(W,y+colH+0.5); ctx.stroke();
  });
  // playhead (t0 is seconds)
  const px = tickX(tickAtSec(Math.min(t0, state.song.seconds)));
  ctx.strokeStyle = PAL["ff5252"]; ctx.lineWidth=1.5;
  ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,H); ctx.stroke();
}
// -- motif view -----------------------------------------------------------
// One row per instrument showing every motif-family *hit* as a coloured block
// (families overlap by design, so hits are translucent; shorter first, and the
// longer covering loops draw over them).  Blocks are auditionable like any
// placement: motif families are ordinary patterns (kind "motif").
// -- shared motif lane drawing ------------------------------------------------
function laneLabelText(sum){
  let t = `${sum.hits} hit${sum.hits === 1 ? "" : "s"}`;
  if (sum.layer && sum.layer !== "mixed") t = sum.layer + " · " + t;
  if (SET.showHarmony){
    if (sum.chord) t += " · " + sum.chord;
    if (sum.harmony && sum.harmony !== sum.chord) t += " · " + sum.harmony;
  }
  return t;
}
function drawLaneLabel(lane, ly, laneH, dim){
  const sum = summarizeLane(lane);
  ctx.globalAlpha = 0.8 * dim;
  ctx.fillStyle = PAL["8b94a3"]; ctx.font = "9px Segoe UI"; ctx.textAlign = "left";
  ctx.fillText(laneLabelText(sum).slice(0, 46), 8, ly + laneH - 4);
  ctx.globalAlpha = 1;
}
function drawMotifLane(tr, lane, ly, laneH, W, dim, active, aud, prog){
  ctx.globalAlpha = 0.5 * dim;
  ctx.fillStyle = PAL["1c212b"];
  ctx.fillRect(labelW, ly+1, W-labelW, laneH-2);
  ctx.globalAlpha = 1;
  for (const b of lane){
    const x = tickX(b.start_tick);
    const endX = tickX(b.start_tick + b.length_ticks);
    const w = Math.max(3, endX - x);
    const hue = hashHue(b.content_id);
    const sel = isSelected(tr, b);
    const isAud = !!aud && aud.track.id === tr.id && aud.block === b;
    const isPlaying = isAud || active.has(b);
    ctx.globalAlpha = (isPlaying ? 1 : (sel ? 1 : (selection ? SET.dimOthers : SET.motifOpacity))) * dim;
    ctx.fillStyle = `hsl(${hue},${isPlaying ? 78 : 60}%,${isPlaying ? 60 : (sel ? 58 : 46)}%)`;
    ctx.fillRect(Math.max(labelW, x)+1, ly+2, Math.max(1, w-2), laneH-4);
    if (isAud && prog > 0){                // progress fill from the mini player
      ctx.globalAlpha = 0.55 * dim;
      ctx.fillStyle = PAL["fff"];
      ctx.fillRect(Math.max(labelW, x)+1, ly+2, Math.max(1, (w-2)*prog), laneH-4);
    }
    ctx.globalAlpha = 1;
    if (SET.showHarmony && laneH >= 14 && w > 44 && (b.chord || b.harmony)){
      ctx.fillStyle = "rgba(0,0,0,0.65)";
      ctx.font = "9px Segoe UI"; ctx.textAlign = "left";
      ctx.fillText((b.chord || b.harmony).slice(0, Math.floor((w - 8) / 6)),
                   Math.max(labelW, x)+4, ly + laneH - 5);
      ctx.textAlign = "center";
    }
    ctx.strokeStyle = isPlaying ? PAL["fff"] : (sel ? PAL["ffe9a0"] : "rgba(255,255,255,0.12)");
    ctx.lineWidth = isPlaying ? 3 : (sel ? 2.5 : 1);
    ctx.strokeRect(Math.max(labelW, x)+1, ly+2, Math.max(1, w-2), laneH-4);
    if (isPlaying){                       // pulsing halo marks the sounding hit
      ctx.globalAlpha = (0.35 + 0.35 * (0.5 + 0.5*Math.sin(performance.now()/220))) * dim;
      ctx.strokeStyle = PAL["ff5252"]; ctx.lineWidth = 2;
      ctx.strokeRect(Math.max(labelW, x)+0.5, ly+1.5, Math.max(1, w-1), laneH-3);
      ctx.globalAlpha = 1;
    }
  }
}
function renderMotifs(){
  if (!state) return;
  const m = state;
  const rows = ensureMotifRows();
  const aud = auditionMotif;
  const live = activeMotifHits();          // whole-song playback
  const mst = mini.state();
  const prog = mst.totalMs > 0 ? Math.min(1, mst.pos / mst.totalMs) : 0;
  const totalBars = m.bars.length;
  const W = Math.max(canvas.parentElement.clientWidth, labelW + totalBars*pxPerBar + 20);
  const bottom = rows.length ? rows[rows.length-1].top + rows[rows.length-1].height : rulerH;
  const H = bottom + 20;
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0,0,W,H);
  const labels = drawRuler(W);
  ctx.strokeStyle = PAL["333a46"];
  ctx.beginPath(); ctx.moveTo(0,rulerH+0.5); ctx.lineTo(W,rulerH+0.5); ctx.stroke();
  drawGrid(totalBars, rulerH, H, labels);
  if (!rows.length){
    ctx.textAlign = "left"; ctx.fillStyle = PAL["8b94a3"]; ctx.font = "13px Segoe UI";
    ctx.fillText("No motifs extracted for this song — re-index with motif extraction enabled.", 16, rulerH + 28);
    return;
  }
  for (const r of rows){
    const tr = r.tr;
    const dim = isOn(tr.id) ? 1 : 0.22;
    // label column spans the whole group
    ctx.globalAlpha = dim;
    ctx.fillStyle = PAL["20242c"]; ctx.fillRect(0, r.top, labelW-6, r.height - motifGroupGap + 2);
    ctx.fillStyle = tr.is_drums ? PAL["d8a04a"] : PAL["dfe5ee"];
    ctx.textAlign = "left"; ctx.font = "12px Segoe UI";
    ctx.fillText(trackTitle(tr).slice(0,30), 8, r.top+15);
    ctx.font = "10px Segoe UI"; ctx.fillStyle = PAL["8b94a3"];
    ctx.fillText(trackSub(tr).slice(0,22), 8, r.top+28);
    // motif lanes (non-overlapping), each with a detail label
    r.lanes.forEach((lane, li)=>{
      const ly = r.laneTop + li*r.laneH;
      drawMotifLane(tr, lane, ly, r.laneH, W, dim, live, aud, prog);
      drawLaneLabel(lane, ly, r.laneH, dim);
    });
    // arrangement strip below the lanes (muted, not selectable)
    ctx.globalAlpha = 0.45 * dim;
    ctx.fillStyle = PAL["1c212b"];
    ctx.fillRect(labelW, r.arrTop+1, W-labelW, r.arrH-3);
    for (const b of r.arrangement){
      const x = tickX(b.start_tick);
      const endX = tickX(b.start_tick + b.length_ticks);
      const w = Math.max(2, endX - x);
      const bh = hashHue(b.content_id);
      ctx.globalAlpha = 0.55 * dim;
      ctx.fillStyle = `hsl(${bh},32%,45%)`;
      ctx.fillRect(Math.max(labelW, x)+1, r.arrTop+3, Math.max(1, w-2), r.arrH-6);
    }
    ctx.globalAlpha = dim;
    ctx.fillStyle = PAL["8b94a3"]; ctx.font = "9px Segoe UI"; ctx.textAlign = "left";
    ctx.fillText("arrangement", 8, r.arrTop + r.arrH - 2);
    ctx.globalAlpha = 1;
    ctx.strokeStyle=PAL["262b34"];
    ctx.beginPath(); ctx.moveTo(0, r.top+r.height-1+0.5); ctx.lineTo(W, r.top+r.height-1+0.5); ctx.stroke();
  }
  const px = tickX(tickAtSec(Math.min(t0, state.song.seconds)));
  ctx.strokeStyle = PAL["ff5252"]; ctx.lineWidth=1.5;
  ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,H); ctx.stroke();
}

// -- harmony view -------------------------------------------------------------
// Per track, one section per layer (Mixed / Melody / Harmony) with its motif
// families' hits packed into lanes, plus chord/harmony names when enabled.
const LAYER_COLOR = { mixed: "c7d0dd", melody: "7fd0ff", harmony: "ffd98a" };
function renderHarmony(){
  if (!state) return;
  const m = state;
  const rows = ensureHarmonyRows();
  const aud = auditionMotif;
  const live = activeMotifHits();
  const mst = mini.state();
  const prog = mst.totalMs > 0 ? Math.min(1, mst.pos / mst.totalMs) : 0;
  const totalBars = m.bars.length;
  const W = Math.max(canvas.parentElement.clientWidth, labelW + totalBars*pxPerBar + 20);
  const bottom = rows.length ? rows[rows.length-1].top + rows[rows.length-1].height : rulerH;
  const H = bottom + 20;
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0,0,W,H);
  const labels = drawRuler(W);
  ctx.strokeStyle = PAL["333a46"];
  ctx.beginPath(); ctx.moveTo(0,rulerH+0.5); ctx.lineTo(W,rulerH+0.5); ctx.stroke();
  drawGrid(totalBars, rulerH, H, labels);
  if (!rows.length){
    ctx.textAlign = "left"; ctx.fillStyle = PAL["8b94a3"]; ctx.font = "13px Segoe UI";
    ctx.fillText("No motifs extracted — re-index with motif extraction enabled.", 16, rulerH + 28);
    return;
  }
  for (const r of rows){
    const tr = r.tr;
    const dim = isOn(tr.id) ? 1 : 0.22;
    ctx.globalAlpha = dim;
    ctx.fillStyle = PAL["20242c"]; ctx.fillRect(0, r.top, labelW-6, r.height - motifGroupGap + 2);
    ctx.fillStyle = tr.is_drums ? PAL["d8a04a"] : PAL["dfe5ee"];
    ctx.textAlign = "left"; ctx.font = "12px Segoe UI";
    ctx.fillText(trackTitle(tr).slice(0,30), 8, r.top+15);
    ctx.font = "10px Segoe UI"; ctx.fillStyle = PAL["8b94a3"];
    ctx.fillText(trackSub(tr).slice(0,22), 8, r.top+28);
    for (const s of r.sections){
      // layer header band
      ctx.globalAlpha = 0.6 * dim;
      ctx.fillStyle = PAL["191d25"];
      ctx.fillRect(0, s.headerTop, W, layerHeadH);
      ctx.globalAlpha = dim;
      ctx.fillStyle = PAL[LAYER_COLOR[s.layer] || "c7d0dd"];
      ctx.font = "600 10px Segoe UI"; ctx.textAlign = "left";
      let head = `${s.layer.toUpperCase()}  ·  ${s.summary.families} family(s) · ${s.summary.hits} hit(s)`;
      if (SET.showHarmony && s.summary.chord) head += ` · ${s.summary.chord}`;
      if (SET.showHarmony && s.summary.harmony && s.summary.harmony !== s.summary.chord)
        head += ` · ${s.summary.harmony}`;
      ctx.fillText(head.slice(0, 80), 8, s.headerTop + layerHeadH - 4);
      ctx.globalAlpha = 1;
      s.lanes.forEach((lane, li)=>{
        const ly = s.laneTop + li*s.laneH;
        drawMotifLane(tr, lane, ly, s.laneH, W, dim, live, aud, prog);
        drawLaneLabel(lane, ly, s.laneH, dim);
      });
      ctx.strokeStyle = PAL["262b34"];
      ctx.beginPath();
      ctx.moveTo(0, s.headerTop + s.height + 0.5); ctx.lineTo(W, s.headerTop + s.height + 0.5); ctx.stroke();
    }
    ctx.strokeStyle=PAL["262b34"];
    ctx.beginPath(); ctx.moveTo(0, r.top+r.height-1+0.5); ctx.lineTo(W, r.top+r.height-1+0.5); ctx.stroke();
  }
  const px = tickX(tickAtSec(Math.min(t0, state.song.seconds)));
  ctx.strokeStyle = PAL["ff5252"]; ctx.lineWidth=1.5;
  ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,H); ctx.stroke();
}

function posToCanvas(e){  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width/rect.width;
  return { x:(e.clientX-rect.left)*scaleX, y:(e.clientY-rect.top)*scaleX };
}
// -- repetition (compression) view -------------------------------------------
// Levels: for every instrument we show each *extracted level* — the smallest
// loop length up to the largest — as its own colour-coded tile row (every
// window at that level), plus one thin line per repeating group underneath,
// so you can see how patterns repeat from the smallest to the largest unit.
function trackLevels(tr){
  const cells = trackPlacements(tr.id).slice().sort((a,b)=>a.start_tick-b.start_tick);
  if (!cells.length) return null;
  const freq = new Map();
  for (const c of cells) freq.set(c.length_bars, (freq.get(c.length_bars)||0)+1);
  let baseBars = 1, best = -1;
  for (const [len, cnt] of freq) if (cnt > best){ best = cnt; baseBars = len; }
  const full = cells.filter(c => c.length_bars === baseBars);
  const N = full.length;
  const levels = [];
  for (let m = 1; m <= N; m *= 2){
    const wins = [];
    for (let i = 0; i + m <= N; i += m){
      const seg = full.slice(i, i + m);
      const key = seg.map(s => s.content_id).join('|');
      wins.push({ key, startTick: seg[0].start_tick,
                  endTick: seg[m-1].start_tick + seg[m-1].length_ticks, i });
    }
    if (!wins.length) continue;
    const gmap = new Map();
    for (const w of wins){ if (!gmap.has(w.key)) gmap.set(w.key, []); gmap.get(w.key).push(w); }
    const groups = [...gmap.values()]
      .map(ws => ({ key: ws[0].key, occ: ws.length, windows: ws }))
      .sort((a,b)=> b.occ - a.occ || a.windows[0].i - b.windows[0].i);
    levels.push({ m, bars: baseBars * m, wins, groups });
  }
  // keep the smallest unit through the largest level where anything still
  // repeats — drop fully-grey trailing powers that add no repeating structure
  let lastRep = -1;
  for (let i = 0; i < levels.length; i++){
    if (levels[i].groups.some(g => g.occ >= 2)) lastRep = i;
  }
  if (levels.length){
    levels.length = lastRep >= 0 ? lastRep + 1 : 1;
  }
  return { tr, baseBars, cells: full, N, levels };
}
function renderRepetition(){
  if (!state) return;
  const sections = [];
  for (const tr of state.tracks){
    const lv = trackLevels(tr);
    if (lv && lv.levels.length) sections.push(lv);
  }
  const totalBars = state.bars.length;
  const W = Math.max(canvas.parentElement.clientWidth, labelW + totalBars*pxPerBar + 20);
  const tileH = repStripeH, lineH = 11, gap = 6;
  let H = rulerH;
  for (const s of sections){
    H += repHeadH;
    for (const lv of s.levels){
      H += tileH;
      H += lv.groups.filter(g=>g.occ>=2).length * lineH;
    }
    H += gap;
  }
  H += 12;
  canvas.width = W; canvas.height = H;
  ctx.clearRect(0,0,W,H);
  // ruler
  const labels = drawRuler(W);
  ctx.strokeStyle=PAL["333a46"]; ctx.beginPath(); ctx.moveTo(0,rulerH+0.5); ctx.lineTo(W,rulerH+0.5); ctx.stroke();
  drawGrid(totalBars, rulerH, H, labels);
  let y = rulerH;
  for (const s of sections){
    const tr = s.tr;
    const dim = isOn(tr.id) ? 1 : 0.22;
    ctx.globalAlpha = dim;
    ctx.fillStyle = PAL["191d25"]; ctx.fillRect(0, y, W, repHeadH);
    ctx.fillStyle = tr.is_drums ? PAL["d8a04a"] : PAL["dfe5ee"];
    ctx.textAlign = "left"; ctx.font = "600 12px Segoe UI";
    let head = trackTitle(tr);
    if (trackSub(tr)) head += " — " + trackSub(tr);
    if (tr.engine) head += `  [${tr.engine}${tr.lens && tr.lens !== "pitch" ? "/" + tr.lens : ""}]`;
    ctx.fillText(head.slice(0, 60), 8, y+17);
    ctx.fillStyle = PAL["8b94a3"]; ctx.textAlign = "right"; ctx.font = "10px Segoe UI";
    ctx.fillText(`${s.levels.length} levels · smallest ${s.baseBars}b`, W-10, y+17);
    ctx.globalAlpha = 1;
    y += repHeadH;
    for (const lv of s.levels){
      drawLevelTile(y, lv, dim);
      y += tileH;
      for (const g of lv.groups){
        if (g.occ < 2) continue;
        drawRepeatLine(y, lv, g, dim);
        y += lineH;
      }
    }
    y += gap;
  }
  // playhead
  const px = tickX(tickAtSec(Math.min(t0, state.song.seconds)));
  ctx.strokeStyle = PAL["ff5252"]; ctx.lineWidth=1.5;
  ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,H); ctx.stroke();
}
function drawLevelTile(y, lv, dim){
  ctx.globalAlpha = (dim === undefined) ? 1 : dim;
  ctx.fillStyle = PAL["14171d"]; ctx.fillRect(0, y, canvas.width, repStripeH);
  ctx.textAlign = "left"; ctx.font = "600 10px Segoe UI"; ctx.fillStyle = PAL["c7d0dd"];
  ctx.fillText(`${lv.bars}b`, 8, y + repStripeH - 6);
  ctx.font = "9px Segoe UI"; ctx.fillStyle = PAL["6b7686"];
  ctx.fillText(`${lv.wins.length} window(s)`, 40, y + repStripeH - 6);
  for (const g of lv.groups){
    const hue = hashHue(g.key);
    const repeating = g.occ >= 2;
    for (const w of g.windows){
      const x = tickX(w.startTick);
      const endX = tickX(w.endTick);
      const wpx = Math.max(2, endX - x);
      ctx.fillStyle = repeating ? `hsl(${hue},55%,${46}%)` : PAL["2b323e"];
      ctx.fillRect(Math.max(labelW, x)+1, y+2, wpx-1, repStripeH-4);
    }
  }
  ctx.globalAlpha = 1;
  ctx.strokeStyle=PAL["232833"]; ctx.beginPath();
  ctx.moveTo(0, y+repStripeH+0.5); ctx.lineTo(canvas.width, y+repStripeH+0.5); ctx.stroke();
}
function drawRepeatLine(y, lv, g, dim){
  const d = (dim === undefined) ? 1 : dim;
  const hue = hashHue(g.key);
  const color = `hsl(${hue},62%,54%)`;
  ctx.fillStyle = PAL["101318"]; ctx.fillRect(0, y, canvas.width, 11);
  ctx.fillStyle = color; ctx.fillRect(6, y+2.5, 7, 6);
  ctx.textAlign = "left"; ctx.font = "9px Segoe UI"; ctx.fillStyle = PAL["9aa4b2"];
  ctx.fillText(`×${g.occ} @ ${lv.bars}b`, 18, y + 8.5);
  for (const w of g.windows){
    const x = tickX(w.startTick);
    const endX = tickX(w.endTick);
    const wpx = Math.max(2, endX - x);
    ctx.fillStyle = color; ctx.globalAlpha = 0.55 * d;
    ctx.fillRect(Math.max(labelW, x)+1, y+3.5, Math.max(1, wpx-1), 4);
    ctx.globalAlpha = 1;
  }
}
// -- zoom ---------------------------------------------------------------
const ZOOM_BASE = 90;               // the "100%" pixel width per bar
function zoomPct(){ return Math.round(pxPerBar / ZOOM_BASE * 100); }
function clampPx(p){ return Math.min(MAX_PX, Math.max(MIN_PX, p)); }
function zoomAt(anchorCanvasX, factor){
  if (!state) return;
  const old = pxPerBar;
  const np = clampPx(pxPerBar * factor);
  if (np === old) return;
  const tick = xTick(Math.max(0, anchorCanvasX));   // bar under the cursor
  pxPerBar = np;
  const newX = tickX(tick);
  const container = canvas.parentElement;
  container.scrollLeft = Math.max(0, container.scrollLeft + (newX - anchorCanvasX));
  $("zoomLabel").textContent = zoomPct() + "%";
  render();
}
function zoomBy(factor){
  const c = canvas.parentElement;
  zoomAt(c.scrollLeft + c.clientWidth * 0.5, factor);
}
function zoomFit(){
  if (!state) return;
  const c = canvas.parentElement;
  const tb = Math.max(1, state.bars.length);
  pxPerBar = Math.min(MAX_PX, Math.max(6, Math.floor((c.clientWidth - labelW - 16) / tb)));
  c.scrollLeft = 0;
  $("zoomLabel").textContent = zoomPct() + "%";
  render();
}
$("btnZoomIn").onclick  = ()=> zoomBy(1.25);
$("btnZoomOut").onclick = ()=> zoomBy(1/1.25);
$("btnZoomFit").onclick = ()=> zoomFit();
canvas.addEventListener("wheel", e=>{
  if (e.ctrlKey){                       // mouse wheel / trackpad pinch zoom
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.18 : 1/1.18;
    zoomAt(posToCanvas(e).x, f);
  }
}, {passive:false});
function syncZoomLabel(){ $("zoomLabel").textContent = zoomPct() + "%"; }

let seeking = false;
let wasPlaying = false;   // resume playback from the seeked position
let pointerDown = false, downPos = null, dragged = false, pendingSelect = null;

function hitAtPos(x, y){
  if (!state) return null;
  if (viewMode === "arrangement"){
    const tick = xTick(x);
    const ri = Math.floor((y-rulerH)/colH);
    const rows = buildRows();
    const r = rows[ri];
    if (r){
      for (const b of r.blocks){
        if (tick>=b.start_tick && tick < b.start_tick+b.length_ticks) return {block:b, track:r.tr};
      }
    }
  } else if (viewMode === "motifs"){
    ensureMotifRows();
    return motifHitAt(y, xTick(x), motifRows);
  } else if (viewMode === "harmony"){
    ensureHarmonyRows();
    return motifHitAt(y, xTick(x), harmonyRows);
  }
  return null;
}
async function applySelection(cand){
  if (sameSpot(selection, cand)){
    selection = null;                       // toggle the locked block off
  } else {
    selection = cand;
    hover = cand;
    exportBpmEdited = false;
    setHoverBar(describeBlock(cand.track, cand.block));
    if (viewMode === "motifs" || viewMode === "harmony"){
      playBlock(cand, null);
    }
  }
  render(); showPatternInfo();
}
canvas.addEventListener("pointermove", e=>{
  const {x,y} = posToCanvas(e);
  if (pointerDown && downPos && !dragged){
    const dx = x - downPos.x, dy = y - downPos.y;
    if (dx*dx + dy*dy > 16){                // > ~4px -> drag (seek)
      dragged = true; pendingSelect = null;
      setHoverTip(null);
      wasPlaying = playing;                 // keep playing through the seek
      seeking = true;
    }
  }
  if (seeking && state){
    t0 = secAtTick(xTick(x)); updatePos(); render();
    return;
  }
  const found = hitAtPos(x, y);
  setHoverBar(found ? describeBlock(found.track, found.block) : "");
  setHoverTip(found, e.clientX, e.clientY);
  if (viewMode === "arrangement"){
    const changed = JSON.stringify(hover)!==JSON.stringify(found);
    hover = found;
    if (changed){ render(); showPatternInfo(); }
  }
});
canvas.addEventListener("pointerleave", ()=>{
  setHoverBar("");
  setHoverTip(null);
  if (hover && !pointerDown){ hover = null; render(); showPatternInfo(); }
});
canvas.addEventListener("pointerdown", async e=>{
  const {x, y} = posToCanvas(e);
  pointerDown = true; downPos = {x, y}; dragged = false;
  const cand = (y >= rulerH) ? hitAtPos(x, y) : null;
  if (cand){ pendingSelect = cand; return; }   // decide click vs drag on move/up
  // ruler or empty space: seek (and unlock when clicking empty track area)
  if (y >= rulerH && selection){ selection = null; }
  wasPlaying = playing;                     // do not pause; resume from the new spot
  t0 = secAtTick(xTick(x)); seeking = true;
  updatePos(); render();
});
window.addEventListener("pointerup", async ()=>{
  if (pointerDown && pendingSelect && !dragged){
    await applySelection(pendingSelect);
  }
  const resume = wasPlaying && seeking;
  pointerDown = false; pendingSelect = null; downPos = null; dragged = false; seeking = false;
  wasPlaying = false;
  if (resume){
    mini.seekTo(t0, true);                  // jump the stream to the seeked position
    setPlay(true);
  }
});
function updatePos(){
  if (!state) return;
  const tick = tickAtSec(t0);
  const bar = barIndexOfTick(tick) + 1;
  $("pos").textContent = "▶ " + fmtSec(t0) + " · bar " + bar;
}
function setPlay(on){
  playing = on;                       // Stop stays always enabled
  $("btnPlay").textContent = on ? "❚❚ Pause" : "▶ Play";
}
// Whole-song playback runs through the shared bottom transport, so its seek
// bar scrubs the track as well as motifs.
async function startPlay(startSec){
  if (!state) return;
  auditionMotif = null;
  const ok = await mini.playSong({
    song: state.song.name,
    label: state.song.name + " — full song",
    total_s: state.song.seconds,
    tracks: allEnabled() ? null : enabledIds(),
    device: Number($("devSel").value || 0),
    start: startSec || 0,
  });
  setPlay(!!ok);
}
$("btnPlay").onclick = ()=>{
  if (!state) return;
  if (playing){ mini.pause(); setPlay(false); render(); return; }
  startPlay(t0);
};
$("btnStop").onclick = ()=>{ auditionMotif = null; mini.stop(); setPlay(false); t0 = 0; updatePos(); render(); };
// -- playback mix UI ---------------------------------------------------------
function refreshMixClasses(){
  if (!state) return;
  document.querySelectorAll(".mixrow").forEach(row=>{
    const cb = row.querySelector("input[data-track]");
    const id = Number(cb.dataset.track);
    const on = isOn(id);
    cb.checked = on;
    row.classList.toggle("off", !on);
    const sb = row.querySelector("button.msolo");
    if (sb) sb.classList.toggle("active", solo === id);
  });
  const note = $("mixnote");
  if (note){
    if (solo != null){
      const tr = state.tracks.find(t => t.id === solo);
      note.textContent = tr ? "solo: " + trackTitle(tr) : "solo #" + solo;
    } else if (muted.size){
      note.textContent = muted.size + " muted";
    } else {
      note.textContent = "all on";
    }
  }
}
function mixChanged(){
  refreshMixClasses();
  render();
  if (playing) startPlay(t0);   // re-stream with the new mute/solo mix
}
$("side").addEventListener("click", async ev=>{
  const rb = ev.target.closest("button[data-render]");
  if (rb){ exportRender = rb.dataset.render; showPatternInfo(); return; }
  const ex = ev.target.closest("button[data-exp]");
  if (ex){ doExport(ex.dataset.exp); return; }
  const s = ev.target.closest("button[data-solo]");
  if (s){ const id = Number(s.dataset.solo); solo = (solo === id) ? null : id; await mixChanged(); return; }
  const act = ev.target.closest("button[data-act]");
  if (act){
    if (act.dataset.act === "all"){ muted.clear(); solo = null; }
    else if (act.dataset.act === "solooff"){ solo = null; }
    else if (act.dataset.act === "reset"){ muted.clear(); solo = null; }
    await mixChanged(); return;
  }
  const cb = ev.target.closest("input[data-track]");
  if (cb){
    const id = Number(cb.dataset.track);
    if (cb.checked) muted.delete(id); else muted.add(id);
    await mixChanged();
  }
});
$("side").addEventListener("input", ev=>{
  if (ev.target && ev.target.id === "exp-bpm"){
    exportBpmEdited = true;
    exportBpm = Math.max(1, Number(ev.target.value) || 1);
  }
});
// Audition a pattern in the shared mini player (isolated from other tracks).
function audition(pid, renderMode){
  if (!state) return;
  mini.playPattern({
    pattern_id: pid,
    render: renderMode && renderMode !== "original" ? renderMode : null,
    device: Number($("devSel").value || 0),
  });
}
// Audition the shown block.  In the Motifs view the block is highlighted (and
// filled with playback progress); the global playhead is left untouched.
function playBlock(shown, renderMode){
  const { track, block } = shown;
  setPlay(false);   // any whole-song playback stops when the mini player re-streams
  auditionMotif = ((viewMode === "motifs" || viewMode === "harmony") && block.kind === "motif")
    ? { track, block } : null;
  const span = Math.max(0, secAtTick(block.start_tick + block.length_ticks)
                           - secAtTick(block.start_tick));
  mini.playPattern({
    pattern_id: block.pattern_id,
    render: renderMode && renderMode !== "original" ? renderMode : null,
    label: `${trackTitle(track)} — ${blockDescriptor(track, block)} · ${block.length_bars}b`,
    total_s: span,
    device: Number($("devSel").value || 0),
  });
  render();
}
// Side-panel audition buttons: highlight the shown motif in the Motifs view.
function auditionShown(pid, renderMode){
  const shown = selection || hover;
  if (shown) playBlock(shown, renderMode);
  else audition(pid, renderMode);
}
$("songSel").onchange = async ()=>{ await loadSong($("songSel").value); };
$("viewSel").onchange = ()=>{
  viewMode = $("viewSel").value || "arrangement";
  hover = null; selection = null; render(); showPatternInfo();
};
window.addEventListener("resize", ()=>render());
$("legendToggle").onclick = () => {
  const el = $("legend");
  setLegendOpen(el ? el.classList.contains("hide") : true);
};
setLegendOpen(legendWasOpen());
mountSettingsPanel({
  settings, theme, container: $("top"),
  onChange: (key) => { if (key === "theme") { applyTheme(); } else { render(); showPatternInfo(); } },
});
mountAboutPanel({ container: $("top"), isLibrary: false });
applyTheme();
theme.bindMedia(() => applyTheme());
loadSongs(); loadPorts();

// Inline HTML handlers resolve against the global scope; expose the functions
// the generated markup calls (pattern audition buttons in the side panel).
Object.assign(globalThis, { audition, auditionShown, clearSelection });
