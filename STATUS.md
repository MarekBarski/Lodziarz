# STATUS

**Wersja:** 0.2.0 (2026-08-12) — crash-safe worker + bake od LOD N

## Nowe w 0.2.0
- pipeline w osobnym procesie (`lodziarz/worker.py`): crash natywny (ufbx/GL/FBX SDK)
  nie zabija GUI ani batcha; `lodziarz_crash.log` w folderze wyjściowym
- **bake od LOD N**: LOD-y < N zostają z oryginalnymi materiałami i UV,
  LOD-y ≥ N dostają atlas (GUI: "bake od LOD", CLI: `--bake-from-lod N`)
- GLB: LOD-y bez bake mają primitives per materiał źródłowy
- NIEROZWIĄZANE: model użytkownika crashował 0.1.0 — teraz błąd będzie
  złapany i zalogowany; czekamy na retest i plik do debugowania

## Działa (zweryfikowane)
- [x] import FBX/OBJ/glTF/GLB
- [x] bake texel-space: atlas BaseColor/Normal/ORM/Emissive/Opacity, SSAA 1/2/4, dilation
- [x] LOD chain (meshoptimizer, UV+normale zachowane, atlas z LOD0)
- [x] FBX one-pass LODGroup (`<Nazwa>` → `<Nazwa>_LOD0..N`) — **UE 5.6 import potwierdzony headless** (num_lods=4)
- [x] FBX per LOD (`SM_*_LODn.fbx`), GLB (TEXCOORD_1 = stare UV), PNG/TGA
- [x] viewer: LOD-y, sloty packed+osobne (gloss→rough inwersja), DX/GL flip, tryby kanałów, UV checker/flat stare+nowe, drag&drop GLB
- [x] GUI pywebview + tryb `--browser`, CLI batch
- [x] `dist\Lodziarz.exe` portable onefile (64 MB)

## Stuby / ograniczenia
- [ ] bake backend `raycast` (TODO w `lodziarz/bake/raycast_cage.py`)
- [ ] bake backend `cameras26` (TODO w `lodziarz/bake/camera26.py`)
- flat-shaded hard-surface słabo się redukuje (locked verts w meshopt) — patrz `docs/LOG_2026-08-12.md`

## Build
`native\build_native.ps1` → `.venv\Scripts\pyinstaller.exe lodziarz.spec --noconfirm`
Wymaga: FBX SDK `G:\Programing\FBXSDK\2020.3.4`, VS BuildTools 18.

Szczegóły sesji: `docs/LOG_2026-08-12.md`. Pamięć agenta: pułapki ufbx + stack.
