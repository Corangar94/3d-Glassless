import hashlib
import io
import zipfile

import pytest

from scripts import bootstrap


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, url: str = "https://example.test/asset.bin"):
        super().__init__(payload)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_download_is_atomic_and_verifies_before_replace(tmp_path, monkeypatch):
    payload = b"verified payload"
    expected = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "asset.bin"
    monkeypatch.setattr(
        bootstrap.urllib.request,
        "urlopen",
        lambda _request, timeout: _Response(payload),
    )

    bootstrap._download(
        "https://example.test/asset.bin",
        str(destination),
        "asset",
        sha256=expected,
    )

    assert destination.read_bytes() == payload
    assert not list(tmp_path.glob(".*.part"))


def test_interrupted_download_leaves_no_final_or_partial_file(tmp_path, monkeypatch):
    class BrokenResponse:
        def __init__(self):
            self._calls = 0

        def geturl(self) -> str:
            return "https://example.test/asset.bin"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size=-1):
            self._calls += 1
            if self._calls == 1:
                return b"partial"
            raise OSError("connection reset")

    destination = tmp_path / "asset.bin"
    monkeypatch.setattr(
        bootstrap.urllib.request,
        "urlopen",
        lambda _request, timeout: BrokenResponse(),
    )

    with pytest.raises(OSError, match="connection reset"):
        bootstrap._download(
            "https://example.test/asset.bin",
            str(destination),
            "asset",
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".*.part"))


def test_download_rejects_non_https_url(tmp_path):
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        bootstrap._download(
            "http://example.test/asset.bin",
            str(tmp_path / "asset.bin"),
            "asset",
        )


def test_download_rejects_https_downgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bootstrap.urllib.request,
        "urlopen",
        lambda _request, timeout: _Response(
            b"payload", url="http://example.test/asset.bin"
        ),
    )
    destination = tmp_path / "asset.bin"

    with pytest.raises(RuntimeError, match="HTTPS downgrade"):
        bootstrap._download(
            "https://example.test/asset.bin",
            str(destination),
            "asset",
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".*.part"))


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.txt",
        r"..\escape.txt",
        "/absolute.txt",
        r"C:\absolute.txt",
        "nested/file.txt:stream",
    ],
)
def test_archive_destination_rejects_unsafe_paths(tmp_path, relative):
    with pytest.raises(RuntimeError, match="unsafe archive member"):
        bootstrap._safe_archive_destination(str(tmp_path / "extract"), relative)


def test_nupkg_extraction_rejects_path_traversal(tmp_path):
    package = tmp_path / "unsafe.nupkg"
    destination = tmp_path / "extract"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("payload/../../escape.txt", "owned")

    with pytest.raises(RuntimeError, match="unsafe archive member"):
        bootstrap._extract_from_nupkg(
            str(package),
            "payload/",
            str(destination),
        )

    assert not (tmp_path / "escape.txt").exists()


def test_nupkg_extraction_writes_contained_nested_member(tmp_path):
    package = tmp_path / "safe.nupkg"
    destination = tmp_path / "extract"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("payload/nested/asset.dll", b"dll")

    assert bootstrap._extract_from_nupkg(
        str(package),
        "payload/",
        str(destination),
    ) == 1
    assert (destination / "nested" / "asset.dll").read_bytes() == b"dll"


def test_core_bootstrap_call_sites_use_hardened_helpers():
    assert bootstrap._core._download is bootstrap._download
    assert bootstrap._core._extract_from_nupkg is bootstrap._extract_from_nupkg
    assert bootstrap._core.step_reshade_sdk is bootstrap.step_reshade_sdk
