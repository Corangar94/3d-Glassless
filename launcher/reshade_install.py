"""Transactional installer for the offline-only ReShade add-on backend."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from collections.abc import Generator, Mapping
from pathlib import Path

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


def _safe_relative(path: str) -> Path:
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise InstallError("Reading install manifest", f"unsafe managed path: {path!r}")
    return relative


def _load_install_manifest(game_dir: Path) -> dict | None:
    path = game_dir / _INSTALL_MANIFEST
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError("Reading install manifest", str(exc)) from exc
    if data.get("format") != 1 or not isinstance(data.get("files"), list):
        raise InstallError("Reading install manifest", "unsupported manifest format")
    return data


def _restore(game_dir: Path, records: list[dict]) -> None:
    backup_root = game_dir / _BACKUP_DIR
    for record in reversed(records):
        relative = _safe_relative(record["path"])
        destination = game_dir / relative
        backup = backup_root / relative
        if record.get("backup") and backup.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)
        elif destination.exists():
            destination.unlink()
        parent = destination.parent
        while parent != game_dir:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    shutil.rmtree(backup_root, ignore_errors=True)


def install_steps(
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

    backup_root = target / _BACKUP_DIR
    records: list[dict] = []
    try:
        previous_records = {
            _safe_relative(item["path"]): item for item in (previous or {}).get("files", [])
        }
        for relative in planned:
            destination = target / relative
            had_original = destination.exists()
            prior = previous_records.get(relative)
            original_was_backed_up = bool(prior and prior.get("backup"))
            record = {"path": relative.as_posix(), "backup": original_was_backed_up or had_original}
            records.append(record)
            if had_original and not original_was_backed_up:
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)

        for relative, source in source_files.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        yield "Copying verified ReShade assets"

        _write_configuration(target, profile_name, base)
        yield "Writing section-safe configuration"

        installed = {
            "format": 1,
            "offline_only": True,
            "architecture": arch,
            "graphics_api": api,
            "proxy": selected_proxy,
            "files": records,
        }
        (target / _INSTALL_MANIFEST).write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")
        yield "Recording rollback manifest"
    except Exception as exc:
        _restore(target, records)
        if isinstance(exc, InstallError):
            raise
        raise InstallError("Installing ReShade", str(exc)) from exc


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
    """Remove only managed files and restore pre-install backups."""
    target = Path(game_dir).resolve()
    manifest = _load_install_manifest(target)
    if manifest is None:
        raise InstallError("Uninstall", "no Glassless3D ReShade installation manifest found")
    records = manifest["files"]
    _restore(target, records)
    (target / _INSTALL_MANIFEST).unlink(missing_ok=True)
    shutil.rmtree(target / _BACKUP_DIR, ignore_errors=True)


def _write_reshade_ini(game_dir: str, profile_name: str, base: str) -> None:
    """Compatibility wrapper retained for callers that only update settings."""
    _write_configuration(Path(game_dir), profile_name, Path(base))
