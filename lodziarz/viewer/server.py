"""Lokalny serwer HTTP: statyczne pliki viewera + API procesu.

Dziala w dwoch trybach:
  - GUI: okno pywebview wskazujace na serwer (dialogi natywne przez webview)
  - przegladarka: `lodziarz gui --browser` albo dowolny http://127.0.0.1:port
"""
from __future__ import annotations

import base64
import json
import re
import socket
import tempfile
import threading
import urllib.parse
from dataclasses import asdict
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
        self.result: dict | None = None      # {'kind': 'load'|'process', ...}
        self.out_root: Path | None = None    # katalog serwowany pod /out/
        # stan wczytanego assetu (krok "Wczytaj" przed "Process")
        self.asset = None
        self.asset_path: str = ""
        self.material_textures: dict = {}    # {material: {slot: sciezka}}
        self.workdir = Path(tempfile.mkdtemp(prefix="lodziarz_gui_"))


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
                    "result": STATE.result,
                })
            return
        if path.startswith("/out/"):
            self._serve_file_from(STATE.out_root, path[len("/out/"):])
            return
        if path.startswith("/work/"):
            self._serve_file_from(STATE.workdir, path[len("/work/"):])
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
        elif path == "/api/load":
            self._api_load(payload)
        elif path == "/api/reexport":
            self._api_reexport(payload)
        elif path == "/api/assign_texture":
            self._api_assign_texture(payload)
        elif path == "/api/dialog":
            self._api_dialog(payload)
        else:
            self._json({"error": "nieznane API"}, 404)

    def _api_load(self, p: dict):
        """Import assetu + preview GLB + lista materialow — bez przetwarzania."""
        with STATE.lock:
            if STATE.running:
                self._json({"error": "operacja juz trwa"}, 409)
                return
            input_path = Path(p.get("input", ""))
            if not input_path.is_file():
                self._json({"error": f"plik nie istnieje: {input_path}"}, 400)
                return
            up = str(p.get("up", "auto"))
            STATE.running = True
            STATE.log = PipelineLog(echo=True)
            STATE.result = None

        def work():
            log = STATE.log
            result: dict = {"kind": "load", "ok": False}
            try:
                from ..core import LodChain
                from ..exporter.glb import export_glb
                from ..importer import load_asset
                log.progress(10, "import")
                asset = load_asset(input_path, log, force_up=up)
                from ..report import validation_report
                validation_report(asset, log)
                log.progress(70, "preview")
                chain = LodChain(asset_name=asset.name, lods=[asset.mesh],
                                 materials=asset.materials,
                                 baked=[False], baked_material_index=None)
                export_glb(chain, STATE.workdir / "preview.glb", {}, log)
                mats = []
                for m in asset.materials:
                    mats.append({"name": m.name, "maps": {
                        "basecolor": m.base_color_tex is not None,
                        "normal": m.normal_tex is not None,
                        "occlusion": m.occlusion_tex is not None,
                        "roughness": m.roughness_tex is not None,
                        "metallic": m.metallic_tex is not None,
                        "emissive": m.emissive_tex is not None,
                        "opacity": m.opacity_tex is not None,
                    }})
                result.update(
                    ok=True, preview="/work/preview.glb",
                    materials=mats,
                    stats={"tris": int(asset.mesh.triangle_count),
                           "verts": int(asset.mesh.vertex_count)},
                    up_detected=asset.source_up_axis)
                # nowy plik = czyste przypisania map
                if STATE.asset_path != str(input_path):
                    STATE.material_textures = {}
                STATE.asset = asset
                STATE.asset_path = str(input_path)
                log.progress(100, "wczytano")
            except Exception as e:
                log.error(f"{type(e).__name__}: {e}")
                result["error"] = str(e)
            with STATE.lock:
                STATE.result = result
                STATE.running = False

        threading.Thread(target=work, daemon=True).start()
        self._json({"started": True})

    def _api_assign_texture(self, p: dict):
        """Zapis mapy z drag&drop do workdir + rejestracja przypisania."""
        mat = str(p.get("material", ""))
        slot = str(p.get("slot", ""))
        fname = str(p.get("filename", "tex.png"))
        data = p.get("data", "")
        if not mat or not slot or not data:
            self._json({"error": "brak material/slot/data"}, 400)
            return
        try:
            raw = base64.b64decode(data.split(",", 1)[-1])
        except Exception:
            self._json({"error": "zly base64"}, 400)
            return
        ext = Path(fname).suffix.lower() or ".png"
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{mat}_{slot}{ext}")
        target = STATE.workdir / "matmaps" / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        with STATE.lock:
            STATE.material_textures.setdefault(mat, {})[slot] = str(target)
        self._json({"ok": True, "path": str(target)})

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
            thr_raw = str(p.get("thresholds", "") or "").strip()
            lod_thresholds = None
            if thr_raw:
                try:
                    lod_thresholds = [float(x) for x in
                                      thr_raw.replace(";", ",").split(",")
                                      if x.strip()] or None
                except ValueError:
                    self._json({"error": f"zle progi LOD: '{thr_raw}' "
                                         f"(np. 500,1000,2000)"}, 400)
                    return
            opts = ProcessOptions(
                lod_count=max(1, min(8, int(p.get("lods", 4)))),
                lod_ratio=max(0.05, min(0.95, float(p.get("ratio", 0.5)))),
                smooth_weld=bool(p.get("smoothWeld", False)),
                lod_thresholds=lod_thresholds,
                bake=bool(p.get("bake", True)),
                baked_lods=[int(i) for i in p["bakedLods"]]
                    if isinstance(p.get("bakedLods"), list) else None,
                bake_backend=str(p.get("backend", "texel")),
                atlas_resolution=max(512, min(4096, int(p.get("atlas", 1024)))),
                dilation=max(0, min(64, int(p.get("dilation", 8)))),
                ssaa=int(p.get("ssaa", 2)) if int(p.get("ssaa", 2)) in (1, 2, 4) else 2,
                cage_offset=max(0.0, float(p.get("cageOffset", 0.0))),
                input_normal_directx=bool(p.get("inputNormalDx", True)),
                output_normal_directx=bool(p.get("outputNormalDx", True)),
                texture_format=str(p.get("texFormat", "png")),
                orm_split=str(p.get("ormMode", "packed")) != "packed",
                output_gloss=str(p.get("ormMode", "packed")) == "split_gloss",
                fbx_per_lod=bool(p.get("perLodFbx", False)),
                fbx_embed_textures=bool(p.get("embedTextures", False)),
                fbx_flat_lods=bool(p.get("flatFbx", False)),
                export_fbx=bool(p.get("exportFbx", True)),
                export_glb=bool(p.get("exportGlb", True)),
                export_obj=bool(p.get("exportObj", False)),
                up_axis=str(p.get("up", "auto")),
                material_textures=dict(STATE.material_textures)
                    if STATE.material_textures
                       and STATE.asset_path == str(input_path) else None,
            )
            STATE.running = True
            STATE.log = PipelineLog(echo=True)
            STATE.result = None
            STATE.out_root = out_dir

        def work():
            # subprocess: crash natywny (ufbx/GL/FBX SDK) nie zabija GUI
            result = run_isolated(input_path, out_dir, opts, STATE.log)
            with STATE.lock:
                STATE.result = {"kind": "process", **asdict(result)}
                STATE.running = False

        threading.Thread(target=work, daemon=True).start()
        self._json({"started": True})

    def _api_reexport(self, p: dict):
        """Re-export FBX z nowa maska bake per LOD — bez ponownego bake.

        Czyta cache (oba sety LOD-ow) zapisany przez pipeline w folderze
        wyjsciowym; maska z viewera wybiera set per LOD."""
        with STATE.lock:
            if STATE.running:
                self._json({"error": "operacja juz trwa"}, 409)
                return
            if STATE.out_root is None:
                self._json({"error": "brak wyniku procesu w tej sesji"}, 400)
                return
            cache_name = Path(str(p.get("cache", ""))).name  # bez sciezek
            cache_path = STATE.out_root / cache_name
            if not cache_name or not cache_path.is_file():
                self._json({"error": f"brak cache: {cache_name}"}, 400)
                return
            mask_req = [bool(x) for x in p.get("mask", [])]
            STATE.running = True
            STATE.log = PipelineLog(echo=True)
            STATE.result = None
        out_root = STATE.out_root

        def work():
            log = STATE.log
            result: dict = {"kind": "reexport", "ok": False}
            try:
                import numpy as np
                from PIL import Image

                from ..cache import load_cache
                from ..core import LodChain, MaterialData
                from ..exporter.fbx import (export_fbx_lodgroup,
                                            export_fbx_per_lod)
                from ..exporter.glb import export_glb
                log.progress(10, "wczytywanie cache")
                data = load_cache(cache_path)
                baked_lods = data["baked_lods"]
                orig_lods = data["orig_lods"]
                orig_materials = data["materials"]
                count = len(baked_lods)
                mask = (mask_req + [False] * count)[:count]
                name = data["asset_name"]
                log.info("re-export, bake dla: "
                         + (", ".join(f"LOD{i}" for i, b in enumerate(mask) if b)
                            or "—"))

                if all(mask):
                    materials = [MaterialData(name=f"M_{name}")]
                    baked_idx = 0
                elif any(mask):
                    materials = list(orig_materials)
                    baked_idx = len(materials)
                    materials.append(MaterialData(name=f"M_{name}"))
                else:
                    materials = list(orig_materials)
                    baked_idx = None
                lods = []
                for i in range(count):
                    m = baked_lods[i] if mask[i] else orig_lods[i]
                    if mask[i]:
                        m.tri_material = np.full(len(m.indices), baked_idx,
                                                 dtype=np.int32)
                    lods.append(m)
                chain = LodChain(asset_name=name, lods=lods,
                                 materials=materials, baked=mask,
                                 baked_material_index=baked_idx)

                formats = data.get("formats",
                                   {"fbx": True, "glb": True, "obj": False})
                result.update(ok=True, baked_mask=list(mask))
                if formats.get("fbx", True):
                    log.progress(40, "export FBX")
                    fbx_path = out_root / f"{name}.fbx"
                    export_fbx_lodgroup(chain, fbx_path,
                                        data["texture_files"], log,
                                        embed=data.get("embed", False),
                                        thresholds=data.get("thresholds"),
                                        flat=data.get("flat", False))
                    result["fbx"] = fbx_path.name
                # per LOD niezaleznie od pliku zbiorczego (preset max/loose)
                if data.get("per_lod"):
                    log.progress(55, "export FBX per LOD")
                    paths = export_fbx_per_lod(
                        chain, out_root, data["texture_files"], log,
                        embed=data.get("embed", False))
                    result["fbx_per_lod"] = [pp.name for pp in paths]

                if formats.get("obj", False):
                    log.progress(70, "export OBJ")
                    from ..exporter.obj import export_obj
                    paths = export_obj(chain, out_root,
                                       data["texture_files"], log)
                    result["obj"] = [pp.name for pp in paths]

                if formats.get("glb", True):
                    # tekstury atlasu z plikow na dysku (konwencja wyjsciowa)
                    log.progress(80, "export GLB")
                    images = {}
                    for key, fname in data["texture_files"].items():
                        fpath = out_root / fname
                        if fpath.is_file():
                            images[key] = Image.open(fpath)
                    if "orm" not in images and \
                            any(k in images for k in ("ao", "roughness",
                                                      "glossiness", "metallic")):
                        # mapy byly zapisane osobno — spakuj z powrotem do ORM
                        ref = next(images[k] for k in ("ao", "roughness",
                                                       "glossiness", "metallic")
                                   if k in images)
                        size = ref.size
                        orm = np.zeros((size[1], size[0], 3), dtype=np.uint8)
                        orm[:, :, 0] = np.asarray(
                            images["ao"].convert("L").resize(size)) \
                            if "ao" in images else 255
                        if "roughness" in images:
                            orm[:, :, 1] = np.asarray(
                                images["roughness"].convert("L").resize(size))
                        elif "glossiness" in images:
                            orm[:, :, 1] = 255 - np.asarray(
                                images["glossiness"].convert("L").resize(size))
                        if "metallic" in images:
                            orm[:, :, 2] = np.asarray(
                                images["metallic"].convert("L").resize(size))
                        images["orm"] = Image.fromarray(orm, "RGB")
                    glb_path = out_root / f"{name}.glb"
                    export_glb(chain, glb_path, images, log)
                    result["glb"] = glb_path.name

                # manifest — zapis nowej maski
                manifest = out_root / f"{name}.lodziarz.json"
                if manifest.is_file():
                    try:
                        mdata = json.loads(manifest.read_text(encoding="utf-8"))
                        mdata["baked_mask"] = list(mask)
                        manifest.write_text(json.dumps(mdata, indent=2),
                                            encoding="utf-8")
                    except (json.JSONDecodeError, OSError):
                        pass
                log.progress(100, "gotowe")
            except Exception as e:
                log.error(f"{type(e).__name__}: {e}")
                result["error"] = str(e)
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
