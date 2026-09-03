# Lodziarz

Standalone narzędzie desktopowe (Windows) do przygotowywania game-ready assetów:
**LOD-y + bake materiałów do jednego atlasu + viewer**. Zero zależności od
Blendera i innych DCC — jeden portable `.exe`.

## Pipeline

1. **IMPORT** — FBX (ufbx), OBJ / glTF / GLB (trimesh). Transformy node'ów
   wypalane w geometrię, jednostki normalizowane do metrów. OBJ/MTL czyta
   też `d` / `Tr` / `map_d` (przezroczystość) i `map_Bump` (normalka).
   Po imporcie **raport walidacji**: brakujące mapy, materiały bez trójkątów,
   brak UV, zdegenerowane trójkąty (log + manifest).
2. **BAKE** (opcjonalny, maska per LOD — default LOD0 zostaje na oryginalnych
   materiałach, LOD1+ dostają atlas; pełna dowolność przez checkboxy / `--bake-lods`):
   - scalenie wszystkich materiałów obiektu w 1 materiał
   - nowy UV: unwrap + packing wysp przez **xatlas**
   - trzy pełnoprawne backendy (`--backend`, select w GUI):
     - **texel** (default) — texel space, moderngl offscreen GPU: rasteryzacja
       nowego UV, per texel sampling starych tekstur; normalki re-enkodowane
       stary tangent space → world → nowy tangent space
     - **raycast** — cage raycast (trimesh BVH / embree), inflacja cage
       `--cage-offset` (0 = auto 1% przekątnej)
     - **cameras26** — projekcja z 26 kamer ortho
   - mapy: **BaseColor, Normal, ORM** (Occlusion=R, Roughness=G, Metallic=B),
     opcjonalnie **Emissive** i **Opacity** (szkło itp. — z alfa base color /
     mapy opacity; w GLB ląduje w alfie base color, w FBX pod TransparentColor,
     osobny plik `T_*_Opacity`)
   - antyaliasing: supersampling off / 2× / 4×, downsampling box-filtrem
     ważonym pokryciem; **dilation po downsamplingu** (default 8 px)
   - rozdzielczość atlasu 512–4096
   - debug: `debug/*_uv_layout.png`, `debug/*_coverage.png`
3. **LOD-y** — meshoptimizer `simplifyWithAttributes` (zachowuje UV i normale).
   Liczba LOD-ów i ratio parametrami (default 4 × 0.5^n). LOD-y dziedziczą
   atlas z LOD0 — bake robiony RAZ. Opcjonalny **smooth-weld** (`--smooth-weld`,
   checkbox w GUI): skleja rozcięcia hard edges przed simplify — dużo lepsza
   redukcja na hard-surface kosztem miękkiego cieniowania krawędzi; LOD0
   zawsze zostaje nietknięty.
4. **EXPORT**:
   - **FBX one-pass**: node LODGroup `<Nazwa>` + dzieci `<Nazwa>_LOD0..N`
     (Autodesk FBX SDK — ten sam mechanizm co w Brutgenie; UE importuje
     całość jednym plikiem z *Import Mesh LODs*). Progi przełączania
     LODGroup w cm: auto wg rozmiaru obiektu albo ręcznie (`--thresholds`,
     pole w GUI). Materiał z opacity dostaje TransparentColor (mapa albo factor).
   - tryb alternatywny: `SM_<Nazwa>_LOD0.fbx`, `SM_<Nazwa>_LOD1.fbx`, ...
   - dodatkowo **GLB** (wszystkie LOD-y jako nody — używany przez viewer)
     i **OBJ** (per LOD + wspólny MTL)
   - tekstury PNG (opcjonalnie TGA) obok plików
   - **re-export** z inną maską bake bez ponownego przetwarzania — cache
     `<nazwa>.lodziarz_cache.pkl` (wersjonowany; cache z innej wersji
     programu jest odrzucany z czytelnym komunikatem)

## Użycie

```
Lodziarz.exe                          GUI z viewerem (okno natywne)
Lodziarz.exe gui --browser            GUI w przeglądarce
Lodziarz.exe process IN -o OUT        batch CLI (plik/pliki/katalog)
```

CLI:

```
Lodziarz.exe process model.fbx -o out\model --lods 4 --ratio 0.5 --atlas 2048
Lodziarz.exe process folder_z_modelami -o out --no-bake --per-lod-fbx
opcje: --preset unreal|unity|godot|max|loose
       --lods N --ratio R --smooth-weld --thresholds "500,1000,2000"
       --no-bake --bake-lods "1,2,3"|all|none --backend texel|raycast|cameras26
       --atlas 512..4096 --dilation PX --ssaa 1|2|4 --cage-offset M --tga
       --split-orm --gloss --normal-gl --input-normal-gl
       --per-lod-fbx --flat-fbx --embed-textures --no-fbx --no-glb --obj
       --up auto|y|z
```

### Presety exportu

Preset = nazwany zestaw domyślnych wartości istniejących opcji (dropdown
w GUI Export + `--preset` w CLI). Ustawia tylko format i konwencje —
thresholds / atlas / SSAA / bake zostają poza presetem (zależą od assetu,
nie od silnika). Jawne flagi CLI / ręczna zmiana kontrolki w GUI nadpisują
preset (GUI wraca wtedy na "custom").

| Preset | FBX | GLB | Normal | Mapy AO/R/M | Uwagi |
|---|---|---|---|---|---|
| **unreal** | LODGroup | tak | DirectX | packed ORM | dzisiejsze defaulty; UE importuje jednym plikiem z *Import Mesh LODs* |
| **unity** | płaski (`_LOD0..N`, bez FbxLODGroup) | — | OpenGL | osobno + glossiness | Unity buduje LODGroup z nazw; node FbxLODGroup ignoruje |
| **godot** | — | tak | OpenGL | packed ORM | GLB niesie cały chain LOD jako nody — visibility ranges spina się w edytorze (auto-LOD Godota nie bake'uje materiałów) |
| **max** | per LOD + embed | — | DirectX | packed ORM | 3ds Max nie czyta FbxLODGroup; plik do obejrzenia od ręki |
| **loose** | per LOD | — | DirectX | osobno (roughness) | nic nie spakowane w jeden worek |

Skala/osie: bez znaczenia dla targetu — FBX niesie jednostki (cm) i osie
w nagłówku; UE jest natywnie cm/Z-up, Unity sam konwertuje (Scale Factor /
Convert Units). Piszemy cm + Y-up.

Batch po katalogu (>1 plik) zapisuje raport zbiorczy
`lodziarz_batch_report.json` (pełne wyniki) + `.csv` (skrót: plik, ok,
trójkąty per LOD, materiały, ostrzeżenia, błąd).

### Komendy known-good (szybki smoke test)

```powershell
# kula — pełny pipeline z bake
dist\Lodziarz.exe process testdata\test\ball.fbx -o testout\smoke_ball

# cube z tilingiem — warning gęstości przy małym atlasie to ZAMIERZONE
dist\Lodziarz.exe process testdata\test2\cube.fbx -o testout\smoke_cube --atlas 4096

# torus GLB — import trimesh, bez bake
dist\Lodziarz.exe process testdata\test_torus.glb -o testout\smoke_torus --no-bake

# szkło — opacity end-to-end (GLB alpha + FBX TransparentColor)
dist\Lodziarz.exe process testdata\test_glass.glb -o testout\smoke_glass
```

Sprawdź: exit code 0, brak ERROR w logu, w folderze wyjściowym FBX + GLB +
`*.lodziarz.json` z `"ok": true`.

## Zewnętrzne mapy: sidecar `<plik>.textures.json`

FBX z UE często nie niesie tekstur. Obok pliku wejściowego można położyć
sidecar JSON przypisujący mapy do materiałów (wygrywa z konwencją nazw):

```json
{
  "MI_Muzeum_Sciany": {
    "basecolor": "T_Sciany_BaseColor.png",
    "normal":    "T_Sciany_Normal.png",
    "orm":       "T_Sciany_ORM.png"
  },
  "MI_Szyby": {
    "basecolor": "T_Szyby_BC.png",
    "opacity":   "T_Szyby_Opacity.png"
  },
  "*": { "normal": "T_Default_Normal.png" }
}
```

- nazwa pliku: `model.fbx.textures.json` albo `model.textures.json`
- sloty: `basecolor`, `normal`, `orm`, `occlusion`/`ao`, `roughness`,
  `gloss`, `metallic`, `emissive`, `opacity`
- ścieżki względne wobec folderu assetu; `*` = fallback dla wszystkich
- alternatywa bez JSON: konwencja nazw `<Material>_BaseColor.png` itd.
  obok pliku albo w `textures/`; w GUI też drag&drop na sloty materiału

Workflow Substance: eksportuj z presetu *Unreal Engine 4 (Packed)* —
`T_<mat>_BaseColor/_Normal/_OcclusionRoughnessMetallic` — i wpisz te pliki
w sidecar (slot `orm` przyjmuje spakowaną mapę ORM wprost).

## Manifest `<nazwa>.lodziarz.json`

Zapisywany po każdym procesie; re-export aktualizuje `baked_mask`:

```
ok            bool     sukces
asset_name    str      nazwa assetu
out_dir       str      folder wyjściowy
fbx           str      plik FBX LODGroup ("" gdy --no-fbx)
fbx_per_lod   [str]    pliki SM_*_LODn.fbx (gdy --per-lod-fbx)
obj           [str]    pliki OBJ (gdy --obj)
glb           str      czysty export GLB wg maski
preview_glb   str      debug/*_variants.glb — oba warianty per LOD (viewer)
textures      {slot: plik}  zapisane mapy atlasu
lod_stats     [{tris, verts}]  statystyki per LOD
baked_mask    [bool]   który LOD używa atlasu
cache         str      *.lodziarz_cache.pkl do re-exportu
validation    {...}    raport walidacji importu (tris, verts, materials,
                       uv_missing, degenerate_tris, missing_maps, warnings)
error         str      komunikat błędu gdy ok=false
```

## Viewer

- przełączanie LOD-ów (przyciski / klawisze 0–9) + tri/verts count
- pasek "bake:" per LOD + **Re-export** (FBX/OBJ/GLB + manifest, bez
  ponownego bake)
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
.venv\Scripts\pip install numpy Pillow scipy trimesh ufbx xatlas moderngl pywebview pyinstaller pygltflib pytest
powershell -File native\build_native.ps1     # fbx_writer.exe + meshoptimizer.dll (wymaga FBX SDK + VS BuildTools)
.venv\Scripts\pyinstaller lodziarz.spec --noconfirm   # -> dist\Lodziarz.exe
```

Natywne zależności (`native/`): Autodesk FBX SDK 2020.3.4
(`G:\Programing\FBXSDK\2020.3.4`, konfigurowalny w CMake przez `FBXSDK_ROOT`)
oraz meshoptimizer 0.25 (`third_party/`).

### Checklista smoke po buildzie EXE

1. `dist\Lodziarz.exe` istnieje i waży ~65–75 MB
2. w bundlu są natywne binarki: `fbx_writer.exe`, `meshoptimizer.dll`,
   `msvcp140.dll`, `vcruntime140*.dll` (spec kopiuje z `lodziarz/bin`)
3. `dist\Lodziarz.exe process testdata\test\ball.fbx -o testout\smoke_ball`
   → exit 0, FBX+GLB+manifest na miejscu
4. `dist\Lodziarz.exe` (GUI) → okno się otwiera, Load + Process działa
5. testy źródeł: `.venv\Scripts\python -m pytest tests` → wszystkie zielone

## Testy

```powershell
.venv\Scripts\python -m pytest tests
```

Pokrywają: LOD chain + smooth-weld, regresję `_carry_tri_material`
(granice materiałów 1:1, bez głosowania), import OBJ/MTL (d/Tr/map_d/map_Bump)
i GLB, cache (roundtrip, tożsamość packed ORM, odrzucenie innej wersji),
integrację pipeline bez bake (OBJ → LOD-y → FBX przez LZMESH2 + GLB + manifest).
Geometria testowa jest syntetyczna — testy nie potrzebują plików z `testdata/`.

## Struktura

```
lodziarz/
  importer.py        FBX (ufbx) + OBJ/glTF/GLB (trimesh)
  report.py          raport walidacji po imporcie
  matmap.py          sidecar textures.json + konwencja nazw + przypisania GUI
  atlas.py           xatlas unwrap + packing
  bake/              BakeBackend: texel_space, raycast_cage, camera26
  lod.py             łańcuch LOD (meshoptimizer) + smooth-weld
  meshopt.py         ctypes binding meshoptimizer.dll
  cache.py           cache re-exportu (wersjonowany pickle)
  presets.py         presety exportu (unreal/unity/godot/max/loose)
  exporter/          fbx.py (LZMESH2 -> fbx_writer.exe), glb.py, obj.py, textures.py
  pipeline.py        orkiestracja + ProcessOptions
  cli.py             CLI + raport zbiorczy batcha
  viewer/            server.py (HTTP+API) + web/ (three.js)
  bin/               fbx_writer.exe, meshoptimizer.dll, VC++ runtime
native/              źródła C++ (CMake+Ninja)
tests/               pytest (syntetyczna geometria, bez binarek)
```

## Znane ograniczenia

- meshoptimizer nie redukuje mocno meshy flat-shaded z twardymi krawędziami
  (każdy narożnik = locked vertex) — dla hard-surface włącz **smooth-weld**
  (świadomy trade-off: miększe cieniowanie krawędzi na LOD1+)
- tiled materiały tracą detal w atlasie (matematyka, nie bug) — warning
  gęstości mówi ile i jaki atlas potrzebny
- ufbx 0.0.5 (python binding) ma kruche wrappery — importer robi jedną płytką
  iterację po `scene.nodes`, sceny NIE zwalnia wcale (keepalive do końca
  procesu; import i tak chodzi w osobnym procesie), nie dotykać
  `node.parent` / `node.children`
- FBX materiał to Phong — roughness/metallic nie mają tam slotu; mapa ORM
  jedzie jako plik obok FBX (podpinana ręcznie w silniku), opacity jest
  podpięte pod TransparentColor
