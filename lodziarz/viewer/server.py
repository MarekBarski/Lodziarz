"""Lokalny serwer HTTP: statyczne pliki viewera + API procesu.

Dziala w dwoch trybach:
  - GUI: okno pywebview wskazujace na serwer (dialogi natywne przez webview)
  - przegladarka: `lodziarz gui --browser` albo dowolny http://127.0.0.1:port
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..core import resource_dir
from ..logutil import PipelineLog
from ..pipeline import ProcessOptions, ProcessResult
from ..worker import run_isolated

WEB_DIR = resource_dir() / "viewer" / "web"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".tga": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.log: PipelineLog | None = None
        self.result: ProcessResult | None = None
        self.out_root: Path | None = None   # katalog serwowany pod /out/


STATE = AppState()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # cicho — log HTTP nie interesuje uzytkownika

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode("utf-8"))

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/progress":
            with STATE.lock:
                log = STATE.log
                self._json({
                    "running": STATE.running,
                    "percent": log.percent if log else 0,
                    "stage": log.stage if log else "",
                    "log": log.lines if log else [],
                    "result": STATE.result.__dict__ if STATE.result else None,
                })
            return
        if path.startswith("/out/"):
            self._serve_file_from(STATE.out_root, path[len("/out/"):])
            return
        if path == "/":
            path = "/index.html"
        self._serve_file_from(WEB_DIR, path.lstrip("/"))

    def _serve_file_from(self, root: Path | None, rel: str):
        if root is None:
            self._send(404, b"no output dir", "text/plain")
            return
        rel = urllib.parse.unquote(rel)
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self._send(403, b"forbidden", "text/plain")
            return
        if not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = MIME.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "zly JSON"}, 400)
            return
        if path == "/api/process":
            self._api_process(payload)
        elif path == "/api/dialog":
            self._api_dialog(payload)
        else:
            self._json({"error": "nieznane API"}, 404)

    def _api_process(self, p: dict):
        with STATE.lock:
            if STATE.running:
                self._json({"error": "proces juz trwa"}, 409)
                return
            input_path = Path(p.get("input", ""))
            out_dir = Path(p.get("out", ""))
            if not input_path.is_file():
                self._json({"error": f"plik nie istnieje: {input_path}"}, 400)
                return
            if not str(out_dir):
                self._json({"error": "podaj folder wyjsciowy"}, 400)
                return
            opts = ProcessOptions(
                lod_count=max(1, min(8, int(p.get("lods", 4)))),
                lod_ratio=max(0.05, min(0.95, float(p.get("ratio", 0.5)))),
                bake=bool(p.get("bake", True)),
                bake_from_lod=max(0, min(7, int(p.get("bakeFromLod", 0)))),
                bake_backend=str(p.get("backend", "texel")),
                atlas_resolution=max(512, min(4096, int(p.get("atlas", 2048)))),
                dilation=max(0, min(64, int(p.get("dilation", 8)))),
                ssaa=int(p.get("ssaa", 2)) if int(p.get("ssaa", 2)) in (1, 2, 4) else 2,
                input_normal_directx=bool(p.get("inputNormalDx", False)),
                output_normal_directx=bool(p.get("outputNormalDx", False)),
                texture_format=str(p.get("texFormat", "png")),
                fbx_per_lod=bool(p.get("perLodFbx", False)),
                export_glb=True,  # viewer potrzebuje GLB
            )
            STATE.running = True
            STATE.log = PipelineLog(echo=True)
            STATE.result = None
            STATE.out_root = out_dir

        def work():
            # subprocess: crash natywny (ufbx/GL/FBX SDK) nie zabija GUI
            result = run_isolated(input_path, out_dir, opts, STATE.log)
            with STATE.lock:
                STATE.result = result
                STATE.running = False

        threading.Thread(target=work, daemon=True).start()
        self._json({"started": True})

    def _api_dialog(self, p: dict):
        """Natywne dialogi — tylko w trybie pywebview."""
        try:
            import webview
            if not webview.windows:
                raise RuntimeError("brak okna")
            win = webview.windows[0]
            kind = p.get("kind", "open")
            if kind == "open":
                res = win.create_file_dialog(
                    webview.OPEN_DIALOG, allow_multiple=False,
                    file_types=("Modele 3D (*.fbx;*.obj;*.gltf;*.glb)",
                                "Wszystkie pliki (*.*)"))
            elif kind == "folder":
                res = win.create_file_dialog(webview.FOLDER_DIALOG)
            else:
                res = None
            path = ""
            if res:
                path = res[0] if isinstance(res, (list, tuple)) else str(res)
            self._json({"path": path})
        except Exception as e:
            self._json({"path": "", "error": str(e)})


def start_server(port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    port = port or _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def run_gui(browser: bool = False) -> int:
    httpd, port = start_server()
    url = f"http://127.0.0.1:{port}/"
    print(f"Lodziarz viewer: {url}")
    if browser:
        import webbrowser
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0
    import webview
    webview.create_window("Lodziarz — LOD + Bake + Viewer", url,
                          width=1500, height=950, min_size=(1100, 700))
    webview.start()
    httpd.shutdown()
    return 0
