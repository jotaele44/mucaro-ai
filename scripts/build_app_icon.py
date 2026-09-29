#!/usr/bin/env python3
"""Build the icon of the double-clickable ``Mucaro AI.app`` (``Contents/Resources/AppIcon.icns``).

The artwork is the múcaro owl drawn by ``scripts/build_macos_app.py:render_icon_png``, the same
icon as the packaged release app. Every size macOS asks for is rendered on its own (sharper than
scaling one image down) and packed into a PNG-based ICNS file. Only the standard library is used,
so the icon can be rebuilt and checked on any OS: no ``iconutil``, ``sips`` or image library.

Usage:
  python scripts/build_app_icon.py           write the icon into the app bundle
  python scripts/build_app_icon.py --check   verify the committed icon without rewriting it
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_macos_app import render_icon_png  # noqa: E402

ICNS_PATH = ROOT / "Mucaro AI.app" / "Contents" / "Resources" / "AppIcon.icns"
# PNG-based ICNS element types (macOS 10.7 and later) and the pixel size each one holds.
# ic11-ic14 and ic10 are the @2x (Retina) variants of the 16, 32, 128, 256 and 512 point sizes.
ICNS_TYPES = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)
# Tracked files must stay under the release large-file gate (scripts/certify_release.py).
MAX_BYTES = 1_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def build_icns(render: Callable[[int], bytes] = render_icon_png, types: tuple[tuple[str, int], ...] = ICNS_TYPES) -> bytes:
    rendered: dict[int, bytes] = {}
    body = bytearray()
    for kind, size in types:
        if size not in rendered:
            rendered[size] = render(size)
        png = rendered[size]
        body += kind.encode("ascii") + struct.pack(">I", len(png) + 8) + png
    return b"icns" + struct.pack(">I", len(body) + 8) + bytes(body)


def parse_icns(data: bytes) -> list[tuple[str, bytes]]:
    """Return the (type, payload) elements of an ICNS file."""
    if len(data) < 8 or data[:4] != b"icns" or struct.unpack(">I", data[4:8])[0] != len(data):
        raise ValueError("not an ICNS file (bad header or length)")
    entries: list[tuple[str, bytes]] = []
    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError(f"truncated ICNS element header at byte {offset}")
        kind = data[offset : offset + 4].decode("latin-1")
        length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        if length < 8 or offset + length > len(data):
            raise ValueError(f"bad length {length} for ICNS element {kind!r}")
        entries.append((kind, data[offset + 8 : offset + length]))
        offset += length
    return entries


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != PNG_SIGNATURE or png[12:16] != b"IHDR":
        raise ValueError("not a PNG image")
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def png_pixels_ok(png: bytes) -> bool:
    """Decode the chunk stream far enough to prove the image data is complete (8-bit RGBA)."""
    width, height = png_size(png)
    offset, idat = 8, bytearray()
    while offset + 8 <= len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        kind = png[offset + 4 : offset + 8]
        data = png[offset + 8 : offset + 8 + length]
        crc = png[offset + 8 + length : offset + 12 + length]
        if len(data) != length or len(crc) != 4 or struct.unpack(">I", crc)[0] != zlib.crc32(kind + data) & 0xFFFFFFFF:
            return False
        if kind == b"IDAT":
            idat += data
        if kind == b"IEND":
            break
        offset += 12 + length
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return False
    return png[24:26] == bytes((8, 6)) and len(raw) == height * (1 + width * 4)


def check(data: bytes) -> list[str]:
    problems: list[str] = []
    if len(data) > MAX_BYTES:
        problems.append(f"icon is {len(data)} bytes; tracked files must stay under {MAX_BYTES}")
    try:
        entries = dict(parse_icns(data))
    except ValueError as exc:
        return problems + [str(exc)]
    for kind, size in ICNS_TYPES:
        png = entries.get(kind)
        if png is None:
            problems.append(f"missing ICNS element {kind} ({size}x{size})")
            continue
        try:
            dimensions = png_size(png)
        except ValueError:
            problems.append(f"ICNS element {kind} is not a PNG image")
            continue
        if dimensions != (size, size):
            problems.append(f"ICNS element {kind} is {dimensions[0]}x{dimensions[1]}, expected {size}x{size}")
        elif not png_pixels_ok(png):
            problems.append(f"ICNS element {kind} has damaged image data")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the Mucaro AI.app icon.")
    parser.add_argument("--check", action="store_true", help="Verify the committed icon instead of writing it.")
    parser.add_argument("--output", type=Path, default=ICNS_PATH, help="Where to write the icon (default: the app bundle).")
    args = parser.parse_args(argv)

    if args.check:
        if not args.output.is_file():
            print(f"ERROR_APP_ICON_MISSING {args.output}", file=sys.stderr)
            return 2
        problems = check(args.output.read_bytes())
        for problem in problems:
            print(f"ERROR_APP_ICON {problem}", file=sys.stderr)
        if problems:
            return 2
        print(f"APP_ICON_OK {args.output} bytes={args.output.stat().st_size}")
        return 0

    data = build_icns()
    problems = check(data)
    for problem in problems:
        print(f"ERROR_APP_ICON {problem}", file=sys.stderr)
    if problems:
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"APP_ICON_WRITTEN {args.output} bytes={len(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
