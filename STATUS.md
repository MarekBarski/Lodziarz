# STATUS

**Wersja:** 0.8.0 (2026-09-03) — smooth-weld, progi LODGroup, opacity w FBX
(LZMESH2), walidacja importu, raport batcha, testy pytest. Projekt wrócił
do aktywnego rozwoju (decyzja Marka 2026-09-03).

## Architektura bake (NIE ZMIENIAĆ bez zgody Marka)

- unwrap UV (xatlas) + bake ZAWSZE na **oryginalnej** siatce; potem 2 sety
  LOD-ów: baked chain (decymacja z zachowaniem UV) i orig chain — maska per
  LOD wybiera set do wyjścia
- **granice materiałów per PIXEL, nigdy per trójkąt** — xatlas zachowuje
  kolejność trójkątów, tri_material przenoszony 1:1 (`atlas.py`,
  `_carry_tri_material`); ŻADNEGO głosowania/majority vote (to był bug
  z zygzakiem, patrz LOG_2026-08-13); pilnowane testem `test_atlas_carry.py`
- texel backend wymaga topologii źródła (target == unwrap oryginału);
  raycast/cameras26 mają parametr `source` (projekcja, trafienia > 3× cage
  odrzucane)
- LOD0 default oryginalny, LOD1+ baked — użytkownik decyduje maską
  (checkboxy w GUI / `--bake-lods all` daje baked LOD0)

## Nowe w 0.8.0 (2026-09-03)

- **smooth-weld** (opt-in, checkbox GUI + `--smooth-weld`): sklejanie
  rozcięć hard edges przed simplify — odblokowuje redukcję na hard-surface;
  klucz weld = pozycja+UV (szwy atlasu nietknięte), normale uśredniane,
  LOD0 zawsze oryginalny
- **progi LODGroup jako parametr** (pole GUI + `--thresholds "500,1000"`,
  cm; auto gdy puste); progi zapamiętane w cache — re-export ich używa
- **opacity w FBX**: format LZMESH v2 (5. slot tekstury + opacity_factor),
  `fbx_writer.exe` podpina mapę pod TransparentColor (factor 1.0) albo sam
  factor (1 − opacity); writer czyta v1 i v2
- **OBJ/MTL**: `d` / `Tr` / `map_d` → opacity, `map_Bump` → normalka
  (wcześniej gubione)
- **walidacja importu** (`report.py`): brak UV, zdegenerowane trójkąty,
  materiały bez trójkątów, brakujące mapy — log przy Load/Process + sekcja
  `validation` w manifeście
- **raport zbiorczy batcha** (`lodziarz_batch_report.json` + `.csv`) przy
  przetwarzaniu >1 pliku
- **cache wersjonowany** (`CACHE_VERSION = 2`): load odmawia czytania innej
  wersji z czytelnym komunikatem zamiast pękać na starym pickle
- **raycast + cameras26 oficjalnie wspierane** (decyzja Marka; README
  zsynchronizowane — wcześniej kłamało, że to stuby)
- komunikat po Re-export przez i18n i z listą faktycznie zapisanych plików
  (wcześniej hardcoded "FBX ..." po polsku)
- **testy pytest** (`tests/`, 12 szt.): LOD/smooth-weld, `_carry_tri_material`,
  import OBJ/MTL/GLB, cache, integracja pipeline bez bake (FBX+GLB+manifest);
  syntetyczna geometria, zero binarek
- `testdata/` wyjęte z gita (decyzja Marka — testy nie siedzą w repo;
  pliki zostają lokalnie na dysku)

## Działa (zweryfikowane)

- [x] testy: 12/12 pytest zielone (2026-09-03)
- [x] `fbx_writer.exe` przebudowany z LZMESH2; integracja OBJ z `d 0.3`
      → FBX przechodzi (test_pipeline)
- [x] kula `testdata/test/ball.fbx`: LOD0 == LOD1 po bake (2026-08-13)
- [x] cube `testdata/test2/cube.fbx` z tilingiem 12×: OK przy atlasie 4096
- [x] re-export z nową maską: FBX/OBJ/GLB + manifest w ~1 s
- [x] jasność BaseColor po bake = źródło (delta < 0.6/255)
- [x] import FBX/OBJ/glTF/GLB, LOD chain, FBX LODGroup (UE 5.6), GLB,
      PNG/TGA, GUI+CLI, portable exe
- [ ] smooth-weld / progi / opacity FBX w GUI — czeka na wizualny werdykt
      Marka (screeny)

## Ograniczenia

- flat-shaded hard-surface słabo się redukuje bez smooth-weld (locked verts
  w meshopt); z smooth-weld redukuje, ale cieniowanie krawędzi mięknie
- tiled materiały tracą detal w atlasie (matematyka, nie bug) — warning
  mówi ile i jaki atlas potrzebny
- FBX Phong nie ma slotów PBR — ORM jedzie plikiem obok, opacity przez
  TransparentColor
- ufbx pitfalls — patrz pamięć agenta i LOG_2026-08-12

## Build

`native\build_native.ps1` (natywne, tylko gdy zmieniane) →
`.venv\Scripts\pyinstaller.exe lodziarz.spec --noconfirm` → `dist\Lodziarz.exe`.
**Marek testuje EXE — po zmianach w Pythonie zawsze przebudować.**
Wymaga: FBX SDK `G:\Programing\FBXSDK\2020.3.4`, VS BuildTools 18.
Checklista smoke po buildzie: patrz README → "Checklista smoke po buildzie EXE".

## Git

GitHub: https://github.com/MarekBarski/Lodziarz (push działa).
`testdata/` w całości poza repo (`.gitignore`); duży `.glb` 243 MB wycięty
z historii wcześniej (backup: branch `backup-pre-filter`).

Szczegóły sesji: `docs/LOG_2026-08-13.md`, `docs/LOG_2026-08-12.md`.
