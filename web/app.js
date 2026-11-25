import WaveSurfer from './vendor/wavesurfer.esm.js';
import RegionsPlugin from './vendor/plugins/regions.esm.js';
import { DualPlayer } from './player.js';
import { LANGS, applyStatic, getLang, setLang, t } from './i18n.js';

// ---------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------

const LANE_ORDER = [
  'tremor', 'phrase_end', 'breathiness', 'sob_formant',
  'sob_pulse', 'voice_break', 'articulation_blur', 'sob_inhale',
  'glottal_catch', 'f2_raise', 'nasal', 'sfx',
];
const COLORS = {
  tremor: '#e8a33d', phrase_end: '#e06b6b', breathiness: '#5fb3d9', sob_formant: '#a58cf0',
  sob_pulse: '#f07fb0', voice_break: '#ffd166', articulation_blur: '#7fd99b', sob_inhale: '#9ad0ff',
  glottal_catch: '#ff9f6b', f2_raise: '#c9e265', nasal: '#d2a8ff', sfx: '#b0b8c8',
};
const DEFAULT_STRENGTH = 70;
const MAX_PX_PER_SEC = 1500;
const LOOP_PAD = 0.15;
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
function newId() { return 'r' + Math.random().toString(36).slice(2, 10); }
function fmtTime(t) {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(3).padStart(6, '0')}`;
}
function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// ---------------------------------------------------------------------------
// 状態
// ---------------------------------------------------------------------------

const S = {
  effects: [],
  effectMap: {},
  session: null,
  project: null,
  player: null,
  mode: 'B',
  main: null,
  lanes: {},      // type -> { ws, regions, track, label, spec }
  views: {},      // region id -> { model, region, type }
  selected: null,
  zoomPx: 0,
  scroll: 0,
  syncing: false,
  dirty: false,
  sync: { timer: null, inflight: false, pending: false },
  f0: null,       // { fp, midi: Float32Array, lo, hi }
  loopOn: false,
  materials: {},  // choices_source -> [ファイル名]
  warnIds: new Set(),
};

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch (e) { /* noop */ }
    throw new Error(m);
  }
  return r;
}
const getJSON = async (p) => (await api(p)).json();
const postJSON = async (p, body) => (await api(p, { method: 'POST', body: JSON.stringify(body ?? {}) })).json();

// ---------------------------------------------------------------------------
// 表示の小物
// ---------------------------------------------------------------------------

function setStatus(text) { $('status').textContent = text; }
function setRenderStatus(text, busy = false) {
  const el = $('render-status');
  el.textContent = text;
  el.classList.toggle('busy', busy);
}
function message(text, kind = 'info', timeout = 8000) {
  const el = document.createElement('div');
  el.className = `msg ${kind}`;
  el.textContent = text;
  $('messages').prepend(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
}
function busy(show, text = '', progress = null) {
  $('overlay-busy').hidden = !show;
  if (text) $('busy-text').textContent = text;
  if (progress !== null) $('busy-bar').style.width = `${Math.round(progress * 100)}%`;
}
function markDirty(on = true) {
  S.dirty = on;
  const name = S.session ? S.session.name : t('noFile');
  const proj = S.session && S.session.project_path ? ` — ${S.session.project_path.split(/[\\/]/).pop()}` : '';
  $('file-name').textContent = `${name}${proj}${on ? ' *' : ''}`;
}
function enableControls(on) {
  for (const id of ['btn-save', 'btn-save-as', 'btn-export', 'btn-play', 'btn-a', 'btn-b', 'global-amount', 'zoom']) {
    $(id).disabled = !on;
  }
  updateLoopButton();
}

// ---------------------------------------------------------------------------
// 開く
// ---------------------------------------------------------------------------

// 画面内の確認ダイアログ(ブラウザ標準の confirm は環境によって無効にされるため使わない)
function choose(text, buttons) {
  return new Promise((resolve) => {
    $('modal-text').textContent = text;
    const box = $('modal-buttons');
    box.innerHTML = '';
    const close = (v) => {
      $('modal').hidden = true;
      document.removeEventListener('keydown', onKey, true);
      resolve(v);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close('cancel'); }
    };
    for (const b of buttons) {
      const el = document.createElement('button');
      el.textContent = b.label;
      if (b.primary) el.className = 'primary';
      el.addEventListener('click', () => close(b.value));
      box.appendChild(el);
    }
    document.addEventListener('keydown', onKey, true);
    $('modal').hidden = false;
  });
}

async function openPath(path) {
  if (!path) return;
  if (S.dirty) {
    const ans = await choose(t('dirty.text'), [
      { label: t('dirty.cancel'), value: 'cancel' },
      { label: t('dirty.discard'), value: 'discard' },
      { label: t('dirty.save'), value: 'save', primary: true },
    ]);
    if (ans === 'cancel') return;
    if (ans === 'save' && !(await saveProject(false))) return;
  }
  stopPlayback();
  try {
    const res = await postJSON('/api/open', { path });
    for (const m of res.messages || []) message(m);
    await waitReady();
    await loadSession();
  } catch (e) {
    busy(false);
    message(t('open.failed', { e: e.message }), 'error', 0);
  }
}

async function waitReady() {
  busy(true, t('busy.analyzing'), 0);
  for (;;) {
    const st = await getJSON('/api/status');
    setStatus(st.message);
    busy(true, st.message, st.progress);
    if (st.state === 'ready') break;
    if (st.state === 'error') { busy(false); throw new Error(st.message); }
    if (st.state === 'empty') { busy(false); return false; }
    await sleep(250);
  }
  busy(false);
  return true;
}

async function loadSession() {
  busy(true, t('busy.loading'), 1);
  const info = await getJSON('/api/session');
  S.session = info;
  S.project = info.project;
  S.project.regions = S.project.regions || [];
  for (const r of S.project.regions) if (!r.id) r.id = newId();

  if (S.player) { S.player.stop(); S.player.ctx.close(); }
  S.player = new DualPlayer(info.fs);
  S.player.onEnded = () => updatePlayButton();
  const [ab, bb] = await Promise.all([
    fetch('/api/audio/original').then((r) => r.arrayBuffer()),
    fetch('/api/audio/processed').then((r) => r.arrayBuffer()),
  ]);
  const bufA = await S.player.decode(ab);
  const bufB = await S.player.decode(bb);
  if (bufA.length !== info.n_samples || bufB.length !== info.n_samples) {
    message(t('length.mismatch', { a: bufA.length, b: info.n_samples }), 'warn', 0);
  }
  S.player.setBuffers(bufA, bufB);
  S.player.setMode(S.mode);

  $('global-amount').value = Math.round(S.project.global_cry_amount ?? 60);
  $('global-amount-val').textContent = $('global-amount').value;
  $('zoom').value = 0;
  S.zoomPx = 0;
  S.scroll = 0;
  S.loopOn = false;
  S.player.loop = null;
  setRenderStatus('');

  await loadMaterials();
  prepareF0(info);
  buildTimeline(info, bufA);
  select(null);
  enableControls(true);
  markDirty(false);
  for (const m of info.messages || []) message(m);
  setStatus(sessionStatus());
  busy(false);
}

async function loadMaterials() {
  const kinds = new Set();
  for (const e of S.effects) for (const p of e.params) if (p.choices_source) kinds.add(p.choices_source);
  for (const k of kinds) {
    try {
      const res = await getJSON(`/api/materials/${k}`);
      S.materials[k] = res.files;
      S.materials[`${k}:dir`] = res.dir;
    } catch (e) {
      S.materials[k] = [];
    }
  }
}

// ---------------------------------------------------------------------------
// タイムライン(波形 + レーン)
// ---------------------------------------------------------------------------

function monoOf(buf) {
  if (buf.numberOfChannels === 1) return buf.getChannelData(0);
  const n = buf.length;
  const out = new Float32Array(n);
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const d = buf.getChannelData(c);
    for (let i = 0; i < n; i++) out[i] += d[i];
  }
  const k = 1 / buf.numberOfChannels;
  for (let i = 0; i < n; i++) out[i] *= k;
  return out;
}

function lowResPeaks(data, fs, perSec = 60) {
  const block = Math.max(1, Math.floor(fs / perSec));
  const n = Math.ceil(data.length / block);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let m = 0;
    const end = Math.min(data.length, (i + 1) * block);
    for (let j = i * block; j < end; j++) { const v = Math.abs(data[j]); if (v > m) m = v; }
    out[i] = m;
  }
  return out;
}

function destroyTimeline() {
  if (S.main) { S.main.destroy(); S.main = null; }
  for (const lane of Object.values(S.lanes)) {
    lane.ws.destroy();
    lane.track.remove();
    lane.label.remove();
  }
  S.lanes = {};
  S.views = {};
}

function allWs() {
  return [S.main, ...Object.values(S.lanes).map((l) => l.ws)].filter(Boolean);
}

function buildTimeline(info, bufA) {
  destroyTimeline();
  const duration = info.duration;
  const mono = monoOf(bufA);

  S.main = WaveSurfer.create({
    container: '#wave',
    height: $('main-track').clientHeight - 1,
    waveColor: '#41607e',
    progressColor: '#41607e',
    cursorWidth: 0,
    peaks: [mono],
    duration,
    normalize: true,
    hideScrollbar: true,
    autoScroll: false,
    autoCenter: false,
    interact: false,
    fillParent: true,
  });
  S.main.on('scroll', (_a, _b, left) => syncScroll(left, S.main));
  S.main.on('redrawcomplete', () => onLayout());

  const low = lowResPeaks(mono, info.fs);
  const playhead = $('playhead');
  for (const type of LANE_ORDER) {
    const spec = S.effectMap[type];
    if (!spec || !spec.implemented) continue;
    const color = COLORS[type] || '#999';

    const label = document.createElement('div');
    label.className = 'label';
    fillLaneLabel(label, spec, type);
    $('labels').appendChild(label);

    const track = document.createElement('div');
    track.className = `track lane ${spec.kind}`;
    $('tracks').insertBefore(track, playhead);

    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: track,
      height: track.clientHeight - 1,
      waveColor: 'rgba(140,150,170,0.16)',
      progressColor: 'rgba(140,150,170,0.16)',
      cursorWidth: 0,
      peaks: [low],
      duration,
      normalize: true,
      hideScrollbar: true,
      autoScroll: false,
      autoCenter: false,
      interact: false,
      fillParent: true,
      plugins: [regions],
    });
    const lane = { ws, regions, track, label, spec, type };
    S.lanes[type] = lane;

    if (spec.kind === 'marker') {
      track.addEventListener('dblclick', (e) => {
        if (e.target !== track && !track.contains(e.target)) return;
        addMarkerAt(type, timeAtClientX(e.clientX));
      });
    } else {
      regions.enableDragSelection({ color: rgba(color, 0.35) });
    }
    regions.on('region-created', (r) => onRegionCreated(type, r));
    regions.on('region-updated', (r) => onRegionUpdated(r));
    regions.on('region-clicked', (r, e) => { e.stopPropagation(); select(r.id); });
    regions.on('region-double-clicked', (r, e) => { e.stopPropagation(); select(r.id); loopSelected(true); });
    ws.on('scroll', (_a, _b, left) => syncScroll(left, ws));
  }

  // 保存されていた区間を置く(波形の準備ができてから置かないと長さ 0 として丸められる)
  for (const model of S.project.regions) {
    const lane = S.lanes[model.type];
    if (!lane) continue; // 未実装の処理はデータだけ保持
    S.views[model.id] = { model, region: null, type: model.type };
  }
  for (const lane of Object.values(S.lanes)) {
    lane.ws.once('ready', () => {
      for (const model of S.project.regions) {
        if (model.type !== lane.type || !S.views[model.id]) continue;
        const isMarker = lane.spec.kind === 'marker';
        lane.regions.addRegion({
          id: model.id,
          start: isMarker ? model.time : model.start,
          end: isMarker ? undefined : model.end,
          color: regionColor(model),
          drag: true,
          resize: !isMarker,
        });
      }
    });
  }

  requestAnimationFrame(onLayout);
}

function viewWidth() { return $('main-track').clientWidth; }

function pxPerSec() {
  if (!S.session) return 1;
  const d = S.session.duration;
  const w = viewWidth();
  const full = Math.ceil(d * S.zoomPx);
  return full > w ? full / d : w / d;
}

function timeAtClientX(clientX) {
  const rect = $('main-track').getBoundingClientRect();
  return clamp((clientX - rect.left + S.scroll) / pxPerSec(), 0, S.session.duration);
}

function onLayout() {
  if (!S.session) return;
  $('scroller-inner').style.width = `${Math.ceil(S.session.duration * pxPerSec())}px`;
  const sc = $('scroller');
  if (Math.abs(sc.scrollLeft - S.scroll) > 0.5) sc.scrollLeft = S.scroll;
  drawOverlay();
  drawNoteAxis();
}

function syncScroll(left, source) {
  if (S.syncing) return;
  if (Math.abs(left - S.scroll) < 0.5 && source !== 'force') return;
  S.syncing = true;
  S.scroll = left;
  for (const ws of allWs()) {
    if (ws !== source && Math.abs(ws.getScroll() - left) > 0.5) ws.setScroll(left);
  }
  const sc = $('scroller');
  if (source !== sc && Math.abs(sc.scrollLeft - left) > 0.5) sc.scrollLeft = left;
  S.syncing = false;
  drawOverlay();
}

function setScrollAll(left) {
  const max = Math.max(0, S.session.duration * pxPerSec() - viewWidth());
  syncScroll(clamp(left, 0, max), 'force');
}

function applyZoom(value, anchorTime = null, anchorX = null) {
  if (!S.session) return;
  value = clamp(value, 0, 100);
  $('zoom').value = value;
  const w = viewWidth();
  const fit = w / S.session.duration;
  const px = value <= 0 ? 0 : fit * Math.pow(Math.max(1, MAX_PX_PER_SEC / fit), value / 100);
  const oldPx = pxPerSec();
  if (anchorTime === null) {
    anchorX = w / 2;
    anchorTime = (S.scroll + anchorX) / oldPx;
  }
  S.zoomPx = px;
  for (const ws of allWs()) ws.zoom(px);
  requestAnimationFrame(() => {
    onLayout();
    setScrollAll(anchorTime * pxPerSec() - anchorX);
  });
}

// ---------------------------------------------------------------------------
// F0 と有声 / 無音の表示
// ---------------------------------------------------------------------------

function prepareF0(info) {
  const f0 = info.f0;
  const midi = new Float32Array(f0.length);
  const vals = [];
  for (let i = 0; i < f0.length; i++) {
    if (f0[i] > 0) {
      midi[i] = 69 + 12 * Math.log2(f0[i] / 440);
      vals.push(midi[i]);
    } else {
      midi[i] = NaN;
    }
  }
  vals.sort((a, b) => a - b);
  let lo = 48, hi = 72;
  if (vals.length > 10) {
    lo = Math.floor(vals[Math.floor(vals.length * 0.01)]) - 3;
    hi = Math.ceil(vals[Math.floor(vals.length * 0.99)]) + 3;
    if (hi - lo < 14) { const c = (hi + lo) / 2; lo = Math.floor(c - 7); hi = Math.ceil(c + 7); }
  }
  S.f0 = { fp: info.frame_period / 1000, midi, lo, hi };
}

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return [ctx, w, h];
}

function midiToY(m, h) {
  const { lo, hi } = S.f0;
  return h - ((m - lo) / (hi - lo)) * h;
}

function drawOverlay() {
  const canvas = $('overlay');
  const [ctx, w, h] = fitCanvas(canvas);
  if (!S.session || !S.f0) return;
  const px = pxPerSec();
  const t0 = S.scroll / px;
  const t1 = (S.scroll + w) / px;
  const x = (t) => t * px - S.scroll;

  // 無音(薄く暗く)
  ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
  for (const [s, e] of S.session.silent) {
    if (e < t0 || s > t1) continue;
    ctx.fillRect(x(s), 0, (e - s) * px, h);
  }
  // 半音のグリッド
  const { lo, hi } = S.f0;
  for (let m = Math.ceil(lo); m <= hi; m++) {
    const y = Math.round(midiToY(m, h)) + 0.5;
    ctx.strokeStyle = m % 12 === 0 ? 'rgba(255,255,255,0.16)' : 'rgba(255,255,255,0.045)';
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  // 有声(下端の帯)
  ctx.fillStyle = 'rgba(110, 200, 140, 0.55)';
  for (const [s, e] of S.session.voiced) {
    if (e < t0 || s > t1) continue;
    ctx.fillRect(x(s), h - 4, (e - s) * px, 4);
  }
  // F0
  const { fp, midi } = S.f0;
  const i0 = Math.max(0, Math.floor(t0 / fp) - 1);
  const i1 = Math.min(midi.length - 1, Math.ceil(t1 / fp) + 1);
  const stride = Math.max(1, Math.floor(1 / (px * fp)));
  ctx.strokeStyle = '#f6d365';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  let pen = false;
  for (let i = i0; i <= i1; i += stride) {
    const m = midi[i];
    if (Number.isNaN(m)) { pen = false; continue; }
    const xx = i * fp * px - S.scroll;
    const yy = midiToY(m, h);
    if (pen) ctx.lineTo(xx, yy); else ctx.moveTo(xx, yy);
    pen = true;
  }
  ctx.stroke();
}

function drawNoteAxis() {
  const canvas = $('note-axis');
  const [ctx, w, h] = fitCanvas(canvas);
  if (!S.f0) return;
  const { lo, hi } = S.f0;
  const perSemi = h / (hi - lo);
  ctx.font = '11px Consolas, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let m = Math.ceil(lo); m <= hi; m++) {
    const name = NOTE_NAMES[((m % 12) + 12) % 12];
    const isC = m % 12 === 0;
    const natural = !name.includes('#');
    if (!(isC || (perSemi >= 11) || (natural && perSemi >= 6))) continue;
    const y = midiToY(m, h);
    if (y < 6 || y > h - 6) continue;
    ctx.fillStyle = isC ? '#dfe3ea' : (natural ? '#8d95a5' : '#5c6372');
    ctx.fillText(`${name}${Math.floor(m / 12) - 1}`, w - 8, y);
    ctx.fillRect(w - 5, Math.round(y), 5, 1);
  }
  ctx.textAlign = 'left';
  ctx.fillStyle = '#8d95a5';
  ctx.fillText('F0', 8, 10);
}

// ---------------------------------------------------------------------------
// 区間・マーカー
// ---------------------------------------------------------------------------

function defaultParams(spec) {
  const p = {};
  for (const ps of spec.params) p[ps.name] = ps.default;
  return p;
}

function regionLabel(model) {
  const el = document.createElement('div');
  el.textContent = `${Math.round(model.strength)}`;
  return el;
}

function regionColor(model) {
  const isMarker = S.effectMap[model.type].kind === 'marker';
  const a = model.enabled === false ? (isMarker ? 0.3 : 0.12) : (isMarker ? 0.95 : 0.35);
  return rgba(COLORS[model.type], a);
}

function decorate(view) {
  const { model, region } = view;
  if (!region) return;
  region.setOptions({ color: regionColor(model) });
  region.setContent(regionLabel(model));
  if (region.element) {
    const warned = S.warnIds.has(model.id);
    region.element.style.outline = S.selected === model.id ? '2px solid #ffffff'
      : (warned ? '2px dashed #ff6b6b' : 'none');
    region.element.style.outlineOffset = '-2px';
    region.element.title = t('region.title', { label: S.effectMap[model.type].label, n: Math.round(model.strength) });
  }
}

function onRegionCreated(type, r) {
  const existing = S.views[r.id];
  if (existing) {
    existing.region = r;
    decorate(existing);
    return;
  }
  // ドラッグで新しく作られた区間
  const spec = S.effectMap[type];
  if (spec.kind !== 'marker' && r.end - r.start < 0.05) {
    setTimeout(() => r.remove(), 0);
    return;
  }
  const model = { id: newId(), type, strength: DEFAULT_STRENGTH, params: defaultParams(spec) };
  if (spec.kind === 'marker') model.time = r.start;
  else { model.start = r.start; model.end = r.end; }
  r.setOptions({ id: model.id });
  S.project.regions.push(model);
  const view = { model, region: r, type };
  S.views[model.id] = view;
  select(model.id);
  scheduleSync();
}

function addMarkerAt(type, t) {
  const spec = S.effectMap[type];
  const model = { id: newId(), type, time: t, strength: DEFAULT_STRENGTH, params: defaultParams(spec) };
  S.project.regions.push(model);
  S.views[model.id] = { model, region: null, type };
  S.lanes[type].regions.addRegion({ id: model.id, start: t, color: regionColor(model), drag: true, resize: false });
  select(model.id);
  scheduleSync();
}

function onRegionUpdated(r) {
  const view = S.views[r.id];
  if (!view) return;
  const m = view.model;
  if (S.effectMap[m.type].kind === 'marker') m.time = r.start;
  else { m.start = r.start; m.end = r.end; }
  if (S.selected === m.id) renderPanel();
  else select(m.id);
  scheduleSync();
}

function deleteRegion(id) {
  const view = S.views[id];
  if (!view) return;
  if (view.region) view.region.remove();
  delete S.views[id];
  S.project.regions = S.project.regions.filter((r) => r.id !== id);
  if (S.selected === id) select(null);
  scheduleSync();
}

function select(id) {
  const prev = S.selected;
  S.selected = id;
  if (prev && S.views[prev]) decorate(S.views[prev]);
  if (id && S.views[id]) decorate(S.views[id]);
  if (!id && S.loopOn) setLoop(false);
  renderPanel();
  updateLoopButton();
}

function regionExtent(model) {
  const spec = S.effectMap[model.type];
  const ramp = (model.ramp_ms ?? 50) / 1000;
  if (spec.kind === 'marker' && model.params && model.params.duration_ms != null) {
    return [model.time, model.time + model.params.duration_ms / 1000];
  }
  if (spec.kind === 'marker') return [model.time - ramp, model.time + ramp];
  return [model.start, model.end + (model.type === 'phrase_end' ? ramp : 0)];
}

// ---------------------------------------------------------------------------
// パラメータ欄
// ---------------------------------------------------------------------------

function fieldHTML(key, label, value, min, max, step, unit = '') {
  return `<div class="field" data-key="${key}">
    <div class="flabel"><span>${label}</span><span class="range">${t('range', { min, max })}${unit ? ' ' + unit : ''}</span></div>
    <input type="range" min="${min}" max="${max}" step="${step}" value="${value}">
    <input type="number" min="${min}" max="${max}" step="${step}" value="${value}">
  </div>`;
}

function renderPanel() {
  const panel = $('panel-region');
  const view = S.selected && S.views[S.selected];
  $('panel-empty').hidden = !!view;
  panel.hidden = !view;
  if (!view) { panel.innerHTML = ''; return; }
  const m = view.model;
  const spec = S.effectMap[m.type];
  const isMarker = spec.kind === 'marker';
  const color = COLORS[m.type];
  let html = `<h3><span class="swatch" style="background:${color};width:12px;height:12px;border-radius:2px;display:inline-block"></span>${spec.label}</h3>
    <p class="desc">${spec.description || ''}</p>`;
  if (isMarker) {
    html += `<div class="row-times"><label>${t('panel.time')}<input type="number" data-time="time" step="0.01" min="0" value="${m.time.toFixed(3)}"></label></div>`;
  } else {
    html += `<div class="row-times">
      <label>${t('panel.start')}<input type="number" data-time="start" step="0.01" min="0" value="${m.start.toFixed(3)}"></label>
      <label>${t('panel.end')}<input type="number" data-time="end" step="0.01" min="0" value="${m.end.toFixed(3)}"></label>
    </div>`;
  }
  html += `<label class="check"><input type="checkbox" id="chk-enabled" ${m.enabled === false ? '' : 'checked'}> ${t('panel.enabled')}</label>`;
  html += fieldHTML('strength', t('panel.strength'), Math.round(m.strength), 0, 100, 1);
  for (const p of spec.params) {
    if (p.kind === 'choice') {
      const files = S.materials[p.choices_source] || [];
      const cur = m.params[p.name] ?? p.default;
      const opts = [`<option value="">${t('panel.synth')}</option>`]
        .concat(files.map((f) => `<option value="${f}" ${f === cur ? 'selected' : ''}>${f}</option>`));
      if (cur && !files.includes(cur)) opts.push(`<option value="${cur}" selected>${t('panel.notFound', { name: cur })}</option>`);
      const dir = S.materials[`${p.choices_source}:dir`] || '';
      html += `<div class="choice" data-key="p:${p.name}"><div class="flabel"><span>${p.label}</span></div>
        <select>${opts.join('')}</select>
        <div class="hint">${files.length ? '' : t('panel.noMaterials')}${t('panel.materialsDir', { dir })}</div></div>`;
      continue;
    }
    html += fieldHTML(`p:${p.name}`, p.label, m.params[p.name] ?? p.default, p.min, p.max, p.step, p.unit);
  }
  if (spec.uses_ramp !== false) html += fieldHTML('ramp_ms', t('panel.ramp'), m.ramp_ms ?? 50, 10, 300, 5, 'ms');
  html += `<div class="actions">
    <button id="btn-region-loop">${t('panel.loop')}</button>
    <button id="btn-region-reset">${t('panel.reset')}</button>
    <button id="btn-region-delete" class="danger">${t('panel.delete')}</button>
  </div>`;
  panel.innerHTML = html;

  for (const f of panel.querySelectorAll('.field')) {
    const key = f.dataset.key;
    const [range, num] = f.querySelectorAll('input');
    const min = parseFloat(range.min), max = parseFloat(range.max);
    const setVal = (v, sync) => {
      v = clamp(Number.isFinite(v) ? v : min, min, max); // 安全上限を超える値は入らない
      range.value = v;
      num.value = v;
      if (key === 'strength') m.strength = v;
      else if (key === 'ramp_ms') m.ramp_ms = v;
      else m.params[key.slice(2)] = v;
      decorate(view);
      if (sync) scheduleSync();
    };
    range.addEventListener('input', () => setVal(parseFloat(range.value), false));
    range.addEventListener('change', () => setVal(parseFloat(range.value), true));
    num.addEventListener('change', () => setVal(parseFloat(num.value), true));
  }
  for (const c of panel.querySelectorAll('.choice')) {
    const sel = c.querySelector('select');
    sel.addEventListener('change', () => {
      m.params[c.dataset.key.slice(2)] = sel.value;
      scheduleSync();
    });
  }
  for (const inp of panel.querySelectorAll('[data-time]')) {
    inp.addEventListener('change', () => {
      const k = inp.dataset.time;
      let v = clamp(parseFloat(inp.value) || 0, 0, S.session.duration);
      if (k === 'start') v = Math.min(v, m.end - 0.02);
      if (k === 'end') v = Math.max(v, m.start + 0.02);
      m[k] = v;
      if (view.region) view.region.setOptions(isMarker ? { start: v } : { start: m.start, end: m.end });
      renderPanel();
      scheduleSync();
    });
  }
  $('chk-enabled').addEventListener('change', (e) => {
    m.enabled = e.target.checked;
    decorate(view);
    scheduleSync();
  });
  $('btn-region-loop').addEventListener('click', () => loopSelected(true));
  $('btn-region-delete').addEventListener('click', () => deleteRegion(m.id));
  $('btn-region-reset').addEventListener('click', () => {
    m.params = defaultParams(spec);
    m.strength = DEFAULT_STRENGTH;
    delete m.ramp_ms;
    decorate(view);
    renderPanel();
    scheduleSync();
  });
}

// ---------------------------------------------------------------------------
// サーバーとの同期(変わった範囲だけ再処理してもらう)
// ---------------------------------------------------------------------------

function projectBody() {
  return {
    ...S.project,
    global_cry_amount: Number($('global-amount').value),
    regions: S.project.regions,
  };
}

function scheduleSync(delay = 120) {
  if (!S.session) return;
  markDirty(true);
  clearTimeout(S.sync.timer);
  S.sync.timer = setTimeout(() => { S.sync.timer = null; doSync(); }, delay);
}

async function doSync() {
  if (S.sync.inflight) { S.sync.pending = true; return; }
  S.sync.inflight = true;
  setRenderStatus(t('render.busy'), true);
  try {
    const res = await postJSON('/api/project', projectBody());
    await applyPatches(res.changed);
    const what = res.rendered
      ? t('render.done', { n: res.rendered, s: res.elapsed.toFixed(2) })
      : (res.changed.length ? t('render.restored') : t('render.none'));
    setRenderStatus(what);
    // 同じ警告は出し直さない(新しく出たものだけ表示)
    const now = new Set(res.warnings || []);
    for (const w of now) if (!S.lastWarnings || !S.lastWarnings.has(w)) message(w, 'warn');
    S.lastWarnings = now;
    const prevWarn = S.warnIds;
    S.warnIds = new Set(res.warn_ids || []);
    for (const id of new Set([...prevWarn, ...S.warnIds])) if (S.views[id]) decorate(S.views[id]);
  } catch (e) {
    setRenderStatus(t('render.failed'));
    message(t('render.failedMsg', { e: e.message }), 'error', 0);
  } finally {
    S.sync.inflight = false;
    if (S.sync.pending) { S.sync.pending = false; doSync(); }
  }
}

async function flushSync() {
  if (S.sync.timer) { clearTimeout(S.sync.timer); S.sync.timer = null; await doSync(); }
  while (S.sync.inflight || S.sync.pending) await sleep(40);
}

async function applyPatches(ranges) {
  const buf = S.player.bufB;
  for (const [s0, s1] of ranges) {
    if (s1 <= s0) continue;
    const r = await api(`/api/patch?s0=${s0}&s1=${s1}`);
    const ch = parseInt(r.headers.get('X-Channels'), 10);
    const n = parseInt(r.headers.get('X-Frames'), 10);
    const all = new Float32Array(await r.arrayBuffer());
    for (let c = 0; c < Math.min(ch, buf.numberOfChannels); c++) {
      buf.copyToChannel(all.subarray(c * n, (c + 1) * n), c, s0);
    }
  }
  if (ranges.length) {
    // 再生し直しの失敗は処理の失敗ではないので、別に扱う
    try {
      S.player.refresh();
    } catch (e) {
      S.player.stop();
      updatePlayButton();
      message(t('play.failed', { e: e.message }), 'warn');
    }
  }
}

// ---------------------------------------------------------------------------
// 再生
// ---------------------------------------------------------------------------

function updatePlayButton() {
  $('btn-play').textContent = S.player && S.player.playing ? t('stop') : t('play');
}
function updateLoopButton() {
  $('btn-loop').disabled = !S.session || !S.selected;
  $('btn-loop').classList.toggle('on', S.loopOn);
}
function stopPlayback() {
  if (S.player) S.player.stop();
  updatePlayButton();
}
function togglePlay() {
  if (!S.player) return;
  if (S.player.playing) S.player.stop();
  else S.player.play(null, S.loopOn ? S.player.loop : null);
  updatePlayButton();
}
function setLoop(on) {
  S.loopOn = on;
  if (!on) {
    if (S.player) {
      const wasPlaying = S.player.playing;
      const t = S.player.time;
      S.player.loop = null;
      if (wasPlaying) S.player.play(t);
    }
  }
  updateLoopButton();
}
function loopSelected(force = false) {
  const view = S.selected && S.views[S.selected];
  if (!view) return;
  if (S.loopOn && !force) { setLoop(false); return; }
  const [s, e] = regionExtent(view.model);
  const loop = { start: Math.max(0, s - LOOP_PAD), end: Math.min(S.session.duration, e + LOOP_PAD) };
  S.loopOn = true;
  S.player.play(loop.start, loop);
  updatePlayButton();
  updateLoopButton();
}
function setMode(mode) {
  S.mode = mode;
  if (S.player) S.player.setMode(mode);
  $('btn-a').classList.toggle('on', mode === 'A');
  $('btn-b').classList.toggle('on', mode === 'B');
}

function tick() {
  if (S.player && S.session) {
    const t = S.player.time;
    $('time').textContent = fmtTime(t);
    const px = pxPerSec();
    const w = viewWidth();
    let x = t * px - S.scroll;
    if (S.player.playing && (x > w * 0.95 || x < 0)) {
      setScrollAll(t * px - w * 0.1);
      x = t * px - S.scroll;
    }
    const ph = $('playhead');
    ph.style.display = x < 0 || x > w ? 'none' : 'block';
    ph.style.transform = `translateX(${x + 10}px)`;
    if (!S.player.playing) updatePlayButton();
  }
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------------------
// 保存・書き出し
// ---------------------------------------------------------------------------

async function askPath(mode, kind) {
  const res = await postJSON('/api/dialog', { mode, kind });
  if (res.error) message(t('dialog.failed', { e: res.error }), 'error');
  return res.path;
}

async function saveProject(as = false) {
  if (!S.session) return false;
  let path = null;
  if (as) {
    path = await askPath('save', 'project');
    if (!path) return false;
  }
  await flushSync();
  try {
    const res = await postJSON('/api/save', path ? { path } : {});
    S.session.project_path = res.path;
    markDirty(false);
    message(t('saved', { path: res.path }));
    return true;
  } catch (e) {
    message(t('save.failed', { e: e.message }), 'error', 0);
    return false;
  }
}

async function exportWav() {
  if (!S.session) return;
  const path = await askPath('save', 'wav');
  if (!path) return;
  await flushSync();
  try {
    busy(true, t('busy.exporting'), 1);
    const res = await postJSON('/api/export', { path });
    message(t('exported', { path: res.path }));
  } catch (e) {
    message(t('export.failed', { e: e.message }), 'error', 0);
  } finally {
    busy(false);
  }
}

// ---------------------------------------------------------------------------
// 操作の割り当て
// ---------------------------------------------------------------------------

function bindUI() {
  $('btn-open').addEventListener('click', async () => {
    const p = await askPath('open', 'open');
    if (p) openPath(p);
  });
  $('path-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') openPath($('path-input').value.trim().replace(/^"|"$/g, ''));
  });
  $('btn-save').addEventListener('click', () => saveProject(false));
  $('btn-save-as').addEventListener('click', () => saveProject(true));
  $('btn-export').addEventListener('click', exportWav);
  $('btn-play').addEventListener('click', togglePlay);
  $('btn-loop').addEventListener('click', () => loopSelected(false));
  $('btn-a').addEventListener('click', () => setMode('A'));
  $('btn-b').addEventListener('click', () => setMode('B'));

  const ga = $('global-amount');
  ga.addEventListener('input', () => { $('global-amount-val').textContent = ga.value; });
  ga.addEventListener('change', () => { S.project.global_cry_amount = Number(ga.value); scheduleSync(0); });

  $('zoom').addEventListener('input', () => applyZoom(Number($('zoom').value)));

  const ls = $('lang-select');
  for (const [code, name] of Object.entries(LANGS)) {
    const o = document.createElement('option');
    o.value = code;
    o.textContent = name;
    ls.appendChild(o);
  }
  ls.value = getLang();
  ls.addEventListener('change', () => changeLang(ls.value));

  $('scroller').addEventListener('scroll', () => {
    if (S.session) syncScroll($('scroller').scrollLeft, $('scroller'));
  });

  $('timeline').addEventListener('wheel', (e) => {
    if (!S.session) return;
    if (e.ctrlKey) {
      e.preventDefault();
      const rect = $('main-track').getBoundingClientRect();
      const ax = clamp(e.clientX - rect.left, 0, rect.width);
      const at = (S.scroll + ax) / pxPerSec();
      applyZoom(Number($('zoom').value) - Math.sign(e.deltaY) * 4, at, ax);
    } else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      e.preventDefault();
      const d = e.shiftKey ? e.deltaY : e.deltaX;
      setScrollAll(S.scroll + d);
    }
  }, { passive: false });

  // 波形のクリックで再生位置
  $('main-track').addEventListener('click', (e) => {
    if (!S.session) return;
    S.player.seek(timeAtClientX(e.clientX));
  });

  // ボタンにフォーカスを残さない(Space でボタンが押されて再生が二重に切り替わるのを防ぐ)
  document.addEventListener('mousedown', (e) => {
    if (e.target.closest && e.target.closest('button')) e.preventDefault();
  });
  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && (e.target.tagName || '').toLowerCase() === 'button') e.preventDefault();
  });

  document.addEventListener('keydown', (e) => {
    if (!$('modal').hidden) return; // 確認ダイアログ中はショートカットを止める
    const tag = (e.target.tagName || '').toLowerCase();
    if (e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); saveProject(false); return; }
    if (tag === 'input' && e.target.type !== 'range' && e.target.type !== 'checkbox') return;
    if (tag === 'textarea') return;
    if (!S.session) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.key === 'b' || e.key === 'B') setMode(S.mode === 'A' ? 'B' : 'A');
    else if (e.key === 'l' || e.key === 'L') loopSelected(false);
    else if (e.key === 'Delete' || e.key === 'Backspace') { if (S.selected) { e.preventDefault(); deleteRegion(S.selected); } }
    else if (e.key === 'Escape') select(null);
    else if (e.key === 'Home') S.player.seek(0);
  });

  window.addEventListener('resize', () => requestAnimationFrame(onLayout));

  // 画面が開いている間はサーバーに知らせる(閉じたらサーバーも終わる)
  const ping = () => fetch('/api/ping', { method: 'POST' }).catch(() => {});
  ping();
  setInterval(ping, 20000);
  window.addEventListener('pagehide', () => navigator.sendBeacon('/api/bye'));
  window.addEventListener('beforeunload', (e) => {
    if (S.dirty) { e.preventDefault(); e.returnValue = ''; }
  });
}

async function loadEffects() {
  S.effects = await getJSON('/api/effects');
  S.effectMap = {};
  for (const e of S.effects) S.effectMap[e.name] = e;
}

function sessionStatus() {
  const info = S.session;
  return `${info.fs} Hz / ${info.channels} ch / ${fmtTime(info.duration)}`;
}

function fillLaneLabel(label, spec, type) {
  const color = COLORS[type] || '#999';
  const hint = spec.kind === 'marker' ? t('lane.marker') : (spec.inserts_in_silence ? t('lane.silence') : t('lane.region'));
  label.innerHTML = `<div class="name"><span class="swatch" style="background:${color}"></span>${spec.label}</div>` +
    `<div class="sub">${type} · ${hint}</div>`;
  label.title = `${spec.label}\n${spec.description || ''}`;
}

async function changeLang(lang) {
  try {
    const st = await postJSON('/api/settings', { lang });
    setLang(st.lang);
  } catch (e) {
    message(e.message, 'error');
    return;
  }
  applyStatic();
  await loadEffects();
  for (const [type, lane] of Object.entries(S.lanes)) {
    lane.spec = S.effectMap[type];
    fillLaneLabel(lane.label, lane.spec, type);
  }
  for (const v of Object.values(S.views)) decorate(v);
  renderPanel();
  updatePlayButton();
  markDirty(S.dirty);
  if (S.session) setStatus(sessionStatus());
  else setStatus((await getJSON('/api/status')).message);
}

async function init() {
  try {
    const st = await getJSON('/api/settings');
    setLang(st.lang);
    $('version').textContent = `v${st.version}`;
  } catch (e) {
    setLang('en');
  }
  applyStatic();
  await loadEffects();
  bindUI();
  setMode('B');
  updatePlayButton();
  requestAnimationFrame(tick);
  const st = await getJSON('/api/status');
  setStatus(st.message);
  if (st.state === 'analyzing' || st.state === 'rendering' || st.state === 'ready') {
    try {
      if (await waitReady()) await loadSession();
    } catch (e) {
      message(e.message, 'error', 0);
    }
  }
}

init();
