from __future__ import annotations

import json
from pathlib import Path
import zipfile

from scripts import check_release_ready, package_windows_release


def _fake_bundle(root: Path) -> Path:
    bundle = root / "dist" / "Glassless3D"
    internal = bundle / "_internal"
    (internal / "models").mkdir(parents=True)
    (bundle / "Glassless3D.exe").write_bytes(b"MZ" + b"L" * 100_000)
    (internal / "Glassless3DOverlay.exe").write_bytes(
        b"MZ" + b"O" * 100_000
    )
    (internal / "onnxruntime.dll").write_bytes(b"ort")
    (internal / "DirectML.dll").write_bytes(b"dml")
    (internal / "models" / "face_landmarker.task").write_bytes(b"face")
    (
        internal / "models" / "depth_anything_v2_small_fp16.onnx"
    ).write_bytes(b"depth")
    return bundle


def _project_root(root: Path, *, licensed: bool = False) -> Path:
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    (project / "README.md").write_text("# Glassless3D\n", encoding="utf-8")
    (project / "docs" / "TROUBLESHOOTING.md").write_text(
        "# Troubleshooting\n", encoding="utf-8"
    )
    (project / "docs" / "ARCHITECTURE.md").write_text(
        "# Architecture\n", encoding="utf-8"
    )
    (project / "pyproject.toml").write_text(
        '[project]\nname = "glassless3d"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    if licensed:
        (project / "LICENSE").write_text(
            "Example reviewed project license.\n" * 20,
            encoding="utf-8",
        )
        (project / "THIRD_PARTY_NOTICES.md").write_text(
            "# Third-party notices\n\nReviewed component notice.\n" * 12,
            encoding="utf-8",
        )
    return project


def _acceptance(root: Path, passed: bool = True) -> Path:
    directory = root / "software_acceptance"
    directory.mkdir(parents=True)
    (directory / "software_acceptance.json").write_text(
        json.dumps({"passed": passed, "replay": {"weighted_score": 0.61}}),
        encoding="utf-8",
    )
    (directory / "software_acceptance.md").write_text(
        "# Software acceptance\n", encoding="utf-8"
    )
    return directory


def test_standalone_spec_builds_a_one_folder_runtime():
    source = Path("Glassless3D.spec").read_text(encoding="utf-8")

    assert "coll = COLLECT(" in source
    assert "exclude_binaries=True" in source
    assert 'name="Glassless3D"' in source
    assert "upx=False" in source
    assert "console=False" in source
    assert '("Glassless3DOverlay.exe", ".")' in source
    assert '("onnxruntime.dll", ".")' in source
    assert '("DirectML.dll", ".")' in source
    assert "prepare_slim_mediapipe_runtime" in source
    assert "collect_all" not in source
    assert '"PIL"' in source
    assert '"matplotlib"' in source
    assert '"sounddevice"' in source


def test_packager_creates_deterministic_unlicensed_preview(
    tmp_path,
    monkeypatch,
):
    bundle = _fake_bundle(tmp_path)
    project = _project_root(tmp_path)
    acceptance = _acceptance(tmp_path)
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
    monkeypatch.setattr(package_windows_release, "_native_input_inventory", lambda: [])
    monkeypatch.setattr(package_windows_release, "_python_build_inventory", lambda: [])
    output = tmp_path / "release"

    first = package_windows_release.package_windows_release(
        bundle_dir=bundle,
        output_dir=output,
        project_root=project,
        version="0.1.0",
        commit="a" * 40,
        source_date_epoch=1_700_000_000,
        acceptance_dir=acceptance,
        sbom_path=sbom,
    )
    first_hash = first["archive_sha256"]
    second = package_windows_release.package_windows_release(
        bundle_dir=bundle,
        output_dir=output,
        project_root=project,
        version="0.1.0",
        commit="a" * 40,
        source_date_epoch=1_700_000_000,
        acceptance_dir=acceptance,
        sbom_path=sbom,
    )

    assert second["archive_sha256"] == first_hash
    assert second["license_present"] is False
    assert second["third_party_notices_present"] is False
    staging = Path(second["staging_directory"])
    assert (staging / "UNLICENSED_PREVIEW.txt").is_file()
    assert (staging / "SBOM.cdx.json").is_file()
    assert (staging / "SHA256SUMS.txt").is_file()
    with zipfile.ZipFile(second["archive"]) as archive:
        names = set(archive.namelist())
    prefix = "Glassless3D-0.1.0-windows-x64/"
    assert prefix + "Glassless3D.exe" in names
    assert prefix + "_internal/Glassless3DOverlay.exe" in names
    assert prefix + "release-manifest.json" in names


def test_release_readiness_fails_closed_without_legal_files(tmp_path):
    project = _project_root(tmp_path)
    acceptance = _acceptance(tmp_path)
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"zip")
    package_summary = tmp_path / "package-summary.json"
    package_summary.write_text(
        json.dumps(
            {
                "archive": str(archive),
                "license_present": False,
                "third_party_notices_present": False,
                "software_acceptance_passed": True,
            }
        ),
        encoding="utf-8",
    )

    passed, failures, _details = check_release_ready.validate_release_ready(
        project_root=project,
        tag="v0.1.0",
        acceptance_path=acceptance / "software_acceptance.json",
        package_summary_path=package_summary,
    )

    assert not passed
    assert any("LICENSE" in failure for failure in failures)
    assert any("THIRD_PARTY_NOTICES" in failure for failure in failures)


def test_release_readiness_passes_for_matching_licensed_bundle(tmp_path):
    project = _project_root(tmp_path, licensed=True)
    acceptance = _acceptance(tmp_path)
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"zip")
    package_summary = tmp_path / "package-summary.json"
    package_summary.write_text(
        json.dumps(
            {
                "archive": str(archive),
                "license_present": True,
                "third_party_notices_present": True,
                "software_acceptance_passed": True,
            }
        ),
        encoding="utf-8",
    )

    passed, failures, details = check_release_ready.validate_release_ready(
        project_root=project,
        tag="v0.1.0",
        acceptance_path=acceptance / "software_acceptance.json",
        package_summary_path=package_summary,
    )

    assert passed, failures
    assert details["version"] == "0.1.0"


def test_prerelease_tag_normalization():
    assert set(check_release_ready.expected_tags("0.1.0rc1")) == {
        "v0.1.0rc1",
        "v0.1.0-rc1",
    }
    assert check_release_ready.expected_tags("0.1.0") == ("v0.1.0",)
