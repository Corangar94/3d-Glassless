"""Transactional installer for the offline-only ReShade add-on backend."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from collections.abc import Generator, Mapping
from pathlib import Path, PureWindowsPath
import tempfile
from tracker.file_transactions import atomic_write, path_lock, reject_link

from launcher.game_profiles import Backend, PolicyDecision


_PROXY_API = {
    "dxgi.dll": frozenset({"d3d10", "d3d11", "d3d12"}),
    "d3d11.dll": frozenset({"d3d11"}),
    "d3d9.dll": frozenset({"d3d9"}),
}
_PE_MACHINE = {0x014C: "x86", 0x8664: "x64"}
_INSTALL_MANIFEST = ".glassless3d-reshade.json"
_BACKUP_DIR = ".glassless3d-reshade-backup"
_ASSET_MANIFEST = "reshade-assets.json"
_TRUSTED_RESHADE_HASHES = {
    "ReShade32.dll": "b0a0fa7472d9a153816edcf7606902eb9c8f262e6100fc9973ec495634dca2c2",
    "ReShade64.dll": "ec9245d05c11751f2ac0d2256e6921ad8fb36be9172ef6d587856591eb729a25",
}
_RESHADE_VERSION = "6.7.3"


class InstallError(Exception):
    """Raised when an installation, repair, or uninstall step fails."""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


def _bundle_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_architecture(path: str | os.PathLike[str]) -> str:
    """Return ``x86`` or ``x64`` from a Windows PE header, failing closed."""
    file_path = Path(path)
    try:
        with file_path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("missing DOS signature")
            stream.seek(0x3C)
            pe_offset_data = stream.read(4)
            if len(pe_offset_data) != 4:
                raise ValueError("truncated DOS header")
            pe_offset = struct.unpack("<I", pe_offset_data)[0]
            if pe_offset < 0x40 or pe_offset > file_path.stat().st_size - 6:
                raise ValueError("invalid PE header offset")
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                raise ValueError("missing PE signature")
            machine_data = stream.read(2)
            if len(machine_data) != 2:
                raise ValueError("truncated COFF header")
            machine = struct.unpack("<H", machine_data)[0]
    except OSError as exc:
        raise InstallError("Architecture check", f"cannot read {file_path}: {exc}") from exc
    if machine not in _PE_MACHINE:
        raise InstallError("Architecture check", f"unsupported PE machine 0x{machine:04X}: {file_path}")
    return _PE_MACHINE[machine]


def _load_asset_manifest(base: Path) -> dict:
    path = base / _ASSET_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError("Verifying bundle", f"cannot read {_ASSET_MANIFEST}: {exc}") from exc
    if data.get("reshade_version") != _RESHADE_VERSION or not isinstance(data.get("assets"), dict):
        raise InstallError("Verifying bundle", "unsupported or malformed asset manifest")
    return data


def _verified_asset(base: Path, manifest: Mapping, name: str, arch: str) -> Path:
    entry = manifest.get("assets", {}).get(name)
    if not isinstance(entry, dict) or entry.get("arch") != arch:
        raise InstallError("Verifying bundle", f"no trusted {arch} metadata for {name}")
    path = base / name
    if not path.is_file():
        raise InstallError("Verifying bundle", f"required asset is missing: {name}")
    expected = str(entry.get("sha256", "")).lower()
    pinned = _TRUSTED_RESHADE_HASHES.get(name)
    if pinned is not None and expected != pinned:
        raise InstallError("Verifying bundle", f"untrusted ReShade {_RESHADE_VERSION} hash for {name}")
    if len(expected) != 64 or _sha256(path) != expected:
        raise InstallError("Verifying bundle", f"SHA-256 mismatch for {name}")
    if pe_architecture(path) != arch:
        raise InstallError("Verifying bundle", f"architecture mismatch for {name}")
    return path


def _set_ini_values(path: Path, section: str, values: Mapping[str, object]) -> None:
    """Update one INI section without deleting or relocating unrelated sections."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    target = section.casefold()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().casefold()
            if start is not None:
                end = index
                break
            if current == target:
                start = index
    rendered = {str(key).casefold(): f"{key}={value}" for key, value in values.items()}
    if start is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([f"[{section}]", *rendered.values()])
    else:
        seen: set[str] = set()
        for index in range(start + 1, end):
            line = lines[index]
            if "=" not in line or line.lstrip().startswith((";", "#")):
                continue
            key = line.split("=", 1)[0].strip().casefold()
            if key in rendered:
                lines[index] = rendered[key]
                seen.add(key)
        missing = [value for key, value in rendered.items() if key not in seen]
        lines[end:end] = missing
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_configuration(game_dir: Path, profile_name: str, base: Path) -> None:
    profile_path = base / "profiles" / f"{profile_name}.json"
    if not profile_path.exists():
        profile_path = base / "profiles" / "default.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    depth_settings = profile.get("reshade", {})
    shader_defaults = profile.get("shader_defaults", {})
    if not isinstance(depth_settings, dict) or not isinstance(shader_defaults, dict):
        raise ValueError("profile ReShade settings must be objects")

    ini_path = game_dir / "ReShade.ini"
    _set_ini_values(ini_path, "INPUT", {"KeyOverlay": "36,0,0,0"})
    _set_ini_values(
        ini_path,
        "GENERAL",
        {
            "PresetPath": r".\Glassless3D.ini",
            "EffectSearchPaths": r".\reshade-shaders\Shaders",
            "TextureSearchPaths": r".\reshade-shaders\Textures",
        },
    )
    if depth_settings:
        _set_ini_values(ini_path, "PREPROCESSOR", depth_settings)

    preset = game_dir / "Glassless3D.ini"
    preset.write_text("Techniques=Glassless3D\nTechniqueSorting=Glassless3D\n", encoding="utf-8")
    if shader_defaults:
        _set_ini_values(preset, "Glassless3D.fx", shader_defaults)


_MANAGED_PATHS = frozenset({
    "dxgi.dll", "d3d11.dll", "d3d9.dll", "Glassless3D.addon32", "Glassless3D.addon64",
    "ReShade.ini", "Glassless3D.ini", "reshade-shaders/Shaders/Glassless3D.fx",
    "reshade-shaders/Shaders/Glassless3D.fxh", "reshade-shaders/Shaders/ReShade.fxh",
})


def _safe_relative(path: str) -> Path:
    if not isinstance(path, str) or not path:
        raise InstallError("Reading install manifest", "managed path must be nonempty text")
    windows = PureWindowsPath(path)
    parts = path.replace("\\", "/").split("/")
    if (windows.drive or windows.root or ":" in path or "\0" in path
            or any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in parts)
            or "/".join(parts) not in _MANAGED_PATHS):
        raise InstallError("Reading install manifest", f"unsafe or unmanaged path: {path!r}")
    return Path(*parts)


def _inside(root: Path, relative: Path) -> Path:
    """Reject reparse points at every existing component, not just the leaf."""
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise InstallError("Path check", "path escapes the selected directory")
    path = root
    reject_link(path)
    for component in relative.parts:
        path = path / component
        reject_link(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise InstallError("Path check", "resolved path escapes the selected directory")
    if path.exists() and not path.is_file() and path != root:
        raise InstallError("Path check", f"managed destination is not a file: {path}")
    return path


def _read_bytes(path: Path) -> bytes:
    reject_link(path)
    with path.open("rb") as stream:
        data = stream.read(256 * 1024 * 1024 + 1)
    if len(data) > 256 * 1024 * 1024:
        raise InstallError("Preflight", f"managed file exceeds size limit: {path}")
    return data


def _load_install_manifest(game_dir: Path) -> dict | None:
    path = _inside(game_dir, Path(_INSTALL_MANIFEST))
    if not path.exists():
        return None
    try:
        data = json.loads(_read_bytes(path).decode("utf-8"))
    except (OSError, ValueError) as error:
        raise InstallError("Reading install manifest", str(error)) from error
    if not isinstance(data, dict) or data.get("format") != 2 or not isinstance(data.get("files"), list):
        raise InstallError("Reading install manifest", "legacy/invalid ownership metadata; preserve backups for manual review")
    seen = set()
    for record in data["files"]:
        if not isinstance(record, dict):
            raise InstallError("Reading install manifest", "file record must be an object")
        relative = _safe_relative(record.get("path"))
        if relative in seen or type(record.get("backup")) is not bool:
            raise InstallError("Reading install manifest", "duplicate path or invalid original ownership")
        seen.add(relative)
        for key in ("installed_sha256", "original_sha256"):
            value = record.get(key)
            if key == "original_sha256" and not record["backup"] and value is None:
                continue
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise InstallError("Reading install manifest", f"invalid {key}")
        _inside(game_dir, relative)
        _inside(game_dir, Path(_BACKUP_DIR) / relative)
    if not seen:
        raise InstallError("Reading install manifest", "empty ownership manifest")
    return data


def _publish_files(game_dir: Path, changes: dict[Path, bytes | None]) -> None:
    """Snapshot and journal all old bytes before the first destination change.

    Journal and snapshots remain available on failure. A required missing
    backup always stops restoration; it never turns into a delete operation.
    """
    before = {}
    for relative in changes:
        destination = _inside(game_dir, relative)
        before[relative] = _read_bytes(destination) if destination.exists() else None
    journal_dir = Path(tempfile.mkdtemp(prefix=".glassless3d-recovery-", dir=game_dir))
    records = []
    for index, (relative, original) in enumerate(before.items()):
        snapshot = str(index) if original is not None else None
        if original is not None:
            atomic_write(journal_dir / snapshot, original)
        records.append({"path": relative.as_posix(), "snapshot": snapshot,
                        "sha256": hashlib.sha256(original).hexdigest() if original is not None else None})
    atomic_write(journal_dir / "journal.json", json.dumps({"files": records}, indent=2).encode("utf-8"))
    touched = []
    try:
        for relative, value in changes.items():
            destination = _inside(game_dir, relative)
            current = _read_bytes(destination) if destination.exists() else None
            if current != before[relative]:
                raise InstallError("Publishing", f"file changed since preflight: {relative}")
            touched.append(relative)
            if value is None:
                destination.unlink(missing_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                _inside(game_dir, relative)
                atomic_write(destination, value)
    except BaseException as original_error:
        failures = []
        for relative in reversed(touched):
            try:
                destination = _inside(game_dir, relative)
                original = before[relative]
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    atomic_write(destination, original)
            except Exception as error:
                failures.append(f"{relative}: {error}")
        raise InstallError("Publishing", f"operation failed; recovery retained in {journal_dir.name}; "
                           + ("rollback incomplete: " + "; ".join(failures) if failures else "old files restored")) from original_error
    # Remove only our own journal files after a fully successful commit.
    for record in records:
        if record["snapshot"] is not None:
            (journal_dir / record["snapshot"]).unlink()
    (journal_dir / "journal.json").unlink()
    journal_dir.rmdir()


def _restore(game_dir: Path, records: list[dict]) -> None:
    changes = {}
    for record in records:
        relative = _safe_relative(record["path"])
        destination = _inside(game_dir, relative)
        if destination.exists() and _sha256(destination) != record["installed_sha256"]:
            raise InstallError("Uninstall", f"managed file was edited; preserve/review it first: {relative}")
        if record["backup"]:
            backup = _inside(game_dir, Path(_BACKUP_DIR) / relative)
            if not backup.is_file() or _sha256(backup) != record["original_sha256"]:
                raise InstallError("Uninstall", f"original backup absent or damaged: {relative}; nothing changed")
            changes[relative] = _read_bytes(backup)
        else:
            changes[relative] = None
    changes[Path(_INSTALL_MANIFEST)] = None
    _publish_files(game_dir, changes)
    # Keep immutable originals after uninstall as recovery material. Never
    # recursively delete a directory supplied by a manifest or game layout.


def _install_steps_locked(
    game_dir: str,
    profile_name: str = "default",
    *,
    policy: PolicyDecision,
    game_executable: str,
    graphics_api: str,
    proxy_name: str | None = None,
    repair: bool = False,
) -> Generator[str, None, None]:
    """Install verified assets transactionally into an offline game directory."""
    if not policy.allows(Backend.RESHADE_ADDON):
        raise InstallError("Policy check", "ReShade add-ons are permitted only for acknowledged offline single-player profiles")

    target = Path(game_dir).resolve()
    executable = Path(game_executable).resolve()
    if not target.is_dir() or not executable.is_file() or executable.parent != target:
        raise InstallError("Target check", "game executable must exist directly inside the selected game directory")
    api = graphics_api.strip().lower()
    if api in {"opengl", "vulkan"}:
        raise InstallError("Selecting graphics API", f"{api} installation is not supported by this installer")
    selected_proxy = (proxy_name or ("d3d9.dll" if api == "d3d9" else "dxgi.dll")).strip().lower()
    if selected_proxy not in _PROXY_API or api not in _PROXY_API[selected_proxy]:
        raise InstallError("Selecting ReShade proxy", f"proxy {selected_proxy!r} is not valid for {api!r}")

    arch = pe_architecture(executable)
    base = Path(_bundle_dir())
    asset_manifest = _load_asset_manifest(base)
    reshade_name = f"ReShade{'32' if arch == 'x86' else '64'}.dll"
    addon_name = f"Glassless3D.addon{'32' if arch == 'x86' else '64'}"
    reshade = _verified_asset(base, asset_manifest, reshade_name, arch)
    addon = _verified_asset(base, asset_manifest, addon_name, arch)

    source_files = {
        Path(selected_proxy): reshade,
        Path(addon_name): addon,
        Path("reshade-shaders/Shaders/Glassless3D.fx"): base / "shaders/Glassless3D.fx",
        Path("reshade-shaders/Shaders/Glassless3D.fxh"): base / "shaders/Glassless3D.fxh",
        Path("reshade-shaders/Shaders/ReShade.fxh"): base / "shaders/ReShade.fxh",
    }
    for source in source_files.values():
        if not source.is_file():
            raise InstallError("Verifying bundle", f"required asset is missing: {source.name}")

    previous = _load_install_manifest(target)
    if previous is not None and not repair:
        raise InstallError("Preflight", "Glassless3D ReShade is already installed; use repair=True")
    managed = {_safe_relative(item["path"]) for item in (previous or {}).get("files", [])}
    planned = [*source_files, Path("ReShade.ini"), Path("Glassless3D.ini")]
    # Existing configuration is expected and is edited section-by-section after
    # being backed up. Executable/add-on/shader collisions are never guessed at.
    configurable = {Path("ReShade.ini"), Path("Glassless3D.ini")}
    collisions = [
        str(rel) for rel in planned
        if rel not in configurable and (target / rel).exists() and rel not in managed
    ]
    if collisions:
        raise InstallError("Preflight", "refusing to overwrite unowned files: " + ", ".join(collisions))

    if previous is not None and (
        previous.get("architecture") != arch or previous.get("graphics_api") != api
        or previous.get("proxy") != selected_proxy
        or managed != set(planned)
    ):
        raise InstallError("Repair", "layout changed; uninstall the old layout before installing another")
    for relative in planned:
        _inside(target, relative)
        _inside(target, Path(_BACKUP_DIR) / relative)
    # Preparation occurs only in a fresh directory. The live game is untouched
    # until every asset, configuration, backup and ownership record is ready.
    staging = Path(tempfile.mkdtemp(prefix=".glassless3d-stage-", dir=target))
    for relative, source in source_files.items():
        atomic_write(staging / relative, _read_bytes(source))
    for relative in configurable:
        destination = _inside(target, relative)
        if destination.exists():
            atomic_write(staging / relative, _read_bytes(destination))
    _write_configuration(staging, profile_name, base)
    previous_records = {_safe_relative(item["path"]): item for item in (previous or {}).get("files", [])}
    records = []
    changes = {}
    for relative in planned:
        destination = _inside(target, relative)
        prior = previous_records.get(relative)
        # The first install's ownership is immutable, even when our installed
        # proxy now exists. Repair snapshots never become uninstall originals.
        had_original = prior["backup"] if prior is not None else destination.exists()
        backup = _inside(target, Path(_BACKUP_DIR) / relative)
        original_sha = prior.get("original_sha256") if prior is not None else None
        if prior is not None and relative not in configurable and destination.exists():
            if _sha256(destination) != prior["installed_sha256"]:
                raise InstallError("Repair", f"managed asset has been changed: {relative}; staging retained")
        if had_original:
            if prior is None:
                original = _read_bytes(destination)
                original_sha = hashlib.sha256(original).hexdigest()
                if backup.exists() and _sha256(backup) != original_sha:
                    raise InstallError("Backup", f"conflicting recovery backup: {relative}; nothing changed")
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    _inside(target, Path(_BACKUP_DIR) / relative)
                    atomic_write(backup, original)
            if not backup.is_file() or _sha256(backup) != original_sha:
                raise InstallError("Backup", f"required original backup missing or incomplete: {relative}")
        value = _read_bytes(staging / relative)
        records.append({"path": relative.as_posix(), "backup": had_original,
                        "original_sha256": original_sha,
                        "installed_sha256": hashlib.sha256(value).hexdigest()})
        changes[relative] = value
    installed = {"format": 2, "offline_only": True, "architecture": arch,
                 "graphics_api": api, "proxy": selected_proxy, "files": records}
    changes[Path(_INSTALL_MANIFEST)] = (json.dumps(installed, indent=2) + "\n").encode("utf-8")
    _publish_files(target, changes)
    try:
        for relative in planned:
            _inside(staging, relative).unlink(missing_ok=True)
        parents = {parent for relative in planned for parent in (staging / relative).parents
                   if parent != staging and parent.is_relative_to(staging)}
        for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
            reject_link(parent)
            parent.rmdir()
        staging.rmdir()
    except OSError:
        pass  # Committed installation is sound; retain any remaining staging.
    yield "Installed verified assets and configuration; ownership manifest committed"


def install_steps(game_dir: str, profile_name: str = "default", *, policy: PolicyDecision,
                  game_executable: str, graphics_api: str, proxy_name: str | None = None,
                  repair: bool = False) -> Generator[str, None, None]:
    if not policy.allows(Backend.RESHADE_ADDON):
        raise InstallError("Policy check", "ReShade add-ons are permitted only for acknowledged offline single-player profiles")
    try:
        target = Path(game_dir).resolve(strict=True)
        reject_link(target)
        _inside(target, Path(_INSTALL_MANIFEST + ".lock"))
        with path_lock(target / _INSTALL_MANIFEST):
            yield from _install_steps_locked(str(target), profile_name, policy=policy,
                game_executable=game_executable, graphics_api=graphics_api,
                proxy_name=proxy_name, repair=repair)
    except InstallError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise InstallError("Installing ReShade", str(error)) from error


def install(
    game_dir: str,
    profile_name: str = "default",
    *,
    policy: PolicyDecision,
    game_executable: str,
    graphics_api: str,
    proxy_name: str | None = None,
    repair: bool = False,
) -> None:
    for _ in install_steps(
        game_dir,
        profile_name,
        policy=policy,
        game_executable=game_executable,
        graphics_api=graphics_api,
        proxy_name=proxy_name,
        repair=repair,
    ):
        pass


def uninstall(game_dir: str) -> None:
    """Restore reviewed originals; never guess when metadata/backups are missing."""
    target = Path(game_dir).resolve(strict=True)
    _inside(target, Path(_INSTALL_MANIFEST + ".lock"))
    with path_lock(target / _INSTALL_MANIFEST):
        manifest = _load_install_manifest(target)
        if manifest is None:
            raise InstallError("Uninstall", "no installation manifest found")
        _restore(target, manifest["files"])


def _write_reshade_ini(game_dir: str, profile_name: str, base: str) -> None:
    """Compatibility wrapper retained for callers that only update settings."""
    _write_configuration(Path(game_dir), profile_name, Path(base))
