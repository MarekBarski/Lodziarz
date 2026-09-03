"""Presety exportu — nazwane zestawy domyslnych wartosci ISTNIEJACYCH opcji.

Preset ustawia TYLKO format i konwencje (normal space, pakowanie map,
naming FBX, embed). Thresholds / atlas / SSAA / bake zostaja poza presetami
— zaleza od konkretnego assetu i tilingu, nie od silnika docelowego.
Flagi CLI i kontrolki GUI nadpisuja wartosci presetu.

Klucze = pola ProcessOptions (pilnowane testem test_presets.py).
"""
from __future__ import annotations

PRESETS: dict[str, dict] = {
    # dzisiejsze defaulty pod nazwa: FBX z FbxLODGroup (UE importuje jednym
    # plikiem z Import Mesh LODs), normal DirectX, packed ORM
    "unreal": {
        "export_fbx": True, "export_glb": True, "export_obj": False,
        "fbx_per_lod": False, "fbx_embed_textures": False,
        "fbx_flat_lods": False,
        "output_normal_directx": True,
        "orm_split": False, "output_gloss": False,
    },
    # Unity ModelImporter buduje LODGroup z konwencji nazw *_LOD0..N,
    # a node FbxLODGroup NIE jest importowany (Unity Issue Tracker) —
    # stad FBX plaski; normal OpenGL; smoothness = glossiness osobno
    "unity": {
        "export_fbx": True, "export_glb": False, "export_obj": False,
        "fbx_per_lod": False, "fbx_embed_textures": False,
        "fbx_flat_lods": True,
        "output_normal_directx": False,
        "orm_split": True, "output_gloss": True,
    },
    # Godot: GLB z calym chainem LOD jako nody _LOD0..N (baked atlas na
    # pokladzie; auto-LOD Godota nie umie bake'owac materialow — visibility
    # ranges spina sie w edytorze), normal OpenGL, bez FBX
    "godot": {
        "export_fbx": False, "export_glb": True, "export_obj": False,
        "fbx_per_lod": False, "fbx_embed_textures": False,
        "fbx_flat_lods": False,
        "output_normal_directx": False,
        "orm_split": False, "output_gloss": False,
    },
    # 3ds Max nie czyta FbxLODGroup (potwierdzone) — osobne pliki per LOD,
    # tekstury embedded: plik do obejrzenia od reki
    "max": {
        "export_fbx": False, "export_glb": False, "export_obj": False,
        "fbx_per_lod": True, "fbx_embed_textures": True,
        "fbx_flat_lods": False,
        "output_normal_directx": True,
        "orm_split": False, "output_gloss": False,
    },
    # nic nie spakowane: mapy osobno (AO/Rough/Metal), FBX per LOD, bez GLB
    "loose": {
        "export_fbx": False, "export_glb": False, "export_obj": False,
        "fbx_per_lod": True, "fbx_embed_textures": False,
        "fbx_flat_lods": False,
        "output_normal_directx": True,
        "orm_split": True, "output_gloss": False,
    },
}
