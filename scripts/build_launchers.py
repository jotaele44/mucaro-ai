#!/usr/bin/env python3
"""Build the Windows launcher from source, reproducibly.

The launcher sources in packaging/launchers/windows/ are not wired up yet: they are the base for the
queued Windows double-click launcher, which will commit a working .exe. Until then nothing is
committed from the build except the icon.

Outputs:
- ``packaging/icons/mucaro.ico``             Windows icon (committed; embedded in the .exe)
- ``build/launchers/mucaro_launcher.exe``    Windows launcher (C, GUI subsystem; build/ is not committed)

Tools are pinned and hash-checked (packaging/launchers/build-requirements.txt: zig for cross-compiling,
Pillow for the icon) and installed into a temporary folder. The build host is Linux x86_64.

    python scripts/build_launchers.py           # rebuild the icon and the .exe
    python scripts/build_launchers.py --check   # build twice: the .exe must be identical, the icon must match the committed one
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
EXE = ROOT / "build" / "launchers" / "mucaro_launcher.exe"
ICO = ROOT / "packaging" / "icons" / "mucaro.ico"
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

# An owl face on a 64-unit grid, since a mucaro is an owl.
BACKGROUND = (27, 36, 51, 255)
TUFT = (201, 163, 107, 255)
EYE = (242, 230, 207, 255)
BEAK = (224, 168, 74, 255)


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


def draw_icon(size: int):
    from PIL import Image, ImageDraw  # installed by install_tools

    scale = 4  # draw large, then downsample for smooth edges
    unit = size * scale / 64
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def box(cx: float, cy: float, r: float) -> tuple[float, float, float, float]:
        return ((cx - r) * unit, (cy - r) * unit, (cx + r) * unit, (cy + r) * unit)

    def points(*coords: float) -> list[tuple[float, float]]:
        return [(coords[i] * unit, coords[i + 1] * unit) for i in range(0, len(coords), 2)]

    draw.rounded_rectangle((0, 0, size * scale - 1, size * scale - 1), radius=14 * unit, fill=BACKGROUND)
    draw.polygon(points(14, 20, 22, 12, 26, 20), fill=TUFT)
    draw.polygon(points(50, 20, 42, 12, 38, 20), fill=TUFT)
    for cx in (23, 41):
        draw.ellipse(box(cx, 32, 10), fill=EYE)
        draw.ellipse(box(cx, 32, 5), fill=BACKGROUND)
    draw.polygon(points(29, 42, 35, 42, 32, 48), fill=BEAK)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def build_icon(ico: Path) -> None:
    ico.parent.mkdir(parents=True, exist_ok=True)
    draw_icon(1024).save(ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


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


def install(scratch: Path) -> Path:
    tools = scratch / "tools"
    install_tools(tools)
    sys.path.insert(0, str(tools))
    return tools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Build twice in a temp folder and compare.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mucaro-launchers-") as tmp:
        scratch = Path(tmp)
        tools = install(scratch)
        if not args.check:
            build_icon(ICO)
            build_exe(tools, ICO, EXE, scratch / "exe")
            for path in (ICO, EXE):
                print(f"BUILT {path.relative_to(ROOT)} sha256={sha256(path)} bytes={path.stat().st_size}")
            print("LAUNCHERS_BUILD_OK")
            return 0

        out = scratch / "out"
        build_icon(out / "mucaro.ico")
        build_exe(tools, out / "mucaro.ico", out / "first.exe", scratch / "exe-1")
        build_exe(tools, out / "mucaro.ico", out / "second.exe", scratch / "exe-2")
        icon_same = ICO.is_file() and ICO.read_bytes() == (out / "mucaro.ico").read_bytes()
        exe_same = (out / "first.exe").read_bytes() == (out / "second.exe").read_bytes()
        print(f"{'MATCH' if icon_same else 'MISMATCH'} {ICO.relative_to(ROOT)} rebuilt_sha256={sha256(out / 'mucaro.ico')}")
        print(f"{'MATCH' if exe_same else 'MISMATCH'} launcher .exe built twice sha256={sha256(out / 'first.exe')} / {sha256(out / 'second.exe')}")
        ok = icon_same and exe_same
        print("LAUNCHERS_REPRODUCIBLE_OK" if ok else "LAUNCHERS_REPRODUCIBLE_FAILED")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
