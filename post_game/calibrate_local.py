"""DEPRECATED — 3D-sphere 4-corner field-calibration tool.

⚠️  Do NOT use for new calibrations. Use `post_game.calibrate_flat`
    instead (CLI: `python -m post_game.cli calibrate --game-id <id>`).

This tool's planar-homography output is physically wrong for X5
equirectangular footage: touchlines project as sphere-curves in
equirect, not straight lines between corner pixels, so the 4-corner
homography misaligns by several meters across the field.

The replacement (`calibrate_flat.py`) clicks 13 landmarks on a flat
frame, ray-traces each click to the ground plane on the unit sphere,
and fits a 2D similarity + camera pitch/roll. Sub-meter accuracy on
the near half of the pitch.

Kept here only for backward reference. Will be removed in a future
cleanup once no calibration in Firestore still uses the legacy
homography_flat schema.
"""

from __future__ import annotations

import http.server
import json
import logging
import socketserver
import threading
import time
import webbrowser
from typing import Optional
from urllib.parse import urlparse

from . import firestore_io

log = logging.getLogger(__name__)

PORT = 8765


def _build_page(video_url: str, game_label: str) -> str:
    # Single inline HTML page with the calibration sphere viewer.
    # Mirrors soccer_team_app.jsx::FieldCalibrationModal but plain JS.
    safe_url = json.dumps(video_url)
    safe_label = json.dumps(game_label)
    return r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Field Calibration</title>
<style>
  :root { color-scheme: dark; }
  html, body { height:100%; }
  body { margin:0; background:#0c0a09; color:#e7e5e4; font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif; display:flex; flex-direction:column; overflow:hidden; }
  header { padding:10px 14px; border-bottom:1px solid #292524; display:flex; gap:12px; align-items:center; flex:0 0 auto; }
  header h1 { margin:0; font-size:14px; letter-spacing:.04em; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#1c1917; color:#a8a29e; font-size:11px; }
  #status { margin-left:auto; font-size:12px; color:#84cc16; }
  #status.warn { color:#fbbf24; }
  #status.err { color:#f87171; }
  #wrap { display:flex; flex-direction:column; flex:1 1 auto; min-height:0; }
  #viewer { flex:1; position:relative; background:#0a0a0a; touch-action:none; user-select:none; }
  #hint { padding:6px 14px; font-size:12px; color:#a8a29e; border-bottom:1px solid #292524; display:flex; gap:10px; align-items:center; flex-wrap:wrap; flex:0 0 auto; }
  #hint strong { color:#84cc16; }
  .seg { display:inline-flex; background:#1c1917; border:1px solid #44403c; border-radius:999px; padding:2px; font-size:11px; }
  .seg button { background:transparent; border:0; color:#d6d3d1; padding:4px 10px; border-radius:999px; cursor:pointer; }
  .seg button.on { background:#84cc16; color:#0c0a09; font-weight:600; }
  footer { padding:10px 14px; border-top:1px solid #292524; display:flex; gap:8px; align-items:center; flex:0 0 auto; background:#0c0a09; }
  footer button { flex:1; padding:10px; border-radius:8px; border:0; cursor:pointer; font-weight:600; }
  .btn-secondary { background:#292524; color:#d6d3d1; }
  .btn-primary { background:#84cc16; color:#0c0a09; }
  .btn-primary:disabled { opacity:.35; cursor:not-allowed; }
  #err { color:#f87171; font-size:12px; padding:0 14px 8px; }
</style>
</head>
<body>
  <header>
    <h1>🎯 FIELD CALIBRATION</h1>
    <span class="pill" id="gameLabel"></span>
    <span id="status">Ready.</span>
  </header>
  <div id="hint">
    <span id="hintText">Loading 360° view…</span>
    <span class="seg">
      <button id="lensFront" class="on">FRONT</button>
      <button id="lensBack">BACK</button>
    </span>
    <span style="color:#78716c;">drag to rotate · pinch/scroll to zoom · click to place a pin</span>
  </div>
  <div id="wrap">
    <div id="viewer"></div>
  </div>
  <div id="err"></div>
  <footer>
    <button class="btn-secondary" id="undoBtn">UNDO</button>
    <button class="btn-secondary" id="resetBtn">RESET</button>
    <button class="btn-primary" id="saveBtn" disabled>SAVE CALIBRATION (0/4)</button>
  </footer>

<script>
const VIDEO_URL = __VIDEO_URL__;
const GAME_LABEL = __GAME_LABEL__;
document.getElementById('gameLabel').textContent = GAME_LABEL;

const CORNER_LABELS = ['FAR-LEFT', 'FAR-RIGHT', 'NEAR-RIGHT', 'NEAR-LEFT'];

function setStatus(msg, cls) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = cls || '';
}
function setErr(msg) { document.getElementById('err').textContent = msg || ''; }

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src; s.onload = resolve; s.onerror = reject;
    document.head.appendChild(s);
  });
}

async function attachHls(video, url) {
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    return () => { video.removeAttribute('src'); video.load(); };
  }
  await loadScript('https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js');
  if (!window.Hls || !window.Hls.isSupported()) { video.src = url; return () => {}; }
  const hls = new window.Hls({ lowLatencyMode: false });
  hls.loadSource(url);
  hls.attachMedia(video);
  return () => { try { hls.destroy(); } catch(e) {} };
}

function solveHomography(src, dst) {
  // Direct linear transform — mirror of solveHomography4Point in JSX.
  const A = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i]; const [u, v] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u*x, -u*y, -u]);
    A.push([0, 0, 0, x, y, 1, -v*x, -v*y, -v]);
  }
  // SVD-free solve: use 8x8 system from first 8 rows (sufficient — well-posed).
  // Build 8x9 then row-reduce smallest singular vector via QR-ish elimination.
  // To keep this self-contained we solve A^T A v = 0 via power-iteration on
  // (I - A^T A / ||A^T A||) — but easier: use numerical inversion of the 8x8
  // formed by fixing h33 = 1.
  const M = []; const b = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i]; const [u, v] = dst[i];
    M.push([x, y, 1, 0, 0, 0, -u*x, -u*y]); b.push(u);
    M.push([0, 0, 0, x, y, 1, -v*x, -v*y]); b.push(v);
  }
  // 8x8 Gaussian elimination
  const n = 8;
  for (let i = 0; i < n; i++) {
    // Pivot
    let maxR = i;
    for (let r = i+1; r < n; r++) if (Math.abs(M[r][i]) > Math.abs(M[maxR][i])) maxR = r;
    if (maxR !== i) { [M[i], M[maxR]] = [M[maxR], M[i]]; [b[i], b[maxR]] = [b[maxR], b[i]]; }
    if (Math.abs(M[i][i]) < 1e-12) return null;
    for (let r = i+1; r < n; r++) {
      const f = M[r][i] / M[i][i];
      for (let c = i; c < n; c++) M[r][c] -= f * M[i][c];
      b[r] -= f * b[i];
    }
  }
  const h = new Array(n);
  for (let i = n-1; i >= 0; i--) {
    let s = b[i];
    for (let c = i+1; c < n; c++) s -= M[i][c] * h[c];
    h[i] = s / M[i][i];
  }
  return [[h[0],h[1],h[2]],[h[3],h[4],h[5]],[h[6],h[7],1]];
}

let THREE, renderer, scene, camera, video, texture, geometry, container;
let pins = []; // [{ sprite, px, py }]
let naturalW = 0, naturalH = 0;
const state = { yaw: 0, pitch: -0.25, fov: 90, targetYaw: 0, targetPitch: -0.25, targetFov: 90 };
let lensFront = true;
let raf = 0;

function updateHint() {
  const n = pins.length;
  const t = n < 4
    ? `Tap the <strong>${CORNER_LABELS[n]}</strong> corner. (${n}/4)`
    : '<strong style="color:#84cc16">All 4 corners marked.</strong> SAVE below.';
  document.getElementById('hintText').innerHTML = t;
  const btn = document.getElementById('saveBtn');
  btn.disabled = n !== 4;
  btn.textContent = n === 4 ? 'SAVE CALIBRATION' : `SAVE CALIBRATION (${n}/4)`;
}

function makePinSprite(idx) {
  const c = document.createElement('canvas'); c.width = 64; c.height = 64;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#84cc16'; ctx.strokeStyle = '#0c0a09'; ctx.lineWidth = 5;
  ctx.beginPath(); ctx.arc(32,32,24,0,Math.PI*2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#0c0a09'; ctx.font = 'bold 30px system-ui'; ctx.textAlign='center'; ctx.textBaseline='middle';
  ctx.fillText(String(idx), 32, 35);
  const tex = new THREE.CanvasTexture(c);
  const m = new THREE.SpriteMaterial({ map: tex, depthTest: false, depthWrite: false, transparent: true });
  const s = new THREE.Sprite(m); s.scale.set(28,28,1); s.renderOrder = 999;
  return s;
}

function placePinAtClient(clientX, clientY) {
  if (pins.length >= 4) return;
  const rect = container.getBoundingClientRect();
  const mx = ((clientX - rect.left) / rect.width) * 2 - 1;
  const my = -(((clientY - rect.top) / rect.height) * 2 - 1);
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera({ x: mx, y: my }, camera);
  const sphere = scene.children.find(o => o.isMesh);
  const hits = raycaster.intersectObject(sphere, false);
  if (!hits.length) return;
  const p = hits[0].point.clone().normalize();
  const lon = Math.atan2(p.x, -p.z);
  const lat = Math.asin(Math.max(-1, Math.min(1, p.y)));
  const px = ((lon + Math.PI) / (2 * Math.PI)) * naturalW;
  const py = ((Math.PI/2 - lat) / Math.PI) * naturalH;
  const sprite = makePinSprite(pins.length + 1);
  sprite.position.copy(hits[0].point.clone().multiplyScalar(0.99));
  scene.add(sprite);
  pins.push({ sprite, px, py });
  updateHint();
}

function undo() {
  const last = pins.pop(); if (!last) return;
  scene.remove(last.sprite);
  updateHint();
}
function reset() {
  pins.forEach(p => scene.remove(p.sprite));
  pins = [];
  updateHint();
}

async function init() {
  try {
    await loadScript('https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js');
    THREE = window.THREE;
    container = document.getElementById('viewer');
    const W = container.clientWidth, H = container.clientHeight;

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    container.appendChild(renderer.domElement);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(state.fov, W/H, 0.1, 1100);
    camera.position.set(0,0,0.01);

    geometry = new THREE.SphereGeometry(500, 64, 40);
    geometry.scale(-1,1,1);

    video = document.createElement('video');
    video.crossOrigin = 'anonymous';
    video.playsInline = true;
    video.muted = true;
    video.preload = 'auto';
    const isHls = /\.m3u8(\?|$)/i.test(VIDEO_URL);
    if (isHls) { await attachHls(video, VIDEO_URL); } else { video.src = VIDEO_URL; }

    texture = new THREE.VideoTexture(video);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.minFilter = THREE.LinearFilter;
    texture.magFilter = THREE.LinearFilter;
    const mat = new THREE.MeshBasicMaterial({ map: texture });
    scene.add(new THREE.Mesh(geometry, mat));

    video.addEventListener('loadedmetadata', () => {
      naturalW = video.videoWidth; naturalH = video.videoHeight;
      video.play().catch(()=>{});
      setStatus(`Video loaded (${naturalW}×${naturalH}).`);
    });
    video.addEventListener('error', () => {
      setStatus('Video failed to load.', 'err');
      setErr('Could not load video. Check the URL or CORS settings on the bucket.');
    });
    video.load();

    // Pointer handling
    let dragLast = null;
    const pointers = new Map();
    let pinchStartDist = 0, pinchStartFov = 75;
    let pressX = 0, pressY = 0, pressT = 0;

    container.addEventListener('pointerdown', (e) => {
      container.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1) {
        dragLast = { x: e.clientX, y: e.clientY };
        pressX = e.clientX; pressY = e.clientY; pressT = performance.now();
      } else if (pointers.size === 2) {
        const pts = [...pointers.values()];
        pinchStartDist = Math.hypot(pts[0].x-pts[1].x, pts[0].y-pts[1].y);
        pinchStartFov = state.targetFov;
      }
    });
    container.addEventListener('pointermove', (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1 && dragLast) {
        const dx = e.clientX - dragLast.x, dy = e.clientY - dragLast.y;
        // Sensitivity scales with FOV (zoomed in = finer control). Larger
        // divisor = slower. 5000 ~ comfortable for trackpad + mouse.
        const sens = state.targetFov / 5000;
        state.targetYaw -= dx * sens;
        state.targetPitch = Math.max(-Math.PI/2+0.05, Math.min(Math.PI/2-0.05, state.targetPitch - dy * sens));
        dragLast = { x: e.clientX, y: e.clientY };
      } else if (pointers.size === 2) {
        const pts = [...pointers.values()];
        const d = Math.hypot(pts[0].x-pts[1].x, pts[0].y-pts[1].y);
        if (pinchStartDist > 0) {
          state.targetFov = Math.max(20, Math.min(110, pinchStartFov * (pinchStartDist / d)));
        }
      }
    });
    container.addEventListener('pointerup', (e) => {
      const wasOne = pointers.size === 1;
      pointers.delete(e.pointerId);
      if (wasOne) {
        const dx = e.clientX - pressX, dy = e.clientY - pressY;
        const dt = performance.now() - pressT;
        if (Math.hypot(dx, dy) < 8 && dt < 400) placePinAtClient(e.clientX, e.clientY);
        dragLast = null;
      }
    });
    container.addEventListener('wheel', (e) => {
      e.preventDefault();
      state.targetFov = Math.max(20, Math.min(110, state.targetFov + e.deltaY * 0.05));
    }, { passive: false });

    window.addEventListener('resize', () => {
      const w = container.clientWidth, h = container.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w/h; camera.updateProjectionMatrix();
    });

    const tick = () => {
      raf = requestAnimationFrame(tick);
      state.yaw   += (state.targetYaw   - state.yaw)   * 0.18;
      state.pitch += (state.targetPitch - state.pitch) * 0.18;
      state.fov   += (state.targetFov   - state.fov)   * 0.20;
      camera.fov = state.fov; camera.updateProjectionMatrix();
      const yawOffset = lensFront ? 0 : Math.PI;
      const y = state.yaw + yawOffset;
      const cp = Math.cos(state.pitch), sp = Math.sin(state.pitch);
      const dir = new THREE.Vector3(Math.sin(y)*cp, sp, -Math.cos(y)*cp);
      camera.lookAt(dir);
      renderer.render(scene, camera);
    };
    tick();
    updateHint();
  } catch (e) {
    setStatus('Init failed.', 'err');
    setErr(String(e));
  }
}

document.getElementById('lensFront').onclick = () => {
  lensFront = true;
  document.getElementById('lensFront').classList.add('on');
  document.getElementById('lensBack').classList.remove('on');
  state.targetYaw = 0;
};
document.getElementById('lensBack').onclick = () => {
  lensFront = false;
  document.getElementById('lensBack').classList.add('on');
  document.getElementById('lensFront').classList.remove('on');
  state.targetYaw = 0;
};
document.getElementById('undoBtn').onclick = undo;
document.getElementById('resetBtn').onclick = reset;
document.getElementById('saveBtn').onclick = async () => {
  if (pins.length !== 4) return;
  setStatus('Saving…');
  setErr('');
  const LENGTH_M = 50, WIDTH_M = 35;
  const src = pins.map(p => [p.px, p.py]);
  const dst = [[0,0],[LENGTH_M,0],[LENGTH_M,WIDTH_M],[0,WIDTH_M]];
  const H = solveHomography(src, dst);
  if (!H) { setStatus('Bad corners.', 'err'); setErr('Could not solve homography. Try re-placing the corners.'); return; }
  const payload = {
    length_m: LENGTH_M, width_m: WIDTH_M,
    src_points_px: { p0:{x:src[0][0],y:src[0][1]}, p1:{x:src[1][0],y:src[1][1]}, p2:{x:src[2][0],y:src[2][1]}, p3:{x:src[3][0],y:src[3][1]} },
    dst_points_m:  { p0:{x:dst[0][0],y:dst[0][1]}, p1:{x:dst[1][0],y:dst[1][1]}, p2:{x:dst[2][0],y:dst[2][1]}, p3:{x:dst[3][0],y:dst[3][1]} },
    homography_flat: [H[0][0],H[0][1],H[0][2],H[1][0],H[1][1],H[1][2],H[2][0],H[2][1],H[2][2]],
    video_frame_w: naturalW, video_frame_h: naturalH,
    created_at: Date.now(),
  };
  try {
    const r = await fetch('/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
    setStatus('Saved! Returning to terminal…');
    document.getElementById('saveBtn').disabled = true;
    setTimeout(() => { document.body.innerHTML = '<div style="padding:40px;text-align:center;font-family:system-ui"><h2 style="color:#84cc16">✓ Calibration saved</h2><p>You can close this tab. The pipeline is running.</p></div>'; }, 800);
  } catch (e) {
    setStatus('Save failed.', 'err');
    setErr(String(e));
  }
};

init();
</script>
</body>
</html>
""".replace("__VIDEO_URL__", safe_url).replace("__GAME_LABEL__", safe_label)


def calibrate_in_browser(game_id: str) -> dict:
    """Open the calibration page in the user's browser, wait until they save,
    then return the calibration dict that was written to Firestore.

    Blocks until either a successful save or the server is killed.
    """
    game = firestore_io.get_game(game_id)
    if not game.video_url:
        raise RuntimeError(f"Game {game_id} has no videoUrl set.")

    # Resolve the URL the browser should use. file:// videos can't be loaded
    # cross-origin into an http://localhost page, so for local files we serve
    # them through this same HTTP server at /video.
    raw_url = game.video_url
    local_video_path: Optional[str] = None
    if raw_url.startswith("file://"):
        local_video_path = raw_url[len("file://"):]
        browser_video_url = "/video"
    elif not raw_url.startswith(("http://", "https://")):
        local_video_path = raw_url
        browser_video_url = "/video"
    else:
        browser_video_url = raw_url

    page_html = _build_page(
        video_url=browser_video_url,
        game_label=f"{game_id} · vs {game.opponent or 'OPP'}",
    ).encode("utf-8")

    saved = {"payload": None, "error": None}
    done_event = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence default access log noise
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
            elif path == "/video" and local_video_path:
                self._serve_local_video(local_video_path)
            else:
                self.send_response(404); self.end_headers()

        def _serve_local_video(self, file_path: str):
            try:
                import os
                size = os.path.getsize(file_path)
            except OSError as e:
                self.send_response(404); self.end_headers()
                self.wfile.write(f"video not found: {e}".encode("utf-8"))
                return
            range_header = self.headers.get("Range")
            start = 0
            end = size - 1
            status = 200
            if range_header and range_header.startswith("bytes="):
                try:
                    rng = range_header[len("bytes="):].strip()
                    s, e = rng.split("-", 1)
                    if s:
                        start = int(s)
                    if e:
                        end = min(int(e), size - 1)
                    status = 206
                except Exception:
                    start, end, status = 0, size - 1, 200
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    chunk = 1024 * 1024
                    while remaining > 0:
                        buf = f.read(min(chunk, remaining))
                        if not buf:
                            break
                        self.wfile.write(buf)
                        remaining -= len(buf)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if urlparse(self.path).path != "/save":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
                # Write directly to the game doc under .calibration
                firestore_io.write_game_calibration(game_id, payload)
                saved["payload"] = payload
                resp = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                # Signal main thread that we're done.
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
    log.info("Opening calibration page in your browser: %s", url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    log.info("Tap the 4 field corners, then click SAVE CALIBRATION.")
    log.info("(Or open the URL above manually if a browser tab didn't appear.)")

    try:
        # Wait until the page POSTs. No timeout — coach takes as long as needed.
        while not done_event.is_set():
            time.sleep(0.25)
    finally:
        # Give the browser a moment to receive the response, then shut down.
        time.sleep(0.5)
        httpd.shutdown()
        httpd.server_close()

    if saved["error"]:
        raise RuntimeError(f"Calibration save failed: {saved['error']}")
    if not saved["payload"]:
        raise RuntimeError("Calibration aborted.")
    return saved["payload"]
