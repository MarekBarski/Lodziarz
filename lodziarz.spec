# PyInstaller spec — jeden portable .exe
# build: .venv\Scripts\pyinstaller.exe lodziarz.spec --noconfirm
import os

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[
        ("lodziarz/bin/fbx_writer.exe", "lodziarz/bin"),
        ("lodziarz/bin/meshoptimizer.dll", "lodziarz/bin"),
    ],
    datas=[
        ("lodziarz/viewer/web", "lodziarz/viewer/web"),
    ],
    hiddenimports=[
        "moderngl", "glcontext",
        "xatlas", "ufbx",
        "trimesh", "trimesh.visual", "trimesh.exchange.gltf",
        "PIL", "PIL.Image", "PIL.ImageDraw",
        "webview", "webview.platforms.edgechromium",
        "scipy", "scipy.sparse", "scipy.spatial",
        "pygltflib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Lodziarz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # CLI + log w konsoli; GUI otwiera wlasne okno
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
