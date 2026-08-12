# Lodziarz

Standalone narzędzie desktopowe (Windows) do przygotowywania game-ready assetów:
**LOD-y + bake materiałów do jednego atlasu + viewer**. Zero zależności od
Blendera i innych DCC — jeden portable `.exe`.

## Pipeline

1. **IMPORT** — FBX (ufbx), OBJ / glTF / GLB (trimesh). Transformy node'ów
   wypalane w geometrię, jednostki normalizowane do metrów.
2. **BAKE** (opcjonalny, na LOD0):
   - scalenie wszystkich materiałów obiektu w 1 materiał
   - nowy UV: unwrap + packing wysp przez **xatlas**
   - bake w texel space (**moderngl**, offscreen GPU): rasteryzacja nowego UV,
     per texel sampling starych tekstur; normalki re-enkodowane
     stary tangent space → world → nowy tangent space
   - mapy: **BaseColor, Normal, ORM** (Occlusion=R, Roughness=G, Metallic=B),
     opcjonalnie **Emissive** i **Opacity** (szklo itp. — z alfa base color /
     mapy opacity; w GLB laduje w alfie base color, osobny plik `T_*_Opacity`)
   - antyaliasing: supersampling off / 2× / 4×, downsampling box-filtrem
     ważonym pokryciem; **dilation po downsamplingu** (default 8 px)
   - rozdzielczość atlasu 512–4096
   - debug: `debug/*_uv_layout.png`, `debug/*_coverage.png`
   - architektura: wymienne backendy (`BakeBackend`) — `texel` (pełny),
     `raycast` i `cameras26` jako stuby z TODO
3. **LOD-y** — meshoptimizer `simplifyWithAttributes` (zachowuje UV i normale).
   Liczba LOD-ów i ratio parametrami (default 4 × 0.5^n). LOD-y dziedziczą
   atlas z LOD0 — bake robiony RAZ.
4. **EXPORT**:
   - **FBX one-pass**: node LODGroup `<Nazwa>` + dzieci `<Nazwa>_LOD0..N`
     (Autodesk FBX SDK — ten sam mechanizm co w Brutgenie; UE importuje
     całość jednym plikiem z *Import Mesh LODs*)
   - tryb alternatywny: `SM_<Nazwa>_LOD0.fbx`, `SM_<Nazwa>_LOD1.fbx`, ...
   - dodatkowo **GLB** (wszystkie LOD-y jako nody — używany przez viewer)
   - tekstury PNG (opcjonalnie TGA) obok plików

## Użycie

```
Lodziarz.exe                          GUI z viewerem (okno natywne)
Lodziarz.exe gui --browser            GUI w przeglądarce
Lodziarz.exe process IN -o OUT        batch CLI
```

CLI:

```
Lodziarz.exe process model.fbx -o out\model --lods 4 --ratio 0.5 --atlas 2048
Lodziarz.exe process folder_z_modelami -o out --no-bake --per-lod-fbx
opcje: --lods N --ratio R --no-bake --backend texel|raycast|cameras26
       --atlas 512..4096 --dilation PX --ssaa 1|2|4 --tga
       --normal-dx --input-normal-dx --per-lod-fbx --no-glb
```

## Viewer

- przełączanie LOD-ów (przyciski / klawisze 0–9) + tri/verts count
- sloty map — drag&drop podmiana (PNG/JPG/WebP):
  - packed: BaseColor / Normal / **ORM pack** / Emissive / **Opacity**
  - osobno: **AO / Roughness / Metallic / Glossiness** (gloss automatycznie
    odwracany do roughness)
- przełącznik konwencji normalki DirectX / OpenGL (flip G)
- tryby: Lit, Wireframe, BaseColor, Normal, AO, Roughness, Metallic, Opacity,
  **UV checker (po bake / oryginalne)**, **UV flat (po bake / oryginalne)** —
  oryginalne UV jadą w GLB jako TEXCOORD_1
- drag&drop pliku GLB wprost na viewport
- orbit camera, env IBL (RoomEnvironment) + directional

## Build ze źródeł

```powershell
python -m venv .venv
.venv\Scripts\pip install numpy Pillow scipy trimesh ufbx xatlas moderngl pywebview pyinstaller pygltflib
powershell -File native\build_native.ps1     # fbx_writer.exe + meshoptimizer.dll (wymaga FBX SDK + VS BuildTools)
.venv\Scripts\pyinstaller lodziarz.spec --noconfirm   # -> dist\Lodziarz.exe
```

Natywne zależności (`native/`): Autodesk FBX SDK 2020.3.4
(`G:\Programing\FBXSDK\2020.3.4`, konfigurowalny w CMake przez `FBXSDK_ROOT`)
oraz meshoptimizer 0.25 (`third_party/`).

## Struktura

```
lodziarz/
  importer.py        FBX (ufbx) + OBJ/glTF/GLB (trimesh)
  atlas.py           xatlas unwrap + packing
  bake/              BakeBackend: texel_space (pełny), raycast_cage, camera26 (stuby)
  lod.py             łańcuch LOD (meshoptimizer)
  meshopt.py         ctypes binding meshoptimizer.dll
  exporter/          fbx.py (LZMESH -> fbx_writer.exe), glb.py, textures.py
  pipeline.py        orkiestracja + ProcessOptions
  cli.py             CLI
  viewer/            server.py (HTTP+API) + web/ (three.js)
  bin/               fbx_writer.exe, meshoptimizer.dll
native/              źródła C++ (CMake+Ninja)
```

## Znane ograniczenia

- meshoptimizer nie redukuje mocno meshy flat-shaded z twardymi krawędziami
  (każdy narożnik = locked vertex); photogrammetry / smooth meshe redukują
  się zgodnie z ratio
- ufbx 0.0.5 (python binding) ma kruche wrappery — importer używa wzorca
  single-pass + `scene.free()`; nie dotykać `node.parent` / `node.children`
- backendy `raycast` i `cameras26` to stuby (TODO w plikach)
