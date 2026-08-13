# STATUS

**Wersja:** 0.7.1 (2026-08-13) — bake per pixel, 2 sety LOD-ów, live toggle, formaty exportu

## Architektura bake (NIE ZMIENIAĆ bez zgody Marka)

- unwrap UV (xatlas) + bake ZAWSZE na **oryginalnej** siatce; potem 2 sety
  LOD-ów: baked chain (decymacja z zachowaniem UV) i orig chain — maska per
  LOD wybiera set do wyjścia
- **granice materiałów per PIXEL, nigdy per trójkąt** — xatlas zachowuje
  kolejność trójkątów, tri_material przenoszony 1:1 (`atlas.py`,
  `_carry_tri_material`); ŻADNEGO głosowania/majority vote (to był bug
  z zygzakiem, patrz LOG_2026-08-13)
- texel backend wymaga topologii źródła (target == unwrap oryginału);
  raycast/cameras26 mają parametr `source` (projekcja, trafienia > 3× cage
  odrzucane)

## Nowe w 0.6.x–0.7.x

- **bake per LOD przełączalny po procesie**: viewer pasek "bake:" +
  Re-export (FBX/OBJ/GLB + manifest, bez ponownego bake) z cache
  `<nazwa>.lodziarz_cache.pkl`; podgląd `debug/<nazwa>_variants.glb`
  (oba warianty per LOD), `<nazwa>.glb` = czysty export wg maski
- **formaty exportu**: FBX / GLB / OBJ checkboxami (CLI `--no-fbx`/`--obj`);
  OBJ per LOD + wspólny MTL
- **mapy AO/R/M**: ORM spakowane albo osobno (roughness/glossiness) —
  GUI select, CLI `--split-orm`/`--gloss`
- **normal DirectX domyślnie** (in/out/viewer); GL: `--input-normal-gl`/`--normal-gl`
- **texel_density_hint**: warning gdy tiling/duże tekstury dają atlasowi
  wielokrotnie mniejszą rozdzielczość niż źródło + sugerowana rozdzielczość
- i18n EN (default) / PL zapamiętywany; logo + maskotka (`UI/`), ikona exe
- miniaturki AO/R/M pokazują swój kanał; hover = podgląd 512px

## Działa (zweryfikowane 2026-08-13)

- [x] kula `testdata/test/ball.fbx`: LOD0 == LOD1 po bake (screeny
      `testout/claude_fix/screens/`)
- [x] cube `testdata/test2/cube.fbx` z tilingiem 12×: OK przy atlasie 4096;
      przy małym atlasie warning gęstości (to nie bug — fizyka atlasu)
- [x] re-export z nową maską: FBX/OBJ/GLB + manifest w ~1 s
- [x] jasność BaseColor po bake = źródło (delta < 0.6/255, zmierzone)
- [x] import FBX/OBJ/glTF/GLB, LOD chain, FBX LODGroup (UE 5.6 potwierdzone
      wcześniej), GLB, PNG/TGA, GUI+CLI, portable exe

## Ograniczenia

- flat-shaded hard-surface słabo się redukuje (locked verts w meshopt)
- tiled materiały tracą detal w atlasie (matematyka, nie bug) — warning
  mówi ile i jaki atlas potrzebny
- ufbx pitfalls — patrz pamięć agenta i LOG_2026-08-12

## Build

`native\build_native.ps1` (natywne, tylko gdy zmieniane) →
`.venv\Scripts\pyinstaller.exe lodziarz.spec --noconfirm` → `dist\Lodziarz.exe`.
**Marek testuje EXE — po zmianach w Pythonie zawsze przebudować.**
Wymaga: FBX SDK `G:\Programing\FBXSDK\2020.3.4`, VS BuildTools 18.

## Git

GitHub: https://github.com/MarekBarski/Lodziarz (push działa).
Duże pliki testdata: `.glb` 243 MB wycięty z historii (backup: branch
`backup-pre-filter`), NIE commitować generowanych GLB z testdata.

Szczegóły sesji: `docs/LOG_2026-08-13.md` (dziś), `docs/LOG_2026-08-12.md`.
