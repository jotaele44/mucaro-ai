#!/usr/bin/env python3
"""Build the committed Windows launcher from source, reproducibly.

Outputs (all committed):
- ``Mucaro AI.exe``                            Windows launcher (C, packaging/launchers/windows/)
- ``packaging/icons/mucaro.ico``               Windows icon (embedded in the .exe and used for the
                                               window), made from the macOS app's owl
                                               (``Mucaro AI Setup.app/Contents/Resources/MucaroAI.png``)
- ``packaging/launchers/windows/BUILD_SHA256`` expected hash of the .exe

Tools are pinned and hash-checked (packaging/launchers/build-requirements.txt: zig for cross-compiling,
Pillow for the icon) and installed into a temporary folder. The build host is Linux x86_64.

    python scripts/build_launchers.py           # rebuild and write the outputs
    python scripts/build_launchers.py --check   # rebuild in a temp folder; fail unless identical to the committed files
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "packaging" / "launchers" / "build-requirements.txt"
WINDOWS_SRC = ROOT / "packaging" / "launchers" / "windows"
EXE = ROOT / "Mucaro AI.exe"
ICO = ROOT / "packaging" / "icons" / "mucaro.ico"
ICON_SOURCE = ROOT / "Mucaro AI Setup.app" / "Contents" / "Resources" / "MucaroAI.png"
EXE_HASH_FILE = WINDOWS_SRC / "BUILD_SHA256"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
ZIG_FLAGS = [
    "cc",
    "-target",
    "x86_64-windows-gnu",
    "-Os",
    "-s",
    "-fno-ident",
    # GUI subsystem: no console window flashes on double-click (zig ignores the GCC-style -mwindows).
    "-Wl,--subsystem,windows",
]


def install_tools(target: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "--no-deps",
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--target",
            str(target),
            "-r",
            str(REQUIREMENTS),
        ],
        check=True,
    )


def build_icon(ico: Path) -> None:
    from PIL import Image  # installed by install_tools

    ico.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(ICON_SOURCE) as source:
        source.convert("RGBA").save(ico, format="ICO", sizes=ICO_SIZES)


def build_exe(tools: Path, ico: Path, output: Path, scratch: Path) -> None:
    # Compile from a copy of the sources next to the icon they reference, so the build never depends on
    # the checkout path and the committed icon is the one embedded.
    src = scratch / "src" / "launchers" / "windows"
    src.mkdir(parents=True)
    for name in ("mucaro_launcher.c", "mucaro_launcher.rc", "mucaro_launcher.manifest"):
        shutil.copy2(WINDOWS_SRC / name, src / name)
    (scratch / "src" / "icons").mkdir()
    shutil.copy2(ico, scratch / "src" / "icons" / "mucaro.ico")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tools)
    env["ZIG_GLOBAL_CACHE_DIR"] = str(scratch / "zig-global-cache")
    env["ZIG_LOCAL_CACHE_DIR"] = str(scratch / "zig-local-cache")
    subprocess.run(
        [sys.executable, "-m", "ziglang", *ZIG_FLAGS, "mucaro_launcher.c", "mucaro_launcher.rc", "-o", "launcher.exe"],
        cwd=src,
        env=env,
        check=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "launcher.exe", output)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_all(out_exe: Path, out_ico: Path, scratch: Path) -> None:
    tools = scratch / "tools"
    install_tools(tools)
    sys.path.insert(0, str(tools))
    build_icon(out_ico)
    build_exe(tools, out_ico, out_exe, scratch / "exe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Rebuild in a temp folder and compare with the committed files.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mucaro-launchers-") as tmp:
        scratch = Path(tmp)
        if not args.check:
            build_all(EXE, ICO, scratch)
            EXE_HASH_FILE.write_text(f"{sha256(EXE)}  {EXE.name}\n", encoding="utf-8")
            for path in (EXE, ICO):
                print(f"BUILT {path.relative_to(ROOT)} sha256={sha256(path)} bytes={path.stat().st_size}")
            print("LAUNCHERS_BUILD_OK")
            return 0

        out = scratch / "out"
        out.mkdir()
        rebuilt = {EXE: out / "launcher.exe", ICO: out / "mucaro.ico"}
        build_all(rebuilt[EXE], rebuilt[ICO], scratch)
        ok = True
        for committed, fresh in rebuilt.items():
            same = committed.is_file() and committed.read_bytes() == fresh.read_bytes()
            ok = ok and same
            print(f"{'MATCH' if same else 'MISMATCH'} {committed.relative_to(ROOT)} rebuilt_sha256={sha256(fresh)}")
        recorded = EXE_HASH_FILE.read_text(encoding="utf-8").split()[0] if EXE_HASH_FILE.is_file() else ""
        if recorded != sha256(rebuilt[EXE]):
            ok = False
            print(f"MISMATCH {EXE_HASH_FILE.relative_to(ROOT)} recorded={recorded or 'missing'}")
        print("LAUNCHERS_REPRODUCIBLE_OK" if ok else "LAUNCHERS_REPRODUCIBLE_FAILED")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
