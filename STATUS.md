# STATUS

**Wersja:** 0.1.0 (2026-08-12) — pierwsza działająca całość

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
