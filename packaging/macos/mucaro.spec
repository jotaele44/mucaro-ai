# PyInstaller spec for the Mucaro AI desktop app (GOAL-001, TGT-001, TGT-028).
#
# Do not run directly: scripts/build_macos_app.py sets the version environment,
# checks preconditions, signs, verifies and writes the build receipt.
#
# Result on macOS: dist/Mucaro AI.app, a native Mach-O launcher (backend.app.desktop_shell)
# with its Python runtime, locked dependencies, the built GUI and the JSON schemas inside.
# On Linux the same spec produces the onedir tree only (structural check; no .app).
# ruff: noqa
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
VERSION = os.environ["MUCARO_APP_VERSION"]
BUILD_NUMBER = os.environ.get("MUCARO_APP_BUILD", VERSION)
BUNDLE_ID = os.environ.get("MUCARO_APP_BUNDLE_ID", "ai.mucaro.app")
MINIMUM_OS = os.environ.get("MUCARO_APP_MINIMUM_OS", "14.0")
ICON = os.environ.get("MUCARO_APP_ICON") or None

datas = [
    (str(ROOT / "schemas"), "schemas"),
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "pyproject.toml"), "."),
    (str(ROOT / "backend" / "app" / "gis_grids"), "backend/app/gis_grids"),
]
binaries = []
hiddenimports = collect_submodules("backend.app") + collect_submodules("uvicorn")
for package in ("rasterio", "pyproj", "shapely"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(ROOT / "packaging" / "macos" / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mucaro AI",
    console=False,
    argv_emulation=False,
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=None,  # ad-hoc; Developer ID signing is BLK-SIGN-001
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Mucaro AI")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Mucaro AI.app",
        icon=ICON,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": "Mucaro AI",
            "CFBundleDisplayName": "Mucaro AI",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": BUILD_NUMBER,
            "LSMinimumSystemVersion": MINIMUM_OS,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "Local-first. Your data stays on this Mac.",
            # The window only loads the app's own loopback service; no arbitrary loads are allowed.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
