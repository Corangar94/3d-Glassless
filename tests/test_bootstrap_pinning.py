import hashlib
import shutil
import zipfile
from pathlib import Path

from scripts import bootstrap


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_primary_downloads_are_immutable_and_sha256_pinned():
    hashes = (
        bootstrap.FACE_MODEL_SHA256,
        bootstrap.ORT_NUPKG_SHA256,
        bootstrap.DML_NUPKG_SHA256,
        bootstrap.DEPTH_MODEL_SHA256,
        bootstrap.MINGW64_ARCHIVE_SHA256,
        bootstrap.ORT_TREE_SHA256,
        bootstrap.DML_TREE_SHA256,
        bootstrap.MINGW64_TREE_SHA256,
    )
    assert all(len(value) == 64 for value in hashes)
    assert all(int(value, 16) >= 0 for value in hashes)
    assert "/resolve/main/" not in bootstrap.DEPTH_MODEL_URL
    assert bootstrap.DEPTH_MODEL_REVISION in bootstrap.DEPTH_MODEL_URL
    assert "api.nuget.org/v3-flatcontainer" in bootstrap.ORT_NUPKG_URL
    assert "api.nuget.org/v3-flatcontainer" in bootstrap.DML_NUPKG_URL


def test_face_and_depth_steps_pass_repository_hashes(monkeypatch, tmp_path):
    calls = []

    def record(url, destination, label, sha256):
        calls.append((url, destination, label, sha256))

    monkeypatch.setattr(bootstrap, "_ensure_verified_asset", record)
    monkeypatch.setattr(
        bootstrap,
        "FACE_MODEL_DEST",
        str(tmp_path / "face_landmarker.task"),
    )
    monkeypatch.setattr(
        bootstrap,
        "DEPTH_MODEL_DEST",
        str(tmp_path / "depth.onnx"),
    )

    assert bootstrap.step_face_model()
    assert bootstrap.step_depth_model()

    assert calls[0][0] == bootstrap.FACE_MODEL_URL
    assert calls[0][3] == bootstrap.FACE_MODEL_SHA256
    assert calls[1][0] == bootstrap.DEPTH_MODEL_URL
    assert calls[1][3] == bootstrap.DEPTH_MODEL_SHA256


def test_verified_asset_replaces_invalid_cache(monkeypatch, tmp_path):
    destination = tmp_path / "asset.bin"
    destination.write_bytes(b"tampered")
    expected_payload = b"canonical"
    expected_hash = hashlib.sha256(expected_payload).hexdigest()
    calls = []

    def fake_download(url, dest, label, *, sha256=None):
        calls.append((url, dest, label, sha256))
        Path(dest).write_bytes(expected_payload)

    monkeypatch.setattr(bootstrap, "_download", fake_download)

    bootstrap._ensure_verified_asset(
        "https://example.test/asset.bin",
        str(destination),
        "asset",
        expected_hash,
    )

    assert destination.read_bytes() == expected_payload
    assert calls == [
        (
            "https://example.test/asset.bin",
            str(destination),
            "asset",
            expected_hash,
        )
    ]


def test_tree_digest_detects_content_path_and_extra_file_tampering(tmp_path):
    tree = tmp_path / "tree"
    _write_tree(tree, {"bin/tool.exe": b"tool", "include/api.h": b"api"})
    canonical = bootstrap._tree_digest(tree)

    assert canonical is not None
    assert bootstrap._tree_matches(str(tree), canonical)

    (tree / "bin/tool.exe").write_bytes(b"modified")
    assert not bootstrap._tree_matches(str(tree), canonical)

    (tree / "bin/tool.exe").write_bytes(b"tool")
    (tree / "extra.txt").write_bytes(b"extra")
    assert not bootstrap._tree_matches(str(tree), canonical)

    (tree / "extra.txt").unlink()
    (tree / "include/api.h").rename(tree / "include/renamed.h")
    assert not bootstrap._tree_matches(str(tree), canonical)


def test_onnxruntime_repairs_cached_trees_from_verified_packages(
    monkeypatch,
    tmp_path,
):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    ort_dir = vendor / "onnxruntime"
    dml_dir = vendor / "directml"
    ort_package = vendor / "ort.nupkg"
    dml_package = vendor / "dml.nupkg"
    canonical_ort_package = tmp_path / "canonical-ort.nupkg"
    canonical_dml_package = tmp_path / "canonical-dml.nupkg"

    ort_members = {
        "runtimes/win-x64/native/onnxruntime.dll": b"ort-dll",
        "runtimes/win-x64/native/onnxruntime.lib": b"ort-lib",
        "build/native/include/onnxruntime_c_api.h": b"ort-header",
    }
    dml_members = {
        "bin/x64-win/DirectML.dll": b"dml-dll",
        "bin/x64-win/DirectML.lib": b"dml-lib",
        "include/DirectML.h": b"dml-header",
    }
    _write_zip(canonical_ort_package, ort_members)
    _write_zip(canonical_dml_package, dml_members)

    expected_ort = tmp_path / "expected-ort"
    expected_dml = tmp_path / "expected-dml"
    _write_tree(
        expected_ort,
        {
            "lib/onnxruntime.dll": b"ort-dll",
            "lib/onnxruntime.lib": b"ort-lib",
            "include/onnxruntime_c_api.h": b"ort-header",
        },
    )
    _write_tree(
        expected_dml,
        {
            "lib/DirectML.dll": b"dml-dll",
            "lib/DirectML.lib": b"dml-lib",
            "include/DirectML.h": b"dml-header",
        },
    )

    monkeypatch.setattr(bootstrap, "ORT_DIR", str(ort_dir))
    monkeypatch.setattr(bootstrap, "DML_DIR", str(dml_dir))
    monkeypatch.setattr(bootstrap, "ORT_NUPKG", str(ort_package))
    monkeypatch.setattr(bootstrap, "DML_NUPKG", str(dml_package))
    monkeypatch.setattr(
        bootstrap,
        "ORT_TREE_SHA256",
        bootstrap._tree_digest(expected_ort),
    )
    monkeypatch.setattr(
        bootstrap,
        "DML_TREE_SHA256",
        bootstrap._tree_digest(expected_dml),
    )

    def install_package(url, destination, _label, _sha256):
        source = (
            canonical_ort_package
            if url == bootstrap.ORT_NUPKG_URL
            else canonical_dml_package
        )
        shutil.copy2(source, destination)

    monkeypatch.setattr(bootstrap, "_ensure_verified_asset", install_package)

    assert bootstrap.step_onnxruntime()
    assert (ort_dir / "lib/onnxruntime.dll").read_bytes() == b"ort-dll"
    assert (dml_dir / "lib/DirectML.dll").read_bytes() == b"dml-dll"
    assert not ort_package.exists()
    assert not dml_package.exists()

    (dml_dir / "lib/DirectML.dll").write_bytes(b"tampered")
    assert bootstrap.step_onnxruntime()
    assert (dml_dir / "lib/DirectML.dll").read_bytes() == b"dml-dll"


def test_verified_mingw_cache_is_reused_without_network(monkeypatch, tmp_path):
    toolchain = tmp_path / "mingw"
    _write_tree(
        toolchain,
        {
            "mingw64/bin/g++.exe": b"g++",
            "mingw64/bin/cmake.exe": b"cmake",
            "mingw64/lib/runtime.a": b"runtime",
        },
    )
    monkeypatch.setattr(bootstrap, "_MINGW_DIR", str(toolchain))
    monkeypatch.setattr(
        bootstrap,
        "MINGW64_TREE_SHA256",
        bootstrap._tree_digest(toolchain),
    )
    monkeypatch.setattr(bootstrap, "_verified_mingw_paths", None)
    monkeypatch.setattr(
        bootstrap,
        "_ensure_verified_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network should not be used for a canonical tree")
        ),
    )

    paths = bootstrap._ensure_mingw_toolchain()

    assert paths == (
        str(toolchain / "mingw64/bin/g++.exe"),
        str(toolchain / "mingw64/bin/cmake.exe"),
    )


def test_all_legacy_core_call_sites_are_bound_to_hardened_implementations():
    assert bootstrap._core._sha256 is bootstrap._sha256
    assert bootstrap._core._download is bootstrap._download
    assert bootstrap._core._extract_from_nupkg is bootstrap._extract_from_nupkg
    assert bootstrap._core.step_face_model is bootstrap.step_face_model
    assert bootstrap._core.step_onnxruntime is bootstrap.step_onnxruntime
    assert bootstrap._core.step_depth_model is bootstrap.step_depth_model
    assert bootstrap._core.step_reshade_sdk is bootstrap.step_reshade_sdk
    assert bootstrap._core._find_gcc is bootstrap._find_gcc
    assert bootstrap._core._find_cmake is bootstrap._find_cmake
    assert bootstrap._core.DEPTH_MODEL_URL == bootstrap.DEPTH_MODEL_URL
