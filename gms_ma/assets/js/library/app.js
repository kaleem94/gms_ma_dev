// Library app: composition root for the loop browser.
import { $, esc } from "../common/dom.js";
import { fmtSec, hue } from "../common/format.js";
import { gmLabel, trackDisplayName } from "../common/gm.js";
import { SettingsStore } from "../common/settings.js";
import { ThemeController } from "../common/theme.js";
import { ApiClient } from "../common/api.js";
import { createToast } from "../common/components/toast.js";
import { mountSettingsPanel } from "../common/components/settingsPanel.js";
import { mountAboutPanel } from "../common/components/aboutPanel.js";
import { mountMiniPlayer } from "../common/components/miniPlayer.js";
import { downloadMidi, copyMidi, copyDawCh1, dawExport } from "../common/exportActions.js";
import { containmentForest } from "./forest.js";
import { PAGE_SIZES, validPageSize, windowRange, pageCount, clampPage, pageSlice, rangeLabel } from "./pager.js";

const api = new ApiClient();
const settings = new SettingsStore();
const theme = new ThemeController(settings);
const toast = createToast("toast");
const mini = mountMiniPlayer({ onError: (m) => toast(m, true), onSpeed: () => syncExportBpm() });

// Flat/tree pagination (page size 0 == "All"); persisted as a UI preference.
const PAGE_SIZE_KEY = "gms_ma.library.pageSize";
const LEGACY_PAGE_SIZE_KEY = "midi_analyzer.library.pageSize";
function loadPageSize() {
  let raw = null, legacy = null;
  try { raw = localStorage.getItem(PAGE_SIZE_KEY); } catch (e) { /* ignore */ }
  try { legacy = localStorage.getItem(LEGACY_PAGE_SIZE_KEY); } catch (e) { /* ignore */ }
  if (raw !== null) return validPageSize(raw);   // absent must not mean 0 == All
  return validPageSize(legacy);
}
function savePageSize() {
  try { localStorage.setItem(PAGE_SIZE_KEY, String(pageSize)); } catch (e) { /* ignore */ }
}
let page = 1;
let pageSize = loadPageSize();

// Sliding window cache: keep +/- WINDOW_RADIUS pages around the current one so
// nearby navigation is instant; fetch a fresh window when leaving it.
const WINDOW_RADIUS = 5;
const PAGE_CACHE = new Map();       // page -> rows[]
function windowRadius(){ return pageSize >= 100 ? 2 : WINDOW_RADIUS; }
function clearPageCache(){ PAGE_CACHE.clear(); }
function cacheWindow(rows, start, end, size){
  PAGE_CACHE.clear();
  for (let p = start; p <= end; p++){
    const off = (p - start) * size;
    PAGE_CACHE.set(p, rows.slice(off, off + size));
  }
}
// Flat paginated view renders from the window cache; "All"/tree use the response.
function pageRows() {
  if (viewMode !== "flat" || pageSize <= 0) return data ? data.patterns : [];
  return PAGE_CACHE.get(page) || [];
}

const FACETS = ["genre","emotion","tempo_class","style","key","mode","energy","family","kind"];
const FACE_GROUP = { genre:"Song · genre", emotion:"Song · emotion", tempo_class:"Song · tempo",
  style:"Pattern · style", key:"Pattern · key", mode:"Pattern · mode", energy:"Pattern · energy",
  family:"Instrument", kind:"Loop kind" };
let data = null, sel = null, selected = null, activeSong = "";
let qText = "", midiAvail = false, clipOk = false;
let renderPick = "original";
let exportBpm = 120, exportBpmEdited = false;
const selFacets = {}; FACETS.forEach(f => selFacets[f] = new Set());
let viewMode = "flat";            // 'flat' | 'tree'
let groupMode = "family";         // 'off' | 'family' | 'program'
let songCache = {};               // song name -> lazily fetched loop rows

function chip(kind, t, manual){ return `<span class="chip ${manual?"manual":""}" title="${esc(kind)}${manual?" · manual":""}">${esc(t)}</span>`; }
function tagChips(tags){
  const order = ["genre","emotion","tempo_class","key","mode","style","energy","chord","harmony"];
  let out = "";
  for (const k of order){ const v = tags && tags[k]; if (v) out += chip(k, v.value, v.source==="manual"); }
  return out || `<span class="dim">no tags</span>`;
}

// ---------------------------------------------------------------- fetch
// JIT: the server only sends the page of loops we ask for.  In tree view it
// sends song summaries only (include_patterns=0); loops are fetched lazily per
// song when a song is expanded or drilled into.
function facetParam(){
  const p = new URLSearchParams();
  if (activeSong) p.set("song", activeSong);
  if (qText) p.set("q", qText);
  for (const f of FACETS){ for (const v of selFacets[f]) p.append(f, v); }
  if (viewMode === "flat"){
    p.set("page", String(page));
    p.set("page_size", String(pageSize));
    if (pageSize > 0) p.set("window", String(windowRadius()));
  } else if (activeSong){
    p.set("page_size", "0");           // this song's loops (bounded)
  } else {
    p.set("include_patterns", "0");    // song headers only
    p.set("page_size", "0");
  }
  return p;
}
let loadTimer = 0;
function loadingHtml(done, total){
  const progress = total ? ` ${done} / ${total}` : "";
  return `<div class="loading"><span class="spinner"></span>Loading library…${progress}</div>`;
}
// Show a spinner with a processed/total counter that steps by 1000 while the
// request is in flight (seeded from the previous response's total).
function beginLoading(total){
  const listEl = $("list");
  let done = 0;
  const paint = () => { if (listEl) listEl.innerHTML = loadingHtml(done, total); };
  paint();
  clearInterval(loadTimer);
  loadTimer = total ? setInterval(() => {
    done += 1000;
    if (done > total) done = total;
    paint();
  }, 120) : 0;
}
function endLoading(){ clearInterval(loadTimer); loadTimer = 0; }
async function fetchLib(resetPage = true){
  if (resetPage) page = 1;
  clearPageCache();
  beginLoading(data ? data.total : 0);
  const qs = facetParam().toString();
  const r = await fetch("/api/library?" + qs);
  if (!r.ok){
    endLoading();
    $("list").innerHTML = '<div class="empty">Library request failed.</div>';
    toast("library request failed", true);
    return;
  }
  data = await r.json();
  endLoading();
  songCache = {};
  if (viewMode === "flat" && pageSize > 0){
    page = data.page || page;
    cacheWindow(data.patterns || [], data.window_start || page,
                data.window_end || page, pageSize);
  } else if (viewMode === "flat"){
    page = 1;
  }
  if (selected){
    const fresh = findPattern(selected.pattern_id);
    if (fresh) selected = fresh;
  }
  renderAll();
}
async function fetchSongPatterns(name){
  try {
    const r = await fetch("/api/library?song=" + encodeURIComponent(name) + "&page_size=0");
    if (!r.ok) return [];
    const j = await r.json();
    return j.patterns || [];
  } catch (e){ return []; }
}
function findPattern(id){
  for (const rows of PAGE_CACHE.values()){
    const hit = rows.find(p => p.pattern_id === id);
    if (hit) return hit;
  }
  const inPage = data && data.patterns.find(p => p.pattern_id === id);
  if (inPage) return inPage;
  for (const name in songCache){
    const hit = songCache[name].find(p => p.pattern_id === id);
    if (hit) return hit;
  }
  return null;
}
async function fetchPorts(){
  const r = await fetch("/api/midi-ports"); const p = await r.json();
  midiAvail = !!p.available; clipOk = !!p.clipboard;
  const dev = $("devSel"); dev.innerHTML = "";
  if (!midiAvail){ const o=document.createElement("option"); o.text="(no MIDI output)"; dev.appendChild(o); dev.disabled=true; }
  else { (p.outputs||[]).forEach((n,i)=>{ const o=document.createElement("option"); o.value=i; o.text=n; dev.appendChild(o); }); }
  renderDetail();
}

// ---------------------------------------------------------------- facets UI
function renderFacets(){
  const el = $("filters");
  let html = "";
  for (const group of FACETS){
    const f = data.facets[group] || [];
    const counts = {};
    for (const it of f) counts[it.value] = it.n;
    for (const v of selFacets[group]) if (!(v in counts)) counts[v] = 0;
    const vals = Object.keys(counts).sort((a,b)=> counts[b]-counts[a] || a.localeCompare(b));
    if (!vals.length) continue;
    html += `<h3>${FACE_GROUP[group]}</h3>`;
    for (const v of vals){
      const on = selFacets[group].has(v) ? "checked" : "";
      html += `<label class="fv"><input type="checkbox" data-f="${group}" data-v="${esc(v)}" ${on}>
        <span>${esc(v)}</span><span class="n">${counts[v]}</span></label>`;
    }
  }
  el.innerHTML = html || `<h3>Library</h3><span class="dim">no facets</span>`;
}
function renderSongSel(){
  const s = $("songSel"); const cur = s.value;
  s.innerHTML = '<option value="">All songs</option>';
  for (const so of (data.songs||[])){ const o=document.createElement("option"); o.value=so.name; o.text=`${so.name}  · ${so.n_patterns} loops`; s.appendChild(o); }
  s.value = activeSong || "";
}
function songsBar(){
  const b = $("bar");
  if (activeSong){
    const so = (data.songs||[]).find(x=>x.name===activeSong);
    b.innerHTML = `<button onclick="setActiveSong('')">&#8592; All songs</button>
      <b>${esc(activeSong)}</b> ${so?`<span class="dim">${so.bpm}bpm · &#9200; ${fmtSec(so.seconds)} · ${so.n_patterns} loop(s) · ${so.n_placements} placement(s)</span>`:""}
      <button id="btnPlaySong" class="sp" title="Play the whole reconstructed song">&#9654; Play full song</button>` +
      (so? `<div style="margin-left:6px">${tagChips(so.stags)}</div>` : "");
    const btn = $("btnPlaySong"); if (btn) btn.onclick = () => playSongFull(activeSong);
  } else {
    b.innerHTML = `<b>All loops</b> <span class="dim">${data.total} pattern(s) · ${data.songs.reduce((a,s)=>a+s.n_placements,0)} placement(s)</span>`;
  }
}

// ---------------------------------------------------------------- list
function instLabel(t){ return trackDisplayName(t.name, t.program, t.is_drums) || t.family; }
function rowHtml(p){
  const t = p.track;
  const occ = p.occ_count;
  const motif = p.kind === "motif";
  const occTxt = motif
    ? (occ ? (occ + "× motif") : "motif")
    : (occ ? (occ + "× placed") : (p.kind === "variant" ? "variant · not placed" : "no placement"));
  const bars = occ ? barList(p.occurrences) : (p.kind === "variant" ? `<span class="dim">orig. bar ${p.bar_index}</span>` : "");
  const tags = [p.ptags.style, p.ptags.mode, p.ptags.energy, p.ptags.key].filter(Boolean)
    .map(v=>`${chip("", v.value, v.source==="manual")}`).join("");
  return `<div class="prow ${selected && selected.pattern_id===p.pattern_id ? "sel":""}" id="p${p.pattern_id}" onclick="pick(${p.pattern_id})">
    <button class="play" onclick="event.stopPropagation(); quickPlay(${p.pattern_id})" title="Audition (original)">&#9654;</button>
    <div class="inst">
      <div class="nm"><span class="dot" style="background:${t.is_drums?"#d8a04a":"hsl("+hue(p.content_id)+",55%,50%)"}"></span>${esc(instLabel(t))}
        <span class="kbadge ${p.kind}">${esc(p.kind)}</span></div>
      <div class="sub">ch${t.channel} ${t.is_drums?"drums":esc(gmLabel(t.program, t.is_drums))} · ${esc(t.family)}</div>
    </div>
    <div class="dim" style="width:70px">${p.length_bars}b · ${fmtSec(p.duration_s)}</div>
    <div class="dim" style="min-width:120px">${occTxt}${bars? " &mdash; "+bars : ""}</div>
    <div class="tagrow" style="flex:1">${tags}</div>
    <div class="dim" style="font-size:11px">conf ${(p.confidence*100).toFixed(0)}%</div>
  </div>`;
}
function barList(ocs){
  const bars = ocs.slice(0,14).map(o=>o.bar_index+1);
  const more = ocs.length > 14 ? ` +${ocs.length-14}` : "";
  return `<span class="dim">bars ${bars.join(", ")}${more}</span>`;
}
function renderFlatList(){
  const el = $("list");
  if (!data){ el.innerHTML = '<div class="empty">No data.</div>'; return; }
  if (!data.patterns.length){ el.innerHTML = '<div class="empty">No loops match the current filters.</div>'; return; }
  const bySong = new Map();
  const rowsPage = pageRows();
  for (const p of rowsPage){ if (!bySong.has(p.song.name)) bySong.set(p.song.name, []); bySong.get(p.song.name).push(p); }
  let html = "";
  if (activeSong){
    const rows = bySong.get(activeSong) || [];
    html = rows.map(rowHtml).join("");
  } else {
    for (const so of data.songs){
      const rows = bySong.get(so.name) || [];
      if (!rows.length) continue;
      html += `<div class="songhead" data-song="${esc(so.name)}">
        <button class="sp" data-play-song="${esc(so.name)}" title="Play the whole song">&#9654;</button>
        <span class="t">${esc(so.name)}</span>
        <span class="dim">${so.bpm}bpm</span>
        <div>${tagChips(so.stags)}</div>
        <span class="btn dim">${so.n_patterns} loop(s) &mdash; view sub-patterns &#8594;</span>
      </div>`;
      html += rows.map(rowHtml).join("");
    }
  }
  el.innerHTML = html;
}

// ============================================================= tree view
// Two expandable organisations:
//  * All-songs:  Song ▸ instrument group ▸ track ▸ loops (lazy per song)
//  * drill-down: instrument group ▸ track ▸ loops
// Loops of one track are nested by *bar containment*: the longest covering
// loop sits on top and expanding reveals the shorter loops/phrases placed
// entirely inside the bars it covers.  Only loops of the same song + track
// are ever compared.
function renderList(){ if (viewMode === "tree") renderTreeList(); else renderFlatList(); }
function renderTreeList(){
  const el = $("list");
  if (!data){ el.innerHTML = ""; return; }
  if (!data.total){ el.innerHTML = '<div class="empty">No loops match the current filters.</div>'; return; }
  if (activeSong){
    // drill-down: page over the song's tracks (its loops came with the request)
    const groups = groupTracks(pageSlice(trackEntries(data.patterns), page, pageSize));
    el.innerHTML = groups.map(groupBlockHtml).join("") ||
      '<div class="empty">No loops for this song.</div>';
    return;
  }
  // all songs: page over the song summaries; each song's loops are fetched
  // lazily (JIT) when it is expanded.
  const songs = pageSlice(data.songs || [], page, pageSize);
  let html = "";
  for (const so of songs){
    html += `<div class="tsong">
      <div class="songhead" data-tog="${esc(so.name)}">
        <button class="sp" data-play-song="${esc(so.name)}" title="Play the whole song">&#9654;</button>
        <span class="tcaret">▸</span>
        <span class="t">${esc(so.name)}</span>
        <span class="dim">${so.bpm}bpm · &#9200; ${fmtSec(so.seconds)} · ${so.n_patterns} loop(s)</span>
        <div>${tagChips(so.stags)}</div>
        <button class="btnlink" data-drill="${esc(so.name)}">open &#8594;</button>
      </div>
      <div class="tcontent hide"></div>
    </div>`;
  }
  el.innerHTML = html;
}
async function toggleSongExpand(name, togEl){
  const host = togEl.closest(".tsong"); if (!host) return;
  const content = host.querySelector(".tcontent"); if (!content) return;
  if (!content.dataset.built){
    content.innerHTML = loadingHtml(0, 0);
    const rows = songCache[name] || (songCache[name] = await fetchSongPatterns(name));
    content.innerHTML = songTreeHtml(name, rows);
    content.dataset.built = "1";
  }
  const hide = content.classList.toggle("hide");
  const caret = host.querySelector(".tcaret"); if (caret) caret.textContent = hide ? "▸" : "▾";
}
function toggleCaret(ev){
  // Works whether the click lands on the caret button (which bubbles up) or
  // directly on the group/track/loop header row.
  const head = ev.currentTarget.closest(".trow, .thead, .tghead");
  if (!head) return;
  const kids = head.nextElementSibling;
  if (!kids || !kids.classList || !kids.classList.contains("kids")) return;
  const hide = kids.classList.toggle("hide");
  const caret = head.querySelector(".caret");
  if (caret) caret.textContent = hide ? "▸" : "▾";
}
// Expand every collapsed ancestor of a row so it becomes visible (used when a
// pattern is played from the tree view).
function expandTo(pid){
  let el = document.getElementById("trow-" + pid);
  const root = $("list");
  while (el && el !== root){
    if (el.classList && el.classList.contains("kids") && el.classList.contains("hide")){
      el.classList.remove("hide");
      const head = el.previousElementSibling;
      const caret = head && head.querySelector(".caret");
      if (caret) caret.textContent = "▾";
    }
    el = el.parentElement;
  }
}
// -- instrument similarity grouping (configurable) -------------------------
function groupKeyOf(tr){
  return groupMode === "program" ? gmLabel(tr.program, tr.is_drums) : (tr.family || "?");
}
// One entry per track (all of that track's loop rows).
function trackEntries(rows){
  const tracks = [];
  const seen = new Map();
  for (const p of rows){
    if (!seen.has(p.track.id)){
      seen.set(p.track.id, {tr: p.track, items: []});
      tracks.push(seen.get(p.track.id));
    }
    seen.get(p.track.id).items.push(p);
  }
  tracks.sort((a, b) => a.tr.track_index - b.tr.track_index);
  return tracks;
}
function groupTracks(tracks){
  if (groupMode === "off"){
    return tracks.map(t => ({label: null, tracks: [t]}));
  }
  const groups = [], idx = new Map();
  for (const t of tracks){
    const k = groupKeyOf(t.tr);
    if (!idx.has(k)){ idx.set(k, groups.length); groups.push({label: k, tracks: []}); }
    groups[idx.get(k)].tracks.push(t);
  }
  return groups;
}
function buildInstrumentGroups(rows){ return groupTracks(trackEntries(rows)); }
// One collapsible instrument group: header (caret + label) then its tracks.
function groupBlockHtml(g){
  if (g.label === null || groupMode === "off")
    return g.tracks.map(trackBlockHtml).join("");
  return `<div class="tg">
    <div class="tghead" onclick="toggleCaret(event)">
      <button class="caret" type="button">&#9656;</button>
      ${esc(g.label)} <span class="gcount">${g.tracks.length} track(s)</span>
    </div>
    <div class="kids hide">${g.tracks.map(trackBlockHtml).join("")}</div>
  </div>`;
}
// -- building-block forest (longest covering loops on top) ------------------
// A shorter loop B is a *building block* of a longer loop A (same track +
// song) when B has at least one placement fully inside one of A's windows,
// starting on an aligned bar boundary — this is what the period/repeat ladder
// produces (e.g. the 1-bar cell tiles fill the 4-bar groove).
function forestHtml(items, childMap, depth){
  let h = "";
  for (const p of items){
    const kids = childMap.get(p.pattern_id) || [];
    h += `<div class="trowwrap">`;
    h += `<div class="trow ${selected && selected.pattern_id === p.pattern_id ? "sel" : ""}"
        id="trow-${p.pattern_id}" style="padding-left:${8 + depth * 18}px" onclick="pick(${p.pattern_id})">
        ${kids.length
          ? `<button class="caret" onclick="event.stopPropagation();toggleCaret(event)">▸</button>`
          : `<span class="caret blank">&#183;</span>`}
        <button class="play" onclick="event.stopPropagation();quickPlay(${p.pattern_id})">&#9654;</button>
        <span class="len">${p.length_bars}b</span>
        <span class="dim" style="font-size:11px;font-variant-numeric:tabular-nums">${fmtSec(p.duration_s)}</span>
        <span class="kbadge ${p.kind}">${esc(p.kind)}</span>
        <span class="dim" style="font-size:11px">${p.occ_count ? p.occ_count + "×" : (p.kind === "variant" ? "variant" : "—")} · ${p.notes_count} n</span>
        <div class="tagrow" style="flex:1">${forestMiniChips(p)}</div>
      </div>`;
    if (kids.length){
      h += `<div class="kids hide">` + forestHtml(kids, childMap, depth + 1) + `</div>`;
    }
    h += `</div>`;
  }
  return h;
}
function forestMiniChips(p){
  const tag = p.ptags || {};
  return [tag.style, tag.mode, tag.energy, tag.key].filter(Boolean)
    .map(v => chip("", v.value, v.source === "manual")).join("");
}
function motifMiniRow(p){
  return `<div class="trow ${selected && selected.pattern_id === p.pattern_id ? "sel" : ""}"
      id="trow-${p.pattern_id}" style="padding-left:36px" onclick="pick(${p.pattern_id})">
    <span class="caret blank">&#183;</span>
    <button class="play" onclick="event.stopPropagation();quickPlay(${p.pattern_id})">&#9654;</button>
    <span class="len">${p.length_bars}b</span>
    <span class="dim" style="font-size:11px;font-variant-numeric:tabular-nums">${fmtSec(p.duration_s)}</span>
    <span class="kbadge motif">motif</span>
    <span class="dim" style="font-size:11px">${p.occ_count ? p.occ_count + "×" : "—"} · ${p.notes_count} n</span>
    <div class="tagrow" style="flex:1">${forestMiniChips(p)}</div>
  </div>`;
}
function trackBlockHtml(t){
  const tr = t.tr;
  // bar-containment tree is for bar-aligned cover/variant loops only; the
  // grid-free motif families are shown below the tree as their own group.
  const loops = t.items.filter(p => p.kind !== "motif");
  const motifs = t.items.filter(p => p.kind === "motif");
  const {childMap, roots} = containmentForest(loops);
  const label = instLabel(tr);
  const songName = (t.items[0] && t.items[0].song && t.items[0].song.name) || activeSong || "";
  let kids = forestHtml(roots, childMap, 0);
  if (motifs.length){
    kids += `<div class="tmotifs-label">motifs (grid-free)</div>`;
    kids += `<div class="tmotifs">` + motifs.map(motifMiniRow).join("") + `</div>`;
  }
  return `<div class="ttrack">
    <div class="thead" onclick="toggleCaret(event)">
      <button class="caret" type="button">&#9656;</button>
      <button class="play" data-playtrack="${tr.id}" data-song="${esc(songName)}"
              data-label="${esc(label)}" onclick="event.stopPropagation();playTrack(this)"
              title="Play this channel across the song">&#9654;</button>
      <span class="dot" style="background:${tr.is_drums ? "#d8a04a" : "hsl(" + hue(label) + ",55%,50%)"}"></span>
      <span>${esc(label)}</span>
      <span class="dim">ch${tr.channel}${tr.is_drums ? " · drums" : ""}${" · " + esc(gmLabel(tr.program, tr.is_drums))}</span>
      <span class="dim">${loops.length} loop(s)${motifs.length ? " · " + motifs.length + " motif(s)" : ""}${tr.period_bars ? " · base " + tr.period_bars + "b" : ""}</span>
      ${tr.span_s > 0 ? `<span class="dim" style="font-variant-numeric:tabular-nums">&#9200; ${fmtSec(tr.span_s)}</span>` : ""}
    </div>
    <div class="kids hide">${kids}</div>
  </div>`;
}
function songTreeHtml(name, rows){
  rows = rows || trackRowsOf(name);
  if (!rows.length) return '<div class="empty">No loops for this song.</div>';
  return buildInstrumentGroups(rows).map(groupBlockHtml).join("");
}
function trackRowsOf(name){ return data.patterns.filter(p => p.song.name === name); }
// Number of top-level entries in the current view: loops (flat), tracks
// (tree drill-down) or songs (tree, all songs).
function entryTotal(){
  if (!data) return 0;
  if (viewMode === "flat") return data.total || 0;
  if (activeSong) return trackEntries(data.patterns).length;
  return (data.songs || []).length;
}
function entryUnit(){
  if (viewMode === "flat") return "loops";
  return activeSong ? "tracks" : "songs";
}

// ---------------------------------------------------------------- select & play
function pick(id){
  const p = findPattern(id);
  if (!p) return;
  selected = p;
  exportBpmEdited = false;
  if (renderPick === "piano" && !p.stems.includes("piano")) renderPick = "original";
  if (renderPick === "clap" && !p.stems.includes("clap")) renderPick = "original";
  if (viewMode === "tree"){ paintSel(); renderDetail(); return; }  // keep tree expanded
  renderList(); renderDetail();
}
function paintSel(){
  document.querySelectorAll("#list .trow.sel").forEach(el => el.classList.remove("sel"));
  if (selected){
    const el = document.getElementById("trow-" + selected.pattern_id);
    if (el) el.classList.add("sel");
  }
}
function currentRender(){ return renderPick && renderPick !== "original" ? renderPick : "original"; }
function setActiveSong(name){ activeSong = name || ""; const s=$("songSel"); s.value=activeSong||""; selected=null; fetchLib(); }
function clearAll(){
  activeSong=""; qText=""; $("q").value=""; FACETS.forEach(f=>selFacets[f].clear());
  selected=null; fetchLib();
}
function quickPlay(id){ doPlay(id, "original"); }
function doPlay(id, render){   // (re)start the pattern from the top
  if (!midiAvail){ toast("No MIDI output device available", true); return; }
  const p = findPattern(id);
  if (!p){ toast("pattern not found", true); return; }
  if (viewMode === "tree") expandTo(id);   // reveal the played row
  const r = render && render !== "original" ? render : "original";
  mini.playPattern({
    pattern_id: id, render: r,
    label: `${p.song.name} — ${instLabel(p.track)} · ${r}`,
    total_s: p.duration_s,
  });
}
function playSongFull(name){   // play the whole reconstructed song
  if (!midiAvail){ toast("No MIDI output device available", true); return; }
  const s = (data && data.songs || []).find(x => x.name === name);
  mini.playSong({ song: name, label: name + " — full song", total_s: s && s.seconds });
}
function playTrack(btn){       // play one channel's full line across the song
  if (!midiAvail){ toast("No MIDI output device available", true); return; }
  const song = btn.dataset.song;
  const trackId = Number(btn.dataset.playtrack);
  if (!song || !trackId) return;
  mini.playSong({ song, label: `${song} — ${btn.dataset.label || "channel"}`,
                  tracks: [trackId] });
}
function stopAll(){ return mini.stop(); }
function mbToggle(){ return mini.toggle(); }
function setRenderPick(mode){ renderPick = mode; renderDetail(); }
function renderPickBtn(pid){
  const p = findPattern(pid);
  const stems = p ? p.stems : [];
  const seg = b => `<div class="seg">
      <button class="${renderPick==="original"?"on":""}" onclick="setRenderPick('original')" title="Original instrument/channel">Orig</button>
      <button class="${renderPick==="piano"?"on":""}" ${stems.includes("piano")?"":"disabled"} onclick="setRenderPick('piano')" title="Rhythm skeleton (piano)">Piano</button>
      <button class="${renderPick==="clap"?"on":""}" ${stems.includes("clap")?"":"disabled"} onclick="setRenderPick('clap')" title="Rhythm skeleton (clap)">Clap</button></div>`;
  return seg();
}

// ---------------------------------------------------------------- detail
function renderDetail(){
  const el = $("detail");
  const noClip = clipOk ? "" : "disabled";
  if (!selected){
    el.innerHTML = `<span class="dim">Pick a pattern to audition it and see where it comes from.</span>`;
    return;
  }
  const p = selected, t = p.track, s = p.song;
  const render = currentRender();
  let html = "";
  html += `<h3>Play</h3>`;
  html += `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${renderPickBtn(p.pattern_id)}
    <button class="bigbtn" onclick="doPlay(${p.pattern_id},'${render}')">&#9654; Play ${esc(render)}</button></div>`;
  html += `<h3>Source</h3>`;
  html += `<div class="row"><b>${esc(s.name)}</b> &nbsp;<a target="_blank" href="/?song=${encodeURIComponent(s.name)}">open timeline &#8599;</a></div>`;
  html += `<div class="row"><span class="dim">${s.bpm}bpm</span></div>`;
  html += tagChips(p.stags);
  html += `<h3>Instrument</h3>`;
  html += `<div class="row"><b>${esc(instLabel(t))}</b></div>`;
  html += `<div class="row">channel ${t.channel}${t.is_drums?" (GM drums)":""} &middot; ${esc(gmLabel(t.program, t.is_drums))} &middot; ${esc(t.family)}</div>`;
  html += t.engine ? `<div class="row"><span class="dim">detected with ${esc(t.engine)} lens ${esc(t.lens||"pitch")}${t.period_bars?" · period "+t.period_bars+"b":""}</span></div>` : "";
  html += `<h3>Loop</h3>`;
  html += `<div class="row"><span class="mono">${esc(p.content_id)}</span></div>`;
  html += `<div class="row"><b>${p.kind}</b> &middot; ${p.length_bars} bar(s) &middot; ${p.length_ticks} ticks &middot; ~${fmtSec(p.duration_s)}s</div>`;
  html += `<div class="row">${p.notes_count} note(s) &middot; confidence ${(p.confidence*100).toFixed(0)}% &middot; ${p.occ_count} occurrence(s)</div>`;
  html += p.occ_count ? `<div class="row">placed at: ${barList(p.occurrences)}</div>` : `<div class="row"><span class="dim">variant — captured from bar ${p.bar_index} of ${esc(s.name)}</span></div>`;
  html += `<h3>Pattern tags</h3>` + tagChips(p.ptags);
  html += `<h3>Export</h3>`;
  html += `<div style="margin:2px 0 6px">${render === "original" ? "Active render: <b>original</b>" : "Active render: <b>"+esc(render)+"</b> skeleton"}</div>`;
  html += `<div class="row" style="margin:4px 0">Export BPM
    <input id="lib-exp-bpm" type="number" min="20" max="400" step="1" value="${libExportBpm()}" style="width:72px">
    <span class="dim">· speed ${mini.state().speed.toFixed(2)}×</span></div>`;
  html += `<button onclick="expDownload()">&#8595; Download MIDI</button> `;
  html += `<button ${noClip} title="Copy this MIDI file to the clipboard (paste into Explorer/DAW)" onclick="expCopy()">&#128203; Copy MIDI</button> `;
  html += `<button ${noClip} title="Rewrite to channel 1 and copy the ch1 file to the clipboard" onclick="expCopyCh1()">&#128203; Copy DAW ch1</button><br>`;
  html += `<button style="margin-top:6px" onclick="expDaw()" title="Rewrite to channel 1 (DAW ch.1), copy to clipboard and download">&#127928; DAW ch1 export</button>`;
  html += `<div class="row" style="margin-top:5px"><span class="dim">DAW ch1 = every event remapped to one channel (program &amp; pitch bend kept).</span></div>`;
  el.innerHTML = html;
}
function refreshView(){
  // flat pages live on the server (JIT); tree pages are client-side
  if (viewMode === "flat") fetchLib(false);
  else renderAll();
}
function goPage(p){
  page = clampPage(p, pageCount(entryTotal(), pageSize));
  if (viewMode === "flat" && pageSize > 0 && PAGE_CACHE.has(page)){
    renderAll();               // instant: served from the window cache
    return;
  }
  refreshView();
}
function renderPager(){
  const el = $("pager");
  if (!el) return;
  if (!data || !entryTotal()){
    el.classList.add("hide");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hide");
  const total = entryTotal();
  const pages = pageCount(total, pageSize);
  page = clampPage(page, pages);
  const opts = PAGE_SIZES.map(s =>
    `<option value="${s}"${s === pageSize ? " selected" : ""}>${s === 0 ? "All" : s}</option>`).join("");
  el.innerHTML =
    `<label>Rows per page <select id="pgSize">${opts}</select></label>` +
    `<button id="pgPrev"${page <= 1 ? " disabled" : ""}>&#8249; Prev</button>` +
    `<span id="pgInfo">Page ${page} / ${pages} &middot; ${rangeLabel(page, pageSize, total)} ${entryUnit()}</span>` +
    `<button id="pgNext"${page >= pages ? " disabled" : ""}>Next &#8250;</button>` +
    `<span class="pg-goto">Go to page <input id="pgGo" type="number" min="1" max="${pages}" value="${page}">` +
    `<button id="pgGoBtn"${pages <= 1 ? " disabled" : ""}>Go</button></span>`;

  const sizeSel = $("pgSize");
  if (sizeSel) sizeSel.onchange = () => {
    pageSize = Number(sizeSel.value);
    savePageSize();
    clearPageCache();
    page = 1;
    refreshView();
  };
  const prev = $("pgPrev");
  if (prev) prev.onclick = () => goPage(page - 1);
  const next = $("pgNext");
  if (next) next.onclick = () => goPage(page + 1);
  const go = $("pgGo");
  const doGo = () => goPage(go ? go.value : page);
  const goBtn = $("pgGoBtn");
  if (goBtn) goBtn.onclick = doGo;
  if (go) go.addEventListener("keydown", (e) => {
    if (e.key === "Enter"){ e.preventDefault(); doGo(); }
  });
}
function renderAll(){
  renderFacets(); renderSongSel(); songsBar(); renderList(); renderDetail(); renderPager();
  $("total").textContent = data ? data.total + " loops" : "";
}
function libExportBpm(){
  const base = (selected && selected.song && selected.song.bpm) ? selected.song.bpm : 120;
  return exportBpmEdited ? exportBpm : Math.max(1, Math.round(base * mini.state().speed));
}
function syncExportBpm(){
  const el = $("lib-exp-bpm");
  if (el && !exportBpmEdited) el.value = String(libExportBpm());
}
function exportOpts(){
  const p = selected;
  if (!p) return null;
  const r = currentRender();
  return { pattern_id: p.pattern_id, render: r === "original" ? null : r,
           bpm: Number(libExportBpm()) || null, clipboardOk: clipOk, toast };
}
function expDownload(){ const o = exportOpts(); if (o) downloadMidi(o); }
async function expCopy(){ const o = exportOpts(); if (o) await copyMidi(o); }
async function expCopyCh1(){ const o = exportOpts(); if (o) await copyDawCh1(o); }
async function expDaw(){ const o = exportOpts(); if (o) await dawExport(o); }

// ---------------------------------------------------------------- events
$("filters").addEventListener("change", e=>{
  const cb = e.target.closest("input[data-f]"); if (!cb) return;
  const f = cb.dataset.f, v = cb.dataset.v;
  if (cb.checked) selFacets[f].add(v); else selFacets[f].delete(v);
  selected = null; fetchLib();
});
$("list").addEventListener("click", e=>{
  const play = e.target.closest("[data-play-song]");
  if (play){ e.stopPropagation(); playSongFull(play.dataset.playSong); return; }
  const drill = e.target.closest("[data-drill]");
  if (drill){ e.stopPropagation(); setActiveSong(drill.dataset.drill); return; }
  const tog = e.target.closest("[data-tog]");
  if (tog){ e.stopPropagation(); toggleSongExpand(tog.dataset.tog, tog); return; }
  const head = e.target.closest("[data-song]");
  if (head){ setActiveSong(head.dataset.song); }
});
$("btnView").onclick = ()=>{
  viewMode = viewMode === "tree" ? "flat" : "tree";
  $("btnView").textContent = viewMode === "tree" ? "View: Flat" : "View: Tree";
  $("grpSel").style.display = viewMode === "tree" ? "" : "none";
  selected = null; fetchLib();
};
$("detail").addEventListener("input", e=>{
  if (e.target && e.target.id === "lib-exp-bpm"){
    exportBpmEdited = true;
    exportBpm = Math.max(1, Number(e.target.value) || 1);
  }
});
$("grpSel").onchange = ()=>{
  groupMode = $("grpSel").value;
  if (viewMode === "tree"){ selected = null; fetchLib(); }
};
let qTimer = 0;
$("q").addEventListener("input", e=>{
  clearTimeout(qTimer); qTimer = setTimeout(()=>{ qText = e.target.value.trim(); selected=null; fetchLib(); }, 220);
});
$("songSel").addEventListener("change", e=>{ setActiveSong(e.target.value); });
$("btnClear").onclick = clearAll;
$("btnStop").onclick = stopAll;
window.addEventListener("keydown", e=>{ if (e.key === "Escape") stopAll(); });
$("btnView").textContent = viewMode === "tree" ? "View: Flat" : "View: Tree";
$("grpSel").style.display = viewMode === "tree" ? "" : "none";
mountSettingsPanel({
  settings, theme, container: $("top"),
  onChange: (key) => { if (key === "theme") theme.apply(); },
});
mountAboutPanel({ container: $("top"), isLibrary: true });
theme.apply();
theme.bindMedia(() => theme.apply());
fetchPorts(); fetchLib();

// Inline HTML handlers (onclick="…") resolve against the global scope, which
// module bindings are not part of — expose the handful they call.
Object.assign(globalThis, {
  pick, quickPlay, toggleCaret, setActiveSong, renderDetail, doPlay, playTrack,
  expDownload, expCopy, expCopyCh1, expDaw, setRenderPick, stopAll, mbToggle, playSongFull,
});
