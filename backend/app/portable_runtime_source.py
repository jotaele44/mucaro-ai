from __future__ import annotations

import hashlib
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

RUNTIME_SOURCE_SCHEMA_VERSION = "mucaro-portable-runtime-source/1.0"
RUNTIME_RECEIPT_SCHEMA_VERSION = "mucaro-portable-runtime/1.0"
RUNTIME_RECEIPT_NAME = "runtime-provenance.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCK_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", re.MULTILINE)
PROJECT_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


class PortableRuntimeSourceError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_object(path: Path, *, status: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableRuntimeSourceError(status, f"{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PortableRuntimeSourceError(status, f"{path}: expected a JSON object")
    return payload


def _normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_lock(lock_path: Path) -> dict[str, str]:
    try:
        text = lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_LOCK", f"{lock_path}: {exc}") from exc
    starts = list(LOCK_REQUIREMENT_RE.finditer(text))
    if not starts:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_LOCK", "dependency lock has no pinned requirements")
    requirements: dict[str, str] = {}
    for index, match in enumerate(starts):
        name = _normalized_distribution(match.group(1))
        version = match.group(2)
        block_end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.start() : block_end]
        if "--hash=sha256:" not in block:
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_LOCK",
                f"{name} is not protected by a SHA-256 distribution hash",
            )
        if name in requirements:
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_LOCK", f"duplicate requirement: {name}")
        requirements[name] = version
    return requirements


def _project_runtime_requirements(root: Path) -> dict[str, str]:
    pyproject_path = root / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_PROJECT_METADATA", str(exc)) from exc
    dependencies = payload.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_PROJECT_METADATA", "project.dependencies is required")
    result: dict[str, str] = {}
    for value in dependencies:
        if not isinstance(value, str):
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_PROJECT_METADATA", "dependency must be a string")
        match = PROJECT_REQUIREMENT_RE.fullmatch(value.strip())
        if match is None:
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_PROJECT_DEPENDENCY",
                f"runtime dependency must use one exact version: {value}",
            )
        name = _normalized_distribution(match.group(1))
        if name in result:
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_PROJECT_DEPENDENCY", f"duplicate dependency: {name}")
        result[name] = match.group(2)
    return result


def load_runtime_source_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_SOURCE_MANIFEST",
            "runtime source manifest must be inside the project root",
        ) from exc
    payload = _read_object(manifest_path, status="ERROR_PORTABLE_RUNTIME_SOURCE_MANIFEST")
    if payload.get("schema_version") != RUNTIME_SOURCE_SCHEMA_VERSION:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_SOURCE_SCHEMA",
            str(payload.get("schema_version")),
        )
    source = payload.get("source")
    target = payload.get("target")
    archive_policy = payload.get("archive_policy")
    if not all(isinstance(item, dict) for item in (source, target, archive_policy)):
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_SOURCE_MANIFEST",
            "source, target, and archive_policy must be objects",
        )
    if not SHA256_RE.fullmatch(str(source.get("sha256") or "")):
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_HASH", str(source.get("sha256")))
    url = urllib.parse.urlparse(str(source.get("url") or ""))
    if url.scheme != "https" or url.hostname != "github.com":
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_URL", str(source.get("url")))
    for field in ("bytes",):
        if not isinstance(source.get(field), int) or source[field] <= 0:
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_MANIFEST", f"invalid source.{field}")
    for field in (
        "member_count",
        "regular_file_count",
        "symlink_count",
        "total_regular_bytes",
        "max_members",
        "max_regular_bytes",
    ):
        if not isinstance(archive_policy.get(field), int) or archive_policy[field] < 0:
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_MANIFEST", f"invalid archive_policy.{field}")
    for field in ("logical_members_sha256", "payload_multiset_sha256"):
        value = archive_policy.get(field)
        if value is not None and not SHA256_RE.fullmatch(str(value)):
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_SOURCE_HASH",
                f"archive_policy.{field}={value}",
            )
    if archive_policy["member_count"] > archive_policy["max_members"]:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_LIMIT", "member count exceeds policy maximum")
    if archive_policy["total_regular_bytes"] > archive_policy["max_regular_bytes"]:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_LIMIT", "archive bytes exceed policy maximum")

    lock_value = payload.get("dependency_lock")
    if not isinstance(lock_value, str) or not lock_value:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_LOCK", "dependency_lock is required")
    lock_path = (root / lock_value).resolve()
    try:
        lock_path.relative_to(root)
    except ValueError as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_LOCK", "dependency lock escapes project root") from exc
    if not lock_path.is_file():
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_LOCK", str(lock_path))
    requirements = _parse_lock(lock_path)
    direct_requirements = _project_runtime_requirements(root)
    mismatches = {
        name: {"project": version, "lock": requirements.get(name)}
        for name, version in direct_requirements.items()
        if requirements.get(name) != version
    }
    if mismatches:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_LOCK_PROJECT_MISMATCH",
            json.dumps(mismatches, sort_keys=True),
        )
    required_imports = payload.get("required_imports")
    if not isinstance(required_imports, list) or not required_imports or not all(
        isinstance(item, str) and item for item in required_imports
    ):
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_MANIFEST", "required_imports must be strings")
    payload["_manifest_path"] = str(manifest_path)
    payload["_manifest_sha256"] = _sha256(manifest_path)
    payload["_lock_path"] = str(lock_path)
    payload["_lock_sha256"] = _sha256(lock_path)
    payload["_requirements"] = requirements
    payload["_direct_requirements"] = direct_requirements
    return payload


def _safe_archive_member(name: str, archive_root: str) -> Path:
    parsed = PurePosixPath(name)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts or parsed.parts[0] != archive_root:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_PATH", name)
    relative = parsed.relative_to(archive_root)
    if not relative.parts:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_PATH", name)
    return Path(*relative.parts)


def _safe_symlink_target(member_name: str, link_name: str, archive_root: str) -> Path:
    target = PurePosixPath(link_name)
    if target.is_absolute():
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET", f"{member_name} -> {link_name}")
    normalized = PurePosixPath(posixpath.normpath(str(PurePosixPath(member_name).parent / target)))
    if ".." in normalized.parts or not normalized.parts or normalized.parts[0] != archive_root:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET", f"{member_name} -> {link_name}")
    relative = normalized.relative_to(archive_root)
    if not relative.parts:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET", f"{member_name} -> {link_name}")
    return Path(*relative.parts)


def inspect_runtime_archive(archive_path: Path) -> dict[str, Any]:
    """Inspect one runtime tarball without extracting it.

    The report is suitable for freezing archive-policy evidence. It derives the
    single archive root from member paths, rejects unsafe/duplicate/unsupported
    members and unresolved or cyclic symlinks, hashes every regular member, and
    records both path-sensitive logical identity and a path-insensitive payload
    multiset identity.
    """
    archive_path = archive_path.resolve()
    try:
        archive = tarfile.open(archive_path, "r:gz")  # noqa: SIM115 - closed by the adjacent context manager
    except (OSError, tarfile.TarError) as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE", str(exc)) from exc

    with archive:
        members = archive.getmembers()
        if not members:
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE", "archive has no members")
        unsupported = [member for member in members if not (member.isfile() or member.issym())]
        if unsupported:
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_ARCHIVE_TYPE",
                ",".join(member.name for member in unsupported[:10]),
            )

        roots: set[str] = set()
        for member in members:
            parsed = PurePosixPath(member.name)
            if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_PATH", member.name)
            roots.add(parsed.parts[0])
        if len(roots) != 1:
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_ARCHIVE_ROOT",
                ",".join(sorted(roots)),
            )
        archive_root = next(iter(roots))
        if not archive_root or "/" in archive_root:
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_ROOT", archive_root)

        logical_rows: list[dict[str, Any]] = []
        payload_rows: list[dict[str, Any]] = []
        destinations: set[str] = set()
        regular_paths: set[str] = set()
        links: dict[str, str] = {}

        for member in members:
            relative = _safe_archive_member(member.name, archive_root).as_posix()
            if relative in destinations:
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_DUPLICATE", member.name)
            destinations.add(relative)
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_READ", member.name)
                digest = hashlib.sha256()
                observed_bytes = 0
                with source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                        observed_bytes += len(block)
                if observed_bytes != member.size:
                    raise PortableRuntimeSourceError(
                        "ERROR_PORTABLE_RUNTIME_ARCHIVE_READ",
                        f"{member.name}: expected={member.size} observed={observed_bytes}",
                    )
                sha256 = digest.hexdigest()
                logical_rows.append(
                    {
                        "bytes": member.size,
                        "path": relative,
                        "sha256": sha256,
                        "type": "file",
                    }
                )
                payload_rows.append({"bytes": member.size, "sha256": sha256})
                regular_paths.add(relative)
            else:
                target = _safe_symlink_target(member.name, member.linkname, archive_root).as_posix()
                logical_rows.append({"path": relative, "target": target, "type": "symlink"})
                links[relative] = target

        for start in sorted(links):
            seen: set[str] = set()
            target = start
            while target in links:
                if target in seen:
                    raise PortableRuntimeSourceError(
                        "ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET",
                        f"cyclic symlink chain from {start}",
                    )
                seen.add(target)
                target = links[target]
            if target not in regular_paths:
                raise PortableRuntimeSourceError(
                    "ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET",
                    f"{start} -> {target}",
                )

        logical_rows.sort(key=lambda row: row["path"])
        payload_rows.sort(key=lambda row: (row["bytes"], row["sha256"]))
        regular = [member for member in members if member.isfile()]
        symlinks = [member for member in members if member.issym()]
        return {
            "archive_bytes": archive_path.stat().st_size,
            "archive_root": archive_root,
            "archive_sha256": _sha256(archive_path),
            "logical_members_sha256": _canonical_sha256(logical_rows),
            "member_count": len(members),
            "payload_multiset_sha256": _canonical_sha256(payload_rows),
            "regular_file_count": len(regular),
            "symlink_count": len(symlinks),
            "total_regular_bytes": sum(member.size for member in regular),
        }


def extract_runtime_archive(archive_path: Path, destination: Path, manifest: dict[str, Any]) -> dict[str, int]:
    archive_root = str(manifest["target"].get("archive_root") or "")
    policy = manifest["archive_policy"]
    if not archive_root or PurePosixPath(archive_root).is_absolute() or "/" in archive_root:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_ROOT", archive_root)

    inspection = inspect_runtime_archive(archive_path)
    if inspection["archive_root"] != archive_root:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_ARCHIVE_ROOT",
            f"expected={archive_root} observed={inspection['archive_root']}",
        )
    for field in ("member_count", "regular_file_count", "symlink_count", "total_regular_bytes"):
        if inspection[field] != policy[field]:
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_ARCHIVE_IDENTITY",
                f"{field}: expected={policy[field]} observed={inspection[field]}",
            )
    for field in ("logical_members_sha256", "payload_multiset_sha256"):
        expected = policy.get(field)
        if expected is not None and inspection[field] != expected:
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_ARCHIVE_IDENTITY",
                f"{field}: expected={expected} observed={inspection[field]}",
            )
    if inspection["member_count"] > policy["max_members"] or inspection["total_regular_bytes"] > policy["max_regular_bytes"]:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_ARCHIVE_LIMIT",
            json.dumps(
                {
                    "member_count": inspection["member_count"],
                    "total_regular_bytes": inspection["total_regular_bytes"],
                },
                sort_keys=True,
            ),
        )
    try:
        archive = tarfile.open(archive_path, "r:gz")  # noqa: SIM115 - closed by the adjacent context manager
    except (OSError, tarfile.TarError) as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE", str(exc)) from exc

    with archive:
        members = archive.getmembers()
        regular = [member for member in members if member.isfile()]
        symlinks = [member for member in members if member.issym()]
        counts = {
            "member_count": inspection["member_count"],
            "regular_file_count": inspection["regular_file_count"],
            "symlink_count": inspection["symlink_count"],
            "total_regular_bytes": inspection["total_regular_bytes"],
        }

        destinations: dict[str, Path] = {}
        for member in members:
            relative = _safe_archive_member(member.name, archive_root)
            key = relative.as_posix()
            if key in destinations:
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_DUPLICATE", member.name)
            destinations[key] = destination / relative
        for member in symlinks:
            _safe_symlink_target(member.name, member.linkname, archive_root)

        destination.mkdir(parents=True, exist_ok=False)
        for member in regular:
            target = destinations[_safe_archive_member(member.name, archive_root).as_posix()]
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE_READ", member.name)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(member.mode & 0o777)

        pending = list(symlinks)
        while pending:
            next_pending: list[tarfile.TarInfo] = []
            progress = False
            for member in pending:
                target = destinations[_safe_archive_member(member.name, archive_root).as_posix()]
                source = destination / _safe_symlink_target(member.name, member.linkname, archive_root)
                if source.is_file() and not source.is_symlink():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    progress = True
                else:
                    next_pending.append(member)
            if not progress:
                unresolved = ",".join(f"{item.name}->{item.linkname}" for item in next_pending[:10])
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SYMLINK_TARGET", unresolved)
            pending = next_pending
    return counts


def _isolated_python_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON") and not key.startswith("PIP_")}
    env.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_CONFIG_FILE": os.devnull,
        }
    )
    return env


def _run(command: list[str], *, input_text: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            env=_isolated_python_env(),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_COMMAND", str(exc)) from exc


def _runtime_probe(runtime_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    runtime_python = runtime_dir / str(manifest["target"]["runtime_python"])
    if not runtime_python.is_file() or not os.access(runtime_python, os.X_OK):
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_PYTHON", str(runtime_python))
    script = """
import importlib
import importlib.metadata
import json
import platform
import site
import sys

payload = json.loads(sys.stdin.read())
for module in payload["imports"]:
    importlib.import_module(module)
versions = {name: importlib.metadata.version(name) for name in payload["requirements"]}
user_site = site.getusersitepackages()
print(json.dumps({
    "base_prefix": sys.base_prefix,
    "executable": sys.executable,
    "machine": platform.machine(),
    "platform": sys.platform,
    "python_version": platform.python_version(),
    "required_imports": payload["imports"],
    "requirements": versions,
    "user_site": user_site,
    "user_site_present": user_site in sys.path,
}, sort_keys=True))
"""
    proc = _run(
        [str(runtime_python), "-s", "-P", "-B", "-c", script],
        input_text=json.dumps(
            {
                "imports": manifest["required_imports"],
                "requirements": sorted(manifest["_requirements"]),
            }
        ),
    )
    if proc.returncode != 0:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_IMPORT_PROBE", proc.stdout[-65536:])
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_IMPORT_PROBE", proc.stdout[-65536:]) from exc
    expected = manifest["target"]
    failures = []
    for key in ("platform", "machine", "python_version"):
        if payload.get(key) != expected.get(key):
            failures.append(f"{key}: expected={expected.get(key)} observed={payload.get(key)}")
    if payload.get("requirements") != manifest["_requirements"]:
        failures.append("installed distributions do not match the dependency lock")
    if payload.get("user_site_present") is not False:
        failures.append("host user-site directory is present on sys.path")
    if failures:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_PROBE_MISMATCH", "; ".join(failures))
    return payload


def _runtime_tree(runtime_dir: Path) -> dict[str, Any]:
    rows = []
    for path in sorted(runtime_dir.rglob("*")):
        if path.is_symlink():
            raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SYMLINK", str(path))
        if path.is_file() and path.name != RUNTIME_RECEIPT_NAME:
            rows.append(
                {
                    "bytes": path.stat().st_size,
                    "executable": os.access(path, os.X_OK),
                    "path": path.relative_to(runtime_dir).as_posix(),
                    "sha256": _sha256(path),
                }
            )
    return {
        "member_count": len(rows),
        "total_bytes": sum(row["bytes"] for row in rows),
        "tree_sha256": _canonical_sha256(rows),
    }


def _download(source: dict[str, Any], target: Path) -> None:
    request = urllib.request.Request(source["url"], headers={"User-Agent": "mucaro-ai-runtime-materializer/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme != "https":
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_URL", response.geturl())
            remaining = source["bytes"] + 1
            while remaining:
                block = response.read(min(1024 * 1024, remaining))
                if not block:
                    break
                output.write(block)
                remaining -= len(block)
            if response.read(1):
                raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_SOURCE_SIZE", "download exceeds expected bytes")
    except PortableRuntimeSourceError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_DOWNLOAD", str(exc)) from exc


def _validate_archive_bytes(archive_path: Path, source: dict[str, Any]) -> None:
    if not archive_path.is_file():
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_ARCHIVE", str(archive_path))
    observed_bytes = archive_path.stat().st_size
    if observed_bytes != source["bytes"]:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_SOURCE_SIZE",
            f"expected={source['bytes']} observed={observed_bytes}",
        )
    observed_sha256 = _sha256(archive_path)
    if observed_sha256 != source["sha256"]:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_SOURCE_HASH",
            f"expected={source['sha256']} observed={observed_sha256}",
        )


def _install_locked_dependencies(runtime_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    runtime_python = runtime_dir / str(manifest["target"]["runtime_python"])
    python_version = str(manifest["target"]["python_version"])
    major_minor = ".".join(python_version.split(".")[:2])
    site_packages = runtime_dir / "lib" / f"python{major_minor}" / "site-packages"
    command = [
        str(runtime_python),
        "-s",
        "-P",
        "-B",
        "-m",
        "pip",
        "--isolated",
        "install",
        "--disable-pip-version-check",
        "--no-compile",
        "--only-binary=:all:",
        "--require-hashes",
        "--upgrade",
        "--target",
        str(site_packages),
        "-r",
        manifest["_lock_path"],
    ]
    proc = _run(command, timeout=900)
    if proc.returncode != 0:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_DEPENDENCIES", proc.stdout[-65536:])
    generated_scripts = site_packages / "bin"
    removed_scripts: list[str] = []
    if generated_scripts.is_dir():
        removed_scripts = sorted(path.name for path in generated_scripts.iterdir())
        shutil.rmtree(generated_scripts)
    check = _run([str(runtime_python), "-s", "-P", "-B", "-m", "pip", "--isolated", "check"])
    if check.returncode != 0:
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_DEPENDENCY_CHECK", check.stdout[-65536:])
    return {
        "lock_path": str(Path(manifest["dependency_lock"])),
        "lock_sha256": manifest["_lock_sha256"],
        "requirement_count": len(manifest["_requirements"]),
        "requirements": manifest["_requirements"],
        "direct_requirements": manifest["_direct_requirements"],
        "removed_console_scripts": removed_scripts,
    }


def _assert_path_absent(runtime_dir: Path, forbidden: Path) -> None:
    needle = os.fsencode(str(forbidden))
    for path in runtime_dir.rglob("*"):
        if path.is_file():
            overlap = b""
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    candidate = overlap + block
                    if needle in candidate:
                        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_CAPTURED_PATH", str(path))
                    overlap = candidate[-max(0, len(needle) - 1) :]


def verify_materialized_runtime(root: Path, runtime_dir: Path, manifest_path: Path) -> dict[str, Any]:
    root = root.resolve()
    runtime_dir = runtime_dir.resolve()
    manifest = load_runtime_source_manifest(root, manifest_path)
    receipt_path = runtime_dir / RUNTIME_RECEIPT_NAME
    receipt = _read_object(receipt_path, status="ERROR_PORTABLE_RUNTIME_RECEIPT")
    failures = []
    if receipt.get("schema_version") != RUNTIME_RECEIPT_SCHEMA_VERSION:
        failures.append("schema_version")
    if receipt.get("source", {}).get("manifest_sha256") != manifest["_manifest_sha256"]:
        failures.append("source_manifest_sha256")
    if receipt.get("source", {}).get("archive_sha256") != manifest["source"]["sha256"]:
        failures.append("source_archive_sha256")
    if receipt.get("dependencies", {}).get("lock_sha256") != manifest["_lock_sha256"]:
        failures.append("dependency_lock_sha256")
    try:
        tree = _runtime_tree(runtime_dir)
    except PortableRuntimeSourceError as exc:
        failures.append(exc.status)
        tree = None
    if tree != receipt.get("runtime_tree"):
        failures.append("runtime_tree")
    try:
        probe = _runtime_probe(runtime_dir, manifest)
    except PortableRuntimeSourceError as exc:
        failures.append(exc.status)
        probe = None
    license_path = runtime_dir / str(manifest.get("license_evidence", {}).get("embedded_python_license") or "")
    if not license_path.is_file():
        failures.append("embedded_python_license")
    return {
        "status": "PORTABLE_RUNTIME_VERIFIED" if not failures else "PORTABLE_RUNTIME_VERIFY_FAILED",
        "runtime_dir": str(runtime_dir),
        "receipt_path": str(receipt_path),
        "receipt_sha256": _sha256(receipt_path) if receipt_path.is_file() else None,
        "source_manifest_sha256": manifest["_manifest_sha256"],
        "dependency_lock_sha256": manifest["_lock_sha256"],
        "runtime_tree": tree,
        "probe": probe,
        "failed_checks": failures,
    }


def materialize_portable_runtime(
    root: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    archive_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = load_runtime_source_manifest(root, manifest_path)
    target = manifest["target"]
    if sys.platform != target["platform"] or platform.machine() != target["machine"]:
        raise PortableRuntimeSourceError(
            "ERROR_PORTABLE_RUNTIME_HOST",
            f"expected={target['platform']}/{target['machine']} observed={sys.platform}/{platform.machine()}",
        )
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise PortableRuntimeSourceError("ERROR_PORTABLE_RUNTIME_OUTPUT_EXISTS", str(output_dir))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / f".{output_dir.name}.staging-{uuid.uuid4().hex}"
    promoted = False
    try:
        with tempfile.TemporaryDirectory(prefix="mucaro-runtime-source-") as temporary:
            if archive_path is None:
                archive = Path(temporary) / manifest["source"]["asset_name"]
                _download(manifest["source"], archive)
            else:
                archive = archive_path.expanduser().resolve()
            _validate_archive_bytes(archive, manifest["source"])
            extraction = extract_runtime_archive(archive, staging, manifest)
        dependencies = _install_locked_dependencies(staging, manifest)
        _runtime_probe(staging, manifest)
        _assert_path_absent(staging, staging)
        runtime_tree = _runtime_tree(staging)
        receipt = {
            "schema_version": RUNTIME_RECEIPT_SCHEMA_VERSION,
            "status": "PORTABLE_RUNTIME_SOURCE_READY",
            "runtime_id": manifest["id"],
            "source": {
                "archive_bytes": manifest["source"]["bytes"],
                "archive_name": manifest["source"]["asset_name"],
                "archive_sha256": manifest["source"]["sha256"],
                "manifest_path": str(Path(manifest_path).resolve().relative_to(root)),
                "manifest_sha256": manifest["_manifest_sha256"],
                "project": manifest["source"]["project"],
                "release": manifest["source"]["release"],
                "url": manifest["source"]["url"],
            },
            "target": target,
            "extraction": {**extraction, "materialized_symlink_count": extraction["symlink_count"]},
            "dependencies": dependencies,
            "runtime_tree": runtime_tree,
            "security": {
                "archive_paths_validated": True,
                "archive_types_limited_to_files_and_symlinks": True,
                "captured_staging_path_absent": True,
                "dependency_hashes_required": True,
                "verification_environment_disables_host_user_site": True,
                "runtime_symlinks_remaining": 0,
                "source_archive_hash_verified": True,
                "source_archive_size_verified": True,
                "source_builds_denied": True,
            },
            "license_evidence": manifest["license_evidence"],
            "limitations": [
                "this runtime is architecture-specific and does not prove arm64 or universal-binary compatibility",
                "runtime materialization is not application signing, notarization, sandbox, or App Review evidence",
                "the exact runtime must still be embedded into and exercised through an exact-head application bundle",
            ],
        }
        (staging / RUNTIME_RECEIPT_NAME).write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staging.rename(output_dir)
        promoted = True
        report = verify_materialized_runtime(root, output_dir, manifest_path)
        if report["status"] != "PORTABLE_RUNTIME_VERIFIED":
            raise PortableRuntimeSourceError(
                "ERROR_PORTABLE_RUNTIME_VERIFY",
                ",".join(report["failed_checks"]),
            )
        return report
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if promoted and output_dir.exists():
            shutil.rmtree(output_dir)
        raise
