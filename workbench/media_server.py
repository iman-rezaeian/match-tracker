"""Tiny Range-capable HTTP server for the narration video.

Why this exists: every Streamlit-native way to serve a local file to the
player component is capped or buffered — the component asset route reads the
whole file into memory, and the /app/static route 404s anything over
MAX_APP_STATIC_FILE_SIZE (200 MB). The game reels are ~86 GB, so the workbench
runs this sidecar on a side port instead: single-range GET/HEAD with 206
responses, streamed in 256 KB chunks, rooted at one directory that only ever
holds hardlinks. Runs as a daemon thread inside the Streamlit process, so its
lifecycle is the app's. stdlib http.server deliberately does NOT support
Range, which is why this handler exists at all — <video> seeking needs it.
"""
from __future__ import annotations

import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class _RangeHandler(BaseHTTPRequestHandler):
    root: Path  # set by serve()

    def log_message(self, *a):  # keep the Streamlit console quiet
        pass

    def _target(self) -> Path | None:
        name = self.path.split("?")[0].lstrip("/")
        p = (self.root / name).resolve()
        # only files directly inside the media root; no traversal
        if p.parent != self.root.resolve() or not p.is_file():
            return None
        return p

    def do_HEAD(self):
        p = self._target()
        if not p:
            self.send_error(404)
            return
        size = p.stat().st_size
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        self.end_headers()

    def do_GET(self):
        p = self._target()
        if not p:
            self.send_error(404)
            return
        size = p.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = _RANGE_RE.match(rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):          # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with p.open("rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the <video> element aborts ranges constantly; normal


def serve(root: Path) -> int:
    """Start the daemon server rooted at `root`; returns the bound port."""
    root.mkdir(parents=True, exist_ok=True)
    handler = type("Handler", (_RangeHandler,), {"root": root})
    with socket.socket() as s:      # ask the OS for a free port
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name="narration-media-server").start()
    return port
