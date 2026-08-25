from __future__ import annotations

import json

from scripts.analyze_bundle_size import analyze_bundle


def _manifest(path, files):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [
                    {"path": file_path, "size": size, "sha256": "0" * 64}
                    for file_path, size in files
                ],
            }
        ),
        encoding="utf-8",
    )


def test_size_gate_passes_slim_candidate_and_reports_reduction(tmp_path):
    manifest = tmp_path / "manifest.json"
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"z" * 1_000)
    _manifest(
        manifest,
        [
            ("Glassless3D.exe", 100_000),
            ("_internal/cv2/cv2.pyd", 10_000_000),
            ("_internal/mediapipe/tasks/c/libmediapipe.dll", 27_000_000),
            ("_internal/models/depth.onnx", 50_000_000),
        ],
    )

    report = analyze_bundle(
        manifest_path=manifest,
        archive_path=archive,
        max_unpacked_bytes=100_000_000,
        max_archive_bytes=2_000,
    )

    assert report.passed
    assert report.unpacked_bytes == 87_100_000
    assert report.archive_bytes == 1_000
    assert report.unpacked_reduction_bytes > 0
    assert report.archive_reduction_bytes > 0
    assert report.largest_groups[0].path == "_internal/models"


def test_size_gate_rejects_unrelated_media_runtime(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(
        manifest,
        [
            ("Glassless3D.exe", 100_000),
            ("_internal/matplotlib/backends/backend_qt.py", 20_000),
            ("_internal/PIL/Image.py", 30_000),
            (
                "_internal/mediapipe/tasks/python/vision/pose_landmarker.py",
                40_000,
            ),
        ],
    )

    report = analyze_bundle(manifest_path=manifest)

    assert not report.passed
    assert len(report.forbidden_paths) == 3
    assert any("forbidden" in failure for failure in report.failures)


def test_size_gate_enforces_archive_and_unpacked_limits(tmp_path):
    manifest = tmp_path / "manifest.json"
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"x" * 21)
    _manifest(manifest, [("large.bin", 101)])

    report = analyze_bundle(
        manifest_path=manifest,
        archive_path=archive,
        max_unpacked_bytes=100,
        max_archive_bytes=20,
    )

    assert not report.passed
    assert any("unpacked bundle exceeds" in failure for failure in report.failures)
    assert any("archive exceeds" in failure for failure in report.failures)


def test_size_report_writes_json_and_markdown(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(manifest, [("Glassless3D.exe", 100_000)])
    report = analyze_bundle(manifest_path=manifest)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    report.write_json(json_path)
    report.write_markdown(markdown_path)

    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["passed"] is True
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "standalone bundle-size report" in markdown
    assert "Glassless3D.exe" in markdown
