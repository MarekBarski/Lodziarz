# STATUS

**Wersja:** 0.2.0 (2026-08-12) — crash-safe worker + bake od LOD N

## Nowe w 0.2.0
- pipeline w osobnym procesie (`lodziarz/worker.py`): crash natywny (ufbx/GL/FBX SDK)
  nie zabija GUI ani batcha; `lodziarz_crash.log` w folderze wyjściowym
- **bake per LOD — dowolna maska checkboxami** (GUI: rząd checkboxów pod
  liczbę LOD-ów; CLI: `--bake-lods "1,2,3"` / `"all"`); default: LOD0
  z oryginalnymi materiałami, LOD1+ atlas
- GLB: LOD-y bez bake mają primitives per materiał źródłowy
- default atlasu: 1024 (było 2048)

## Nowe w 0.3.0
- **crash rozwiązany u źródła**: import FBX w dedykowanym podprocesie
  (`fbx_import_proc.py`) — ufbx psuje stertę procesu (AV przy GC / lazy
  importach / free()); podproces wysyła Asset przez Queue i kończy
  `os._exit(0)` bez teardownu. Testowane na 3 assetach UE użytkownika
  (do 2M tri, 25 materiałów) — wszystkie przechodzą
- **mapy per materiał z zewnątrz** (`matmap.py`): FBX z UE nie ma tekstur —
  sidecar `<plik>.textures.json` (per materiał: basecolor/normal/orm albo
  occlusion/roughness/metallic/gloss osobno, emissive, opacity; `*` fallback)
  + auto-dopasowanie po konwencji `<Materiał>_BaseColor.png` obok pliku
  lub w `textures/`; czytelny warning gdy materiał zostaje bez map
- **embedded tekstury FBX**: import działa (zweryfikowany roundtripem),
  export nowym checkboxem "wbuduj tekstury do FBX" / `--embed-textures`
- jednomateriałowy FBX → same LOD-y: `--no-bake`, materiał + tekstury
  przechodzą do wyjścia (FBX i GLB)

## Działa (zweryfikowane)
- [x] import FBX/OBJ/glTF/GLB
- [x] bake texel-space: atlas BaseColor/Normal/ORM/Emissive/Opacity, SSAA 1/2/4, dilation
- [x] LOD chain (meshoptimizer, UV+normale zachowane, atlas z LOD0)
- [x] FBX one-pass LODGroup (`<Nazwa>` → `<Nazwa>_LOD0..N`) — **UE 5.6 import potwierdzony headless** (num_lods=4)
- [x] FBX per LOD (`SM_*_LODn.fbx`), GLB (TEXCOORD_1 = stare UV), PNG/TGA
- [x] viewer: LOD-y, sloty packed+osobne (gloss→rough inwersja), DX/GL flip, tryby kanałów, UV checker/flat stare+nowe, drag&drop GLB
- [x] GUI pywebview + tryb `--browser`, CLI batch
- [x] `dist\Lodziarz.exe` portable onefile (64 MB)

## Nowe w 0.5.0
- **backend `raycast` ZAIMPLEMENTOWANY**: G-buffer w texel space, inflacja
  cage wzdłuż normali (`--cage-offset`, 0 = auto 1% diag), raycast BVH
  (trimesh+rtree), sampling barycentryczny starych UV, normalki przez TBN
  trafionego trójkąta; CPU ~7k rays/s — tryb jakościowy/debug
- **backend `cameras26` ZAIMPLEMENTOWANY**: 26 kamer ortho (ściany/krawędzie/
  rogi sześcianu), 5 map + depth per kamera, wybór najlepszej widocznej kamery
  per texel (dot(n, d) + test depth z biasem zależnym od rozdzielczości)
- `bake/common.py`: wspólny G-buffer, kompozycja map, dilation, sampling
- matmap: konwencja Substance Painter (`<mesh>_<Materiał>_BaseColor`,
  `_OcclusionRoughnessMetallic`, znaki specjalne → `_`)

## Ograniczenia
- flat-shaded hard-surface słabo się redukuje (locked verts w meshopt) — patrz `docs/LOG_2026-08-12.md`

## Build
`native\build_native.ps1` → `.venv\Scripts\pyinstaller.exe lodziarz.spec --noconfirm`
Wymaga: FBX SDK `G:\Programing\FBXSDK\2020.3.4`, VS BuildTools 18.

Szczegóły sesji: `docs/LOG_2026-08-12.md`. Pamięć agenta: pułapki ufbx + stack.
