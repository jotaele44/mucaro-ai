#!/usr/bin/env python3
"""Build the double-clickable ``Mucaro AI.app`` (GOAL-001, TGT-001, TGT-026, TGT-028).

Runs PyInstaller with ``packaging/macos/mucaro.spec`` in an environment installed from
``packaging/macos/requirements-macos-arm64.lock`` (hash-pinned), then on macOS:
ad-hoc signs and verifies the bundle, checks Info.plist and architecture, smoke-starts
the frozen app headless, zips it with ditto and writes a hashed manifest and receipt.

Developer ID signing, notarization and stapling need credentials (BLK-SIGN-001); the
receipt says so instead of implying them.

``--structural`` builds the onedir tree on a non-macOS host to check the spec and the
frozen service only; it never yields a distributable app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "macos" / "mucaro.spec"
LOCK = ROOT / "packaging" / "macos" / "requirements-macos-arm64.lock"
APP_NAME = "Mucaro AI"
BUNDLE_ID = "ai.mucaro.app"
MINIMUM_OS = "14.0"  # pinned pyproj/rasterio arm64 wheels require macOS 14 (RC-008)
PYINSTALLER_VERSION = "6.22.3"
FORBIDDEN_BUNDLE_NAMES = {".env", "mucaro_ai.sqlite3", "instance.json", ".git"}
SCHEMA_VERSION = "mucaro-macos-app-build/1.0"


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=check)


def _git(*args: str) -> str:
    return _run(["git", *args]).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])


def tree_manifest(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append({"path": relative, "symlink": os.readlink(path)})
        elif path.is_file():
            rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path), "executable": os.access(path, os.X_OK)})
    return rows


def tree_digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# --- icon: a múcaro (Puerto Rican screech owl) face, drawn analytically so no image library is needed.


def _png(size: int, pixels: bytearray) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    stride = size * 4
    raw = b"".join(b"\x00" + bytes(pixels[y * stride : (y + 1) * stride]) for y in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def render_icon_png(size: int = 1024) -> bytes:
    pixels = bytearray(size * size * 4)
    inset, radius = 0.1 * size, 0.185 * size  # Apple grid: 824px body on a 1024px canvas
    half = size / 2 - inset
    eyes = ((0.37, 0.47), (0.63, 0.47))
    top, bottom = (0x2F, 0x8A, 0x6A), (0x1F, 0x5F, 0x4A)

    def cover(distance: float) -> float:
        return max(0.0, min(1.0, 0.5 - distance))

    def beak(x: float, y: float) -> float:
        # Downward triangle between the eyes (distance approximated by edge planes).
        ax, ay, bx, by, cx, cy = 0.46 * size, 0.58 * size, 0.54 * size, 0.58 * size, 0.5 * size, 0.68 * size
        edges = []
        for (px, py), (qx, qy) in (((ax, ay), (bx, by)), ((bx, by), (cx, cy)), ((cx, cy), (ax, ay))):
            nx, ny = qy - py, -(qx - px)
            length = math.hypot(nx, ny)
            edges.append(((x - px) * nx + (y - py) * ny) / length)
        return max(edges)

    for y in range(size):
        t = y / (size - 1)
        base = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            qx, qy = abs(px - size / 2) - (half - radius), abs(py - size / 2) - (half - radius)
            body = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
            alpha = cover(body)
            if alpha <= 0.0:
                continue
            color = [float(c) for c in base]
            for ex, ey in eyes:
                d = math.hypot(px - ex * size, py - ey * size)
                white = cover(d - 0.115 * size)
                amber = cover(d - 0.07 * size)
                pupil = cover(d - 0.04 * size)
                for i, (w, a, p) in enumerate(zip((0xFA, 0xFA, 0xF5), (0xF2, 0xB1, 0x3A), (0x14, 0x1F, 0x1A))):
                    color[i] = color[i] * (1 - white) + w * white
                    color[i] = color[i] * (1 - amber) + a * amber
                    color[i] = color[i] * (1 - pupil) + p * pupil
            b = cover(beak(px, py))
            for i, c in enumerate((0xF2, 0xB1, 0x3A)):
                color[i] = color[i] * (1 - b) + c * b
            offset = (y * size + x) * 4
            pixels[offset : offset + 4] = bytes((round(color[0]), round(color[1]), round(color[2]), round(alpha * 255)))
    return _png(size, pixels)


def build_icns(workdir: Path) -> Path | None:
    if sys.platform != "darwin":
        return None
    base = workdir / "icon_1024.png"
    base.write_bytes(render_icon_png(1024))
    iconset = workdir / "Mucaro.iconset"
    iconset.mkdir(exist_ok=True)
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = points * scale
            name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
            _run(["sips", "-z", str(pixels), str(pixels), str(base), "--out", str(iconset / name)])
    icns = workdir / "Mucaro.icns"
    _run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    return icns


# --- verification steps (macOS)


def verify_app(app: Path, *, version: str, build_number: str, head: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    expected = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": build_number,
        "LSMinimumSystemVersion": MINIMUM_OS,
        "CFBundlePackageType": "APPL",
    }
    checks["info_plist"] = {key: {"expected": value, "actual": info.get(key), "ok": info.get(key) == value} for key, value in expected.items()}
    ats = info.get("NSAppTransportSecurity") or {}
    checks["ats_local_only"] = {"ok": ats.get("NSAllowsLocalNetworking") is True and not ats.get("NSAllowsArbitraryLoads"), "value": ats}
    executable = app / "Contents" / "MacOS" / str(info.get("CFBundleExecutable"))
    archs = _run(["lipo", "-archs", str(executable)], check=False).stdout.split()
    checks["architecture"] = {"ok": "arm64" in archs, "archs": archs}
    verify = _run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], check=False)
    details = _run(["codesign", "-dv", str(app)], check=False)
    checks["codesign"] = {
        "ok": verify.returncode == 0,
        "identity": "ad-hoc" if "Signature=adhoc" in details.stderr else "other",
        "verify_output": verify.stderr.strip()[-2000:],
    }
    gatekeeper = _run(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)], check=False)
    checks["gatekeeper"] = {
        "accepted": gatekeeper.returncode == 0,
        "output": gatekeeper.stderr.strip()[-500:],
        "note": "Ad-hoc builds are expected to be rejected; Developer ID + notarization is BLK-SIGN-001.",
    }
    forbidden = sorted(path.relative_to(app).as_posix() for path in app.rglob("*") if path.name in FORBIDDEN_BUNDLE_NAMES)
    checks["no_user_data_or_secrets_in_bundle"] = {"ok": not forbidden, "found": forbidden}
    resources = app / "Contents" / "Resources"
    checks["bundled_gui"] = {"ok": (resources / "frontend" / "dist" / "index.html").exists()}
    checks["bundled_schemas"] = {"ok": (resources / "schemas").is_dir()}
    identity_path = resources / "mucaro_build_identity.json"
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        identity = {}
    checks["bundled_source_identity"] = {
        "ok": identity.get("schema_version") == "mucaro-build-identity/1.0" and identity.get("source_head") == head,
        "source_head": identity.get("source_head"),
    }
    return checks


def smoke(executable: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mucaro-smoke-") as data_dir:
        env = {key: value for key, value in os.environ.items() if not key.startswith("MUCARO_AI_")}
        result = _run([str(executable), "--smoke", "--data-dir", data_dir], env=env, check=False)
    return {"ok": result.returncode == 0 and "DESKTOP_SMOKE_OK" in result.stdout, "returncode": result.returncode, "stdout": result.stdout.strip()[-500:], "stderr": result.stderr.strip()[-2000:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, help="Default: data/exports/macos_app/<utc>-<head12>")
    parser.add_argument("--build-frontend", action="store_true", help="Run npm ci && npm run build first.")
    parser.add_argument("--allow-dirty", action="store_true", help="Build from a modified tree (recorded in the receipt).")
    parser.add_argument("--structural", action="store_true", help="Non-macOS: build and smoke the onedir tree only.")
    args = parser.parse_args()

    on_macos = sys.platform == "darwin"
    if not on_macos and not args.structural:
        print("ERROR_MACOS_REQUIRED The .app build needs macOS (use --structural for a spec check).", file=sys.stderr)
        return 2
    if on_macos and platform.machine() != "arm64":
        print(f"ERROR_ARM64_REQUIRED host is {platform.machine()}", file=sys.stderr)
        return 2

    head = _git("rev-parse", "HEAD")
    worktree_clean = not _git("status", "--porcelain")
    if not worktree_clean and not args.allow_dirty:
        print("ERROR_WORKTREE_DIRTY commit first or pass --allow-dirty", file=sys.stderr)
        return 2
    try:
        import PyInstaller  # noqa: F401 - presence check only
        from PyInstaller import __version__ as pyinstaller_version
    except ImportError:
        print("ERROR_PYINSTALLER_MISSING install packaging/macos/requirements-macos-arm64.lock", file=sys.stderr)
        return 2
    if pyinstaller_version != PYINSTALLER_VERSION:
        print(f"ERROR_PYINSTALLER_VERSION expected {PYINSTALLER_VERSION}, found {pyinstaller_version}", file=sys.stderr)
        return 2

    if args.build_frontend:
        _run(["npm", "ci"], cwd=ROOT / "frontend")
        _run(["npm", "run", "build"], cwd=ROOT / "frontend")
    index = ROOT / "frontend" / "dist" / "index.html"
    if not index.exists():
        print("ERROR_FRONTEND_NOT_BUILT run with --build-frontend", file=sys.stderr)
        return 2

    version = _version()
    build_number = _git("rev-list", "--count", "HEAD")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output_dir or ROOT / "data" / "exports" / "macos_app" / f"{stamp}-{head[:12]}").resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_identity = output / "mucaro_build_identity.json"
    build_identity.write_text(
        json.dumps(
            {
                "schema_version": "mucaro-build-identity/1.0",
                "source_head": head,
                "release_version": version,
                "build_number": build_number,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    work = output / "pyinstaller"
    icon = build_icns(output)
    env = dict(os.environ)
    env.update(
        {
            "MUCARO_APP_VERSION": version,
            "MUCARO_APP_BUILD": build_number,
            "MUCARO_APP_BUNDLE_ID": BUNDLE_ID,
            "MUCARO_APP_MINIMUM_OS": MINIMUM_OS,
            "MUCARO_APP_ICON": str(icon) if icon else "",
            "MUCARO_BUILD_IDENTITY_FILE": str(build_identity),
        }
    )
    started = datetime.now(UTC)
    build = _run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(work / "dist"), "--workpath", str(work / "build"), str(SPEC)],
        env=env,
        check=False,
    )
    (output / "pyinstaller.log").write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode != 0:
        print(f"ERROR_PYINSTALLER_FAILED see {output / 'pyinstaller.log'}", file=sys.stderr)
        return 1

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": started.isoformat(),
        "head_sha": head,
        "worktree_clean": worktree_clean,
        "version": version,
        "build_number": build_number,
        "bundle_id": BUNDLE_ID,
        "minimum_os": MINIMUM_OS,
        "host": {"platform": sys.platform, "machine": platform.machine(), "mac_ver": platform.mac_ver()[0], "python": platform.python_version()},
        "pyinstaller": pyinstaller_version,
        "inputs": {
            "spec_sha256": _sha256(SPEC),
            "lock_sha256": _sha256(LOCK),
            "frontend_index_sha256": _sha256(index),
            "build_identity_sha256": _sha256(build_identity),
        },
        "signing": {"identity": "ad-hoc", "developer_id": False, "notarized": False, "stapled": False, "blocker": "BLK-SIGN-001"},
    }
    if on_macos:
        built = work / "dist" / f"{APP_NAME}.app"
        app = output / f"{APP_NAME}.app"
        if app.exists():
            shutil.rmtree(app)
        _run(["ditto", str(built), str(app)])
        # Seal the finished bundle as a whole (PyInstaller signs its parts ad-hoc).
        _run(["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", str(app)])
        checks = verify_app(app, version=version, build_number=build_number, head=head)
        checks["smoke"] = smoke(app / "Contents" / "MacOS" / APP_NAME)
        rows = tree_manifest(app)
        archive = output / f"Mucaro-AI-{version}-{build_number}-macos-arm64.zip"
        _run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)])
        receipt["artifact"] = {"app": str(app), "zip": str(archive), "zip_sha256": _sha256(archive), "zip_bytes": archive.stat().st_size}
        blocking = [name for name, check in checks.items() if name != "gatekeeper" and not _check_ok(check)]
        receipt["status"] = "MACOS_APP_BUILD_OK" if not blocking else "MACOS_APP_BUILD_FAILED"
    else:
        tree = work / "dist" / APP_NAME
        checks = {"smoke": smoke(tree / APP_NAME)}
        checks["bundled_gui"] = {"ok": any(tree.rglob("frontend/dist/index.html"))}
        checks["bundled_schemas"] = {"ok": any(path.is_dir() for path in tree.rglob("schemas"))}
        checks["bundled_source_identity"] = {"ok": any(tree.rglob("mucaro_build_identity.json"))}
        rows = tree_manifest(tree)
        blocking = [name for name, check in checks.items() if not _check_ok(check)]
        receipt["status"] = "STRUCTURAL_BUILD_OK" if not blocking else "STRUCTURAL_BUILD_FAILED"
        receipt["artifact"] = {"onedir": str(tree), "distributable": False}
    receipt["checks"] = checks
    receipt["blocking_checks"] = blocking
    receipt["bundle"] = {"files": len(rows), "tree_sha256": tree_digest(rows), "bytes": sum(int(row.get("bytes", 0)) for row in rows)}
    (output / "bundle_manifest.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "head_sha": head, "files": rows}, indent=2) + "\n", encoding="utf-8")
    (output / "build_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "output": str(output), "blocking_checks": blocking}))
    return 0 if not blocking else 1


def _check_ok(check: Any) -> bool:
    if isinstance(check, dict) and "ok" in check:
        return bool(check["ok"])
    if isinstance(check, dict):
        return all(_check_ok(value) for value in check.values())
    return True


if __name__ == "__main__":
    raise SystemExit(main())
