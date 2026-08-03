"""Flat-image field-calibration tool — bypasses all 3D math.

Extracts a single equirect frame from the game video, serves it in a
pan/zoom browser viewer, and lets the user click directly on the 4 field
corners in pixel space. Saves the homography to Firestore under
`teams/main/games/<gameId>.calibration` exactly like calibrate_local.py.

Use this when the 3D sphere viewer in soccer_team_app.jsx::FieldCalibrationModal
produces wrong pixels (e.g. all clicks collapse to the horizon band).

Usage:
    .venv-post-game/bin/python -m post_game.calibrate_flat <game-id> [--at SECONDS]
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from typing import Optional
from urllib.parse import urlparse

from . import firestore_io

log = logging.getLogger(__name__)

PORT = 8766  # one higher than calibrate_local.py to avoid clashes


def _refine_with_tilt(payload: dict) -> dict:
    """Re-solve the calibration from the clicked reference points, overwriting the
    browser's LEVEL-camera 2D similarity. Two modes (purely additive — on any
    failure / too-few points, the original level payload is written unchanged so
    a save is never blocked):

    * SCALED (preferred, accuracy-audit B1 + scale fix): when a map-measured
      touchline length is present (payload['map_length_m']), fix the field LENGTH
      to it and solve camera pitch/roll + field WIDTH. This anchors absolute scale
      (nothing in-scene has a known size — goal width varies between U10 fields)
      while recovering the field shape from the clicks. Writes solved width_m.
    * TILT-ONLY (fallback): no map length → solve pitch/roll at the assumed dims.
    """
    try:
        from .calibration_solve import solve_sphere_tilt, solve_sphere_scaled
        rp = payload.get("reference_points") or []
        eq_w = int(payload.get("video_frame_w") or 0)
        eq_h = int(payload.get("video_frame_h") or 0)
        cam_h = float(payload.get("camera_height_m") or 5.0)
        goal_w = float(payload.get("goal_width_m") or 4.88)
        map_len = payload.get("map_length_m")
        if len(rp) < 4 or not (eq_w and eq_h):
            return payload

        if map_len:  # SCALED: length anchored to the map, solve width + tilt
            sol = solve_sphere_scaled(rp, eq_w, eq_h, length_m=float(map_len),
                                      cam_h=cam_h, goal_w=goal_w,
                                      w0=float(payload.get("width_m") or 35.0))
            solver_tag = "scaled_lsq"
        else:        # FALLBACK: fixed dims, tilt only
            sol = solve_sphere_tilt(rp, eq_w, eq_h, cam_h=cam_h)
            solver_tag = "tilt_lsq"
        if not sol:
            return payload

        gs = dict(payload.get("ground_similarity") or {})
        level_rms = gs.get("rms_m")
        gs.update(a=sol["a"], b=sol["b"], tx=sol["tx"], ty=sol["ty"],
                  rms_m=sol["rms_m"], rms_level_m=level_rms,
                  residuals=sol["per_point"], solver=solver_tag)
        payload["ground_similarity"] = gs
        payload["camera_pitch_deg"] = sol["pitch_deg"]
        payload["camera_roll_deg"] = sol["roll_deg"]
        # In scaled mode the solve also gives the field dimensions — persist them
        # so the pipeline/projector use the recovered geometry, not the defaults.
        if "length_m" in sol:
            payload["length_m"] = sol["length_m"]
        if "width_m" in sol:
            payload["width_m"] = sol["width_m"]
        log.info("Calibration refined (%s): rms %s -> %.3f m (pitch %+.2f°, roll %+.2f°%s)",
                 solver_tag,
                 f"{float(level_rms):.3f}" if level_rms is not None else "n/a",
                 sol["rms_m"], sol["pitch_deg"], sol["roll_deg"],
                 f", width {sol['width_m']:.1f}m @ length {sol['length_m']:.1f}m" if "width_m" in sol else "")
    except Exception as e:  # never block a save on the refinement
        log.warning("Calibration refinement skipped: %s", e)
    return payload


def _extract_frame(video_url: str, at_seconds: float, out_path: str) -> None:
    """Pull a single frame from the video at `at_seconds` via ffmpeg."""
    src = video_url
    if src.startswith("file://"):
        src = src[len("file://"):]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{at_seconds:.3f}",
        "-i", src,
        "-frames:v", "1",
        "-q:v", "2",
        out_path,
    ]
    log.info("Extracting frame at t=%.2fs -> %s", at_seconds, out_path)
    subprocess.run(cmd, check=True)


def _build_page(game_label: str, frame_w: int, frame_h: int,
                default_camera_height_m: float = 5.0,
                field_length_m: float = 50.0,
                field_width_m: float = 35.0,
                goal_width_m: float = 4.88) -> str:
    safe_label = json.dumps(game_label)
    return r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Flat Field Calibration</title>
<style>
  :root { color-scheme: dark; }
  html, body { height:100%; margin:0; }
  body { background:#0c0a09; color:#e7e5e4; font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif; display:flex; flex-direction:column; overflow:hidden; }
  header { padding:10px 14px; border-bottom:1px solid #292524; display:flex; gap:12px; align-items:center; flex:0 0 auto; }
  header h1 { margin:0; font-size:14px; letter-spacing:.04em; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#1c1917; color:#a8a29e; font-size:11px; }
  #status { margin-left:auto; font-size:12px; color:#84cc16; }
  #status.warn { color:#fbbf24; }
  #status.err { color:#f87171; }
  #hint { padding:6px 14px; font-size:12px; color:#a8a29e; border-bottom:1px solid #292524; display:flex; gap:10px; align-items:center; flex-wrap:wrap; flex:0 0 auto; }
  #hint strong { color:#84cc16; }
  #wrap { flex:1 1 auto; position:relative; overflow:hidden; min-height:0; background:#000; cursor:crosshair; touch-action:none; user-select:none; }
  #stage { position:absolute; left:0; top:0; transform-origin:0 0; will-change:transform; }
  #stage img { display:block; pointer-events:none; -webkit-user-drag:none; }
  #stage svg { position:absolute; left:0; top:0; pointer-events:none; }
  footer { padding:10px 14px; border-top:1px solid #292524; display:flex; gap:8px; align-items:center; flex:0 0 auto; background:#0c0a09; }
  footer button { flex:1; padding:10px; border-radius:8px; border:0; cursor:pointer; font-weight:600; }
  .btn-secondary { background:#292524; color:#d6d3d1; }
  .btn-primary { background:#84cc16; color:#0c0a09; }
  .btn-primary:disabled { opacity:.35; cursor:not-allowed; }
  #err { color:#f87171; font-size:12px; padding:0 14px 8px; }
  .ctrls { display:flex; gap:6px; }
  .ctrls button { padding:4px 10px; border-radius:6px; border:1px solid #44403c; background:#1c1917; color:#d6d3d1; cursor:pointer; font-size:12px; }
</style>
</head>
<body>
  <header>
    <h1>🎯 FLAT FIELD CALIBRATION</h1>
    <span class="pill" id="gameLabel"></span>
    <span id="status">Ready.</span>
  </header>
  <div id="hint">
    <span id="hintText"></span>
    <span class="ctrls">
      <button id="zoomIn">+</button>
      <button id="zoomOut">−</button>
      <button id="fit">FIT</button>
      <button id="full">100%</button>
    </span>
    <span style="color:#78716c;">drag to pan · wheel/pinch to zoom · click to place the highlighted point</span>
    <span style="margin-left:auto; display:flex; gap:6px; align-items:center;">
      <label for="camH" style="color:#a8a29e;">Camera height (m):</label>
      <input id="camH" type="number" step="0.1" min="1" max="30" value="__CAMH__"
             style="width:64px; background:#1c1917; color:#e7e5e4; border:1px solid #44403c; border-radius:6px; padding:4px 6px;" />
    </span>
  </div>
  <div id="refbar" style="display:flex; gap:6px; padding:6px 14px; border-bottom:1px solid #292524; flex-wrap:wrap; background:#0f0d0c;"></div>
  <div id="wrap">
    <div id="stage">
      <img id="frame" src="/frame.jpg" />
      <svg id="overlay" xmlns="http://www.w3.org/2000/svg"></svg>
    </div>
  </div>
  <div id="err"></div>
  <footer>
    <button class="btn-secondary" id="undoBtn">UNDO LAST</button>
    <button class="btn-secondary" id="resetBtn">RESET ALL</button>
    <button class="btn-primary" id="saveBtn" disabled>SAVE CALIBRATION (0 pts)</button>
  </footer>

<script>
const GAME_LABEL = __GAME_LABEL__;
const NATURAL_W = __FW__;
const NATURAL_H = __FH__;
const FIELD_L = __FL__;
const FIELD_W = __FWM__;
const GOAL_W  = __GW__;
document.getElementById('gameLabel').textContent = GAME_LABEL;

// All known reference points on the field. Each: { key, label, x_m, y_m, required }
// Field frame: x along length 0..FIELD_L, y across width 0..FIELD_W.
// NEAR touchline = y=FIELD_W (bottom of image, closest to camera); FAR = y=0 (top).
// ORDERED as a CLOCKWISE perimeter walk starting bottom-left (near-left corner):
// up the LEFT edge → across the FAR/top edge → down the RIGHT edge → back across
// the NEAR/bottom edge → center last. So placement never jumps side-to-side.
const REF_POINTS = [
  // bottom-left → up the LEFT edge (near → far)
  { key:'corner_NL', label:'NEAR-LEFT corner (start here)', x:0,       y:FIELD_W,              required:true,  group:'corner' },
  { key:'goal_L_in', label:`LEFT goal INNER post (closer to centerline)`,  x:0,       y:FIELD_W/2 + GOAL_W/2, required:false, group:'goal' },
  { key:'goal_L_mid',label:'LEFT goal-mouth center', x:0,       y:FIELD_W/2,            required:false, group:'goal' },
  { key:'goal_L_out',label:`LEFT goal OUTER post (away from centerline)`,  x:0,       y:FIELD_W/2 - GOAL_W/2, required:false, group:'goal' },
  { key:'corner_FL', label:'FAR-LEFT corner',        x:0,       y:0,                    required:true,  group:'corner' },
  // across the FAR/top edge (left → right)
  { key:'mid_far',   label:'CENTER ↔ FAR touchline', x:FIELD_L/2, y:0,                  required:false, group:'centerline' },
  { key:'corner_FR', label:'FAR-RIGHT corner',       x:FIELD_L, y:0,                    required:true,  group:'corner' },
  // down the RIGHT edge (far → near)
  { key:'goal_R_out',label:`RIGHT goal OUTER post (away from centerline)`, x:FIELD_L, y:FIELD_W/2 - GOAL_W/2, required:false, group:'goal' },
  { key:'goal_R_mid',label:'RIGHT goal-mouth center',x:FIELD_L, y:FIELD_W/2,            required:false, group:'goal' },
  { key:'goal_R_in', label:`RIGHT goal INNER post (closer to centerline)`, x:FIELD_L, y:FIELD_W/2 + GOAL_W/2, required:false, group:'goal' },
  { key:'corner_NR', label:'NEAR-RIGHT corner',      x:FIELD_L, y:FIELD_W,              required:true,  group:'corner' },
  // back across the NEAR/bottom edge (right → left)
  { key:'mid_near',  label:'CENTER ↔ NEAR touchline',x:FIELD_L/2, y:FIELD_W,            required:false, group:'centerline' },
  // center last
  { key:'center',    label:'CENTER spot',            x:FIELD_L/2, y:FIELD_W/2,          required:false, group:'center' },
];

const wrap = document.getElementById('wrap');
const stage = document.getElementById('stage');
const img = document.getElementById('frame');
const svg = document.getElementById('overlay');

// pan/zoom state
let scale = 1, tx = 0, ty = 0;
// pins: [{ key, px, py }]
let pins = [];
// Which ref point is currently selected for placement (defaults to first un-placed required)
let activeKey = REF_POINTS[0].key;

function placedKeys() { return new Set(pins.map(p => p.key)); }
function refByKey(k) { return REF_POINTS.find(r => r.key === k); }
function nextRequiredKey() {
  const placed = placedKeys();
  // Advance to the next UNPLACED landmark in LIST ORDER. REF_POINTS is a
  // clockwise perimeter walk, so following list order keeps placement moving
  // smoothly around the field (no side-to-side jumping) and flows through all
  // 13 points, not just the corners.
  for (const r of REF_POINTS) if (!placed.has(r.key)) return r.key;
  return null;  // all points placed
}

function setStatus(msg, cls) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = cls || '';
}
function setErr(msg) { document.getElementById('err').textContent = msg || ''; }

function applyTransform() {
  stage.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
}
function fitToWrap() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  scale = Math.min(w / NATURAL_W, h / NATURAL_H);
  tx = (w - NATURAL_W * scale) / 2;
  ty = (h - NATURAL_H * scale) / 2;
  applyTransform();
  redrawPins();
}
function fullSize() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  scale = 1;
  tx = (w - NATURAL_W) / 2;
  ty = (h - NATURAL_H) / 2;
  applyTransform();
  redrawPins();
}
function zoomBy(factor, cx, cy) {
  if (cx == null) { cx = wrap.clientWidth / 2; cy = wrap.clientHeight / 2; }
  // Zoom around (cx,cy) in wrap coords -> keep that point fixed
  const newScale = Math.max(0.05, Math.min(8, scale * factor));
  const k = newScale / scale;
  tx = cx - (cx - tx) * k;
  ty = cy - (cy - ty) * k;
  scale = newScale;
  applyTransform();
  redrawPins();
}

function clientToNatural(clientX, clientY) {
  const rect = wrap.getBoundingClientRect();
  const wx = clientX - rect.left;
  const wy = clientY - rect.top;
  return { px: (wx - tx) / scale, py: (wy - ty) / scale };
}

function redrawPins() {
  svg.setAttribute('width', NATURAL_W);
  svg.setAttribute('height', NATURAL_H);
  svg.setAttribute('viewBox', `0 0 ${NATURAL_W} ${NATURAL_H}`);
  const r = Math.max(12, 24 / scale);
  const sw = Math.max(2, 4 / scale);
  const fs = Math.max(16, 32 / scale);
  let html = '';
  pins.forEach((p, i) => {
    const ref = refByKey(p.key);
    const color = ref && ref.group === 'corner' ? '#84cc16'
                : ref && ref.group === 'centerline' ? '#fb923c'
                : ref && ref.group === 'center' ? '#f472b6'
                : '#60a5fa';
    html += `<circle cx="${p.px}" cy="${p.py}" r="${r}" fill="${color}" stroke="#0c0a09" stroke-width="${sw}"/>`;
    html += `<text x="${p.px}" y="${p.py + fs*0.35}" text-anchor="middle" font-size="${fs}" font-weight="700" font-family="system-ui" fill="#0c0a09">${i+1}</text>`;
  });
  svg.innerHTML = html;
}

function renderRefBar() {
  const bar = document.getElementById('refbar');
  const placed = placedKeys();
  bar.innerHTML = '';
  for (const r of REF_POINTS) {
    const isPlaced = placed.has(r.key);
    const isActive = r.key === activeKey;
    const btn = document.createElement('button');
    btn.textContent = (isPlaced ? '✓ ' : '') + r.label + (r.required ? ' *' : '');
    btn.title = `field (${r.x.toFixed(2)}, ${r.y.toFixed(2)}) m`;
    btn.style.cssText = `padding:4px 10px; border-radius:999px; border:1px solid ${isActive ? '#84cc16' : '#44403c'}; background:${isActive ? '#1f2a0a' : (isPlaced ? '#1a2410' : '#1c1917')}; color:${isPlaced ? '#a3e635' : '#d6d3d1'}; cursor:pointer; font-size:11px;`;
    btn.onclick = () => { activeKey = r.key; renderRefBar(); updateHint(); };
    bar.appendChild(btn);
  }
}

function updateHint() {
  const ref = refByKey(activeKey);
  const placed = placedKeys();
  const placedCount = pins.length;
  const reqLeft = REF_POINTS.filter(r => r.required && !placed.has(r.key)).length;
  const hint = placed.has(activeKey)
    ? `<strong>${ref.label}</strong> already placed — click again to re-place, or pick another point above.`
    : `Click the <strong>${ref.label}</strong> on the image. Field coord: (${ref.x.toFixed(2)}, ${ref.y.toFixed(2)}) m.`;
  document.getElementById('hintText').innerHTML = hint;
  const btn = document.getElementById('saveBtn');
  const canSave = placedCount >= 4 && reqLeft === 0;
  btn.disabled = !canSave;
  btn.textContent = canSave
    ? `SAVE CALIBRATION (${placedCount} pts)`
    : `SAVE CALIBRATION (${placedCount} pts, need 4 corners + total ≥4)`;
}

function placePin(clientX, clientY) {
  const { px, py } = clientToNatural(clientX, clientY);
  if (px < 0 || py < 0 || px >= NATURAL_W || py >= NATURAL_H) return;
  // Replace existing pin for this key, else append.
  const idx = pins.findIndex(p => p.key === activeKey);
  if (idx >= 0) pins[idx] = { key: activeKey, px, py };
  else pins.push({ key: activeKey, px, py });
  // Advance activeKey to next un-placed required point (if any), else stay.
  const nxt = nextRequiredKey();
  if (nxt) activeKey = nxt;
  redrawPins();
  renderRefBar();
  updateHint();
}
function undo() {
  const last = pins.pop();
  if (last) activeKey = last.key;
  redrawPins(); renderRefBar(); updateHint();
}
function reset() {
  pins = [];
  activeKey = REF_POINTS[0].key;
  redrawPins(); renderRefBar(); updateHint();
}

// Drag-to-pan vs click-to-place: track movement distance.
let pressX = 0, pressY = 0, pressT = 0, dragging = false, panStartTx = 0, panStartTy = 0;
const pointers = new Map();
let pinchStartDist = 0, pinchStartScale = 1, pinchCenter = null;

wrap.addEventListener('pointerdown', (e) => {
  wrap.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 1) {
    pressX = e.clientX; pressY = e.clientY; pressT = performance.now();
    panStartTx = tx; panStartTy = ty;
    dragging = false;
  } else if (pointers.size === 2) {
    const pts = [...pointers.values()];
    pinchStartDist = Math.hypot(pts[0].x-pts[1].x, pts[0].y-pts[1].y);
    pinchStartScale = scale;
    const rect = wrap.getBoundingClientRect();
    pinchCenter = { x: (pts[0].x + pts[1].x)/2 - rect.left, y: (pts[0].y + pts[1].y)/2 - rect.top };
  }
});
wrap.addEventListener('pointermove', (e) => {
  if (!pointers.has(e.pointerId)) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 1) {
    const dx = e.clientX - pressX, dy = e.clientY - pressY;
    if (!dragging && Math.hypot(dx, dy) > 5) dragging = true;
    if (dragging) {
      tx = panStartTx + dx;
      ty = panStartTy + dy;
      applyTransform();
    }
  } else if (pointers.size === 2) {
    const pts = [...pointers.values()];
    const d = Math.hypot(pts[0].x-pts[1].x, pts[0].y-pts[1].y);
    if (pinchStartDist > 0 && pinchCenter) {
      const factor = d / pinchStartDist;
      const newScale = Math.max(0.05, Math.min(8, pinchStartScale * factor));
      const k = newScale / scale;
      tx = pinchCenter.x - (pinchCenter.x - tx) * k;
      ty = pinchCenter.y - (pinchCenter.y - ty) * k;
      scale = newScale;
      applyTransform();
      redrawPins();
    }
  }
});
wrap.addEventListener('pointerup', (e) => {
  const wasOne = pointers.size === 1;
  pointers.delete(e.pointerId);
  if (wasOne) {
    if (!dragging) placePin(e.clientX, e.clientY);
    dragging = false;
  }
});
wrap.addEventListener('wheel', (e) => {
  e.preventDefault();
  const rect = wrap.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.15 : 1/1.15;
  zoomBy(factor, cx, cy);
}, { passive: false });

window.addEventListener('resize', () => { /* keep current transform */ });

document.getElementById('zoomIn').onclick = () => zoomBy(1.3);
document.getElementById('zoomOut').onclick = () => zoomBy(1/1.3);
document.getElementById('fit').onclick = fitToWrap;
document.getElementById('full').onclick = fullSize;
document.getElementById('undoBtn').onclick = undo;
document.getElementById('resetBtn').onclick = reset;

// Equirect pixel -> 3D ray (camera frame: +X right, +Y up, +Z back).
function pixelToRay(px, py) {
  const lon = (px / NATURAL_W) * 2 * Math.PI - Math.PI;
  const lat = Math.PI/2 - (py / NATURAL_H) * Math.PI;
  // Sphere convention matches the JSX viewer: dir = (sin(lon)cos(lat), sin(lat), -cos(lon)cos(lat))
  return {
    x: Math.sin(lon) * Math.cos(lat),
    y: Math.sin(lat),
    z: -Math.cos(lon) * Math.cos(lat),
  };
}

// Intersect a downward-looking ray with the ground plane at y = -h.
// Returns ground point in camera frame (Xc, Zc) — Y is the height axis.
function rayToGround(ray, h) {
  if (ray.y >= -1e-6) return null;          // ray points to or above horizon
  const t = -h / ray.y;                      // h>0; ray.y<0 -> t>0
  return { x: ray.x * t, z: ray.z * t };     // ground X,Z in camera frame
}

// Solve a 2D rigid+scale (similarity) transform from src points to dst points.
// Mapping: dst = s*R*src + t.
// Closed-form least-squares (Umeyama).
function solveSimilarity2D(src, dst) {
  const n = src.length;
  let mx=0,my=0,ux=0,uy=0;
  for (let i=0;i<n;i++) { mx+=src[i][0]; my+=src[i][1]; ux+=dst[i][0]; uy+=dst[i][1]; }
  mx/=n; my/=n; ux/=n; uy/=n;
  let sxx=0, syx=0, varS=0;
  for (let i=0;i<n;i++) {
    const dx = src[i][0]-mx, dy = src[i][1]-my;
    const Ux = dst[i][0]-ux, Uy = dst[i][1]-uy;
    sxx += dx*Ux + dy*Uy;
    syx += dx*Uy - dy*Ux;
    varS += dx*dx + dy*dy;
  }
  const denom = Math.hypot(sxx, syx);
  if (denom < 1e-12 || varS < 1e-12) return null;
  const cos = sxx / denom, sin = syx / denom;
  const s = denom / varS;
  // Apply scale*rotation, then translate
  const a = s*cos, b = -s*sin;
  // dst.x = a*src.x + b*src.y + tx
  // dst.y = -b*src.x + a*src.y + ty
  const tx = ux - (a*mx + b*my);
  const ty = uy - (-b*mx + a*my);
  // Compute residual RMS
  let r2 = 0;
  for (let i=0;i<n;i++) {
    const px = a*src[i][0] + b*src[i][1] + tx;
    const py = -b*src[i][0] + a*src[i][1] + ty;
    const ex = px - dst[i][0], ey = py - dst[i][1];
    r2 += ex*ex + ey*ey;
  }
  const rms = Math.sqrt(r2 / n);
  return { a, b, tx, ty, scale: s, rms };
}

document.getElementById('saveBtn').onclick = async () => {
  const placed = placedKeys();
  const reqLeft = REF_POINTS.filter(r => r.required && !placed.has(r.key));
  if (reqLeft.length > 0 || pins.length < 4) return;
  setStatus('Saving…');
  setErr('');
  const camH = parseFloat(document.getElementById('camH').value);
  if (!(camH > 0 && camH < 50)) {
    setStatus('Bad camera height.', 'err');
    setErr('Camera height must be a positive number of meters.');
    return;
  }
  // Build src (camera-frame ground X,Z) and dst (field x,y) arrays
  // in the same order, using the pin list.
  const groundPts = [];
  const dst = [];
  const labels = [];
  for (const p of pins) {
    const ref = refByKey(p.key);
    if (!ref) continue;
    const ray = pixelToRay(p.px, p.py);
    const g = rayToGround(ray, camH);
    if (!g) {
      setStatus('Pin above horizon.', 'err');
      setErr(`Pin "${ref.label}" is at or above the horizon line — cannot project to ground. Re-place it below the horizon.`);
      return;
    }
    groundPts.push([g.x, g.z]);
    dst.push([ref.x, ref.y]);
    labels.push(ref.key);
  }
  const sim = solveSimilarity2D(groundPts, dst);
  if (!sim) {
    setStatus('Bad geometry.', 'err');
    setErr('Could not solve calibration — points may be collinear or coincident.');
    return;
  }
  // Per-point residuals for diagnostics
  const residuals = [];
  for (let i=0;i<groundPts.length;i++) {
    const Xc = groundPts[i][0], Zc = groundPts[i][1];
    const fx =  sim.a*Xc + sim.b*Zc + sim.tx;
    const fy = -sim.b*Xc + sim.a*Zc + sim.ty;
    const ex = fx - dst[i][0], ey = fy - dst[i][1];
    residuals.push({ key: labels[i], dx_m: ex, dy_m: ey, err_m: Math.hypot(ex,ey) });
  }

  // Also keep the 4 corner pins in the legacy fields for backwards compat.
  const cornerKeys = ['corner_FL','corner_FR','corner_NR','corner_NL'];
  const cornerPins = cornerKeys.map(k => pins.find(p => p.key === k));
  const srcLegacy = cornerPins.map(p => [p.px, p.py]);
  const dstLegacy = [[0,0],[FIELD_L,0],[FIELD_L,FIELD_W],[0,FIELD_W]];

  const payload = {
    length_m: FIELD_L, width_m: FIELD_W,
    camera_height_m: camH,
    goal_width_m: GOAL_W,
    // Legacy 4-corner fields (kept for any old reader)
    src_points_px: { p0:{x:srcLegacy[0][0],y:srcLegacy[0][1]}, p1:{x:srcLegacy[1][0],y:srcLegacy[1][1]}, p2:{x:srcLegacy[2][0],y:srcLegacy[2][1]}, p3:{x:srcLegacy[3][0],y:srcLegacy[3][1]} },
    dst_points_m:  { p0:{x:dstLegacy[0][0],y:dstLegacy[0][1]}, p1:{x:dstLegacy[1][0],y:dstLegacy[1][1]}, p2:{x:dstLegacy[2][0],y:dstLegacy[2][1]}, p3:{x:dstLegacy[3][0],y:dstLegacy[3][1]} },
    // New: all reference points used in the fit
    reference_points: pins.map(p => {
      const ref = refByKey(p.key);
      return { key: p.key, label: ref.label, px: p.px, py: p.py, field_x_m: ref.x, field_y_m: ref.y };
    }),
    ground_similarity: { a: sim.a, b: sim.b, tx: sim.tx, ty: sim.ty,
                         scale: sim.scale, rms_m: sim.rms,
                         residuals: residuals },
    video_frame_w: NATURAL_W, video_frame_h: NATURAL_H,
    created_at: Date.now(),
    source: 'flat_sphere_multi',
  };
  const worst = residuals.reduce((m, r) => r.err_m > m.err_m ? r : m, residuals[0]);
  setStatus(`RMS ${sim.rms.toFixed(2)} m · worst ${worst.err_m.toFixed(2)} m @ ${worst.key} — saving…`);
  try {
    const r = await fetch('/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
    setStatus('Saved! You can close this tab.');
    document.getElementById('saveBtn').disabled = true;
    setTimeout(() => { document.body.innerHTML = '<div style="padding:40px;text-align:center;font-family:system-ui"><h2 style="color:#84cc16">✓ Calibration saved</h2><p>You can close this tab.</p></div>'; }, 600);
  } catch (e) {
    setStatus('Save failed.', 'err');
    setErr(String(e));
  }
};

// Initial layout
img.addEventListener('load', () => { fitToWrap(); renderRefBar(); updateHint(); setStatus(`Frame loaded (${NATURAL_W}×${NATURAL_H}).`); });
img.addEventListener('error', () => { setStatus('Failed to load frame.', 'err'); });
renderRefBar();
updateHint();
</script>
</body>
</html>
""".replace("__GAME_LABEL__", safe_label).replace("__FW__", str(frame_w)).replace("__FH__", str(frame_h)).replace("__FL__", str(field_length_m)).replace("__FWM__", str(field_width_m)).replace("__CAMH__", str(default_camera_height_m)).replace("__GW__", str(goal_width_m))


def calibrate_flat(game_id: str, at_seconds: float = 60.0,
                   field_length_m: float = 50.0,
                   field_width_m: float = 35.0,
                   goal_width_m: float = 4.88,
                   camera_height_m: float = 5.0,
                   map_length_m: float | None = None,
                   field_key: str | None = None) -> dict:
    """Extract a single equirect frame, serve a pan/zoom click viewer, save to Firestore.

    map_length_m: map-measured touchline length (the per-field scale anchor). When
        set, the saved calibration is solved with the field LENGTH fixed to it and
        the WIDTH recovered from the clicks (see _refine_with_tilt / solve_sphere_scaled),
        and the field's scale is persisted under field_key for reuse.
    field_key: short label for the field (dedupes the per-field scale store)."""
    game = firestore_io.get_game(game_id)
    if not game.video_url:
        raise RuntimeError(f"Game {game_id} has no videoUrl set.")

    # A map-measured length IS the field length — the browser lays out corners at
    # x=0..field_length_m, and the scaled solve fixes L to it.
    if map_length_m:
        field_length_m = float(map_length_m)

    tmp_dir = tempfile.mkdtemp(prefix="calib_flat_")
    frame_path = os.path.join(tmp_dir, "frame.jpg")
    _extract_frame(game.video_url, at_seconds, frame_path)

    # Probe the actual frame size via ffprobe so the page knows the natural pixel grid.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", frame_path],
        check=True, capture_output=True, text=True,
    )
    probe_json = json.loads(probe.stdout)
    fw = int(probe_json["streams"][0]["width"])
    fh = int(probe_json["streams"][0]["height"])
    log.info("Frame natural size: %dx%d", fw, fh)

    page_html = _build_page(
        game_label=f"{game_id} · vs {game.opponent or 'OPP'} · t={at_seconds:.0f}s",
        frame_w=fw, frame_h=fh,
        default_camera_height_m=camera_height_m,
        field_length_m=field_length_m,
        field_width_m=field_width_m,
        goal_width_m=goal_width_m,
    ).encode("utf-8")

    saved = {"payload": None, "error": None}
    done_event = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page_html)))
                self.end_headers()
                self.wfile.write(page_html)
            elif path == "/frame.jpg":
                try:
                    with open(frame_path, "rb") as f:
                        data = f.read()
                except OSError as e:
                    self.send_response(404); self.end_headers()
                    self.wfile.write(str(e).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            if urlparse(self.path).path != "/save":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
                # Inject the scale anchor server-side so _refine_with_tilt uses
                # the scaled solve (fix length, solve width) instead of tilt-only.
                if map_length_m:
                    payload["map_length_m"] = float(map_length_m)
                if field_key:
                    payload["field_key"] = field_key
                payload = _refine_with_tilt(payload)
                firestore_io.write_game_calibration(game_id, payload)
                # Persist per-field scale + link the game to its field, so the
                # map length is entered once and reused for every game here.
                # Also persist the SOLVED width so the QC gate can check that the
                # same field solves to a consistent width run-to-run. Only set a
                # source note when the field has none yet — never clobber a
                # specific note the coach set (e.g. "goal-to-goal (coach)").
                if field_key:
                    firestore_io.set_game_field(game_id, field_key)
                    if map_length_m:
                        solved_w = payload.get("width_m")
                        existing = firestore_io.get_field_scale(field_key)
                        src = "" if (existing and existing.get("source")) else "google-maps"
                        firestore_io.save_field_scale(
                            field_key, float(map_length_m), source=src,
                            width_m=float(solved_w) if solved_w is not None else None,
                        )
                saved["payload"] = payload
                resp = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                done_event.set()
            except Exception as e:
                saved["error"] = str(e)
                resp = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
    httpd.allow_reuse_address = True
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    url = f"http://localhost:{PORT}/"
    log.info("Flat calibration page: %s", url)
    print(f"\n  >>> Open {url} in your browser if it didn't open automatically.\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        while not done_event.is_set():
            time.sleep(0.25)
    finally:
        time.sleep(0.5)
        httpd.shutdown()
        httpd.server_close()

    if saved["error"]:
        raise RuntimeError(f"Calibration save failed: {saved['error']}")
    if not saved["payload"]:
        raise RuntimeError("Calibration aborted.")
    return saved["payload"]


def _main():
    p = argparse.ArgumentParser(description="Flat-image field calibration tool")
    p.add_argument("game_id")
    p.add_argument("--at", type=float, default=60.0,
                   help="Seconds into the video to grab the frame from (default 60).")
    p.add_argument("--length", type=float, default=50.0, help="Field length in meters (default 50).")
    p.add_argument("--width",  type=float, default=35.0, help="Field width in meters (default 35).")
    p.add_argument("--goal-width", type=float, default=4.88,
                   help="Goal mouth width in meters (default 4.88 = 16ft U10 goal).")
    p.add_argument("--cam-height", type=float, default=5.0,
                   help="Camera height above field in meters (default 5.0).")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    payload = calibrate_flat(
        args.game_id, at_seconds=args.at,
        field_length_m=args.length, field_width_m=args.width,
        goal_width_m=args.goal_width, camera_height_m=args.cam_height,
    )
    print("\nSaved calibration:")
    print(f"  reference_points: {len(payload.get('reference_points', []))}")
    print(f"  RMS: {payload.get('ground_similarity', {}).get('rms_m', 'n/a')} m")
    print(f"  video_frame:   {payload['video_frame_w']}x{payload['video_frame_h']}")


if __name__ == "__main__":
    _main()
