"""Verify deployed native artifacts against their build sidecar."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

MANIFEST = "Glassless3DOverlay.build.json"
FILES = ("Glassless3DOverlay.exe", "onnxruntime.dll", "DirectML.dll")


def verify_native_build(directory: Path, expected_commit: str | None = None) -> dict:
    path = directory / MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("native build manifest is malformed")
    if expected_commit is not None and data.get("source_commit") != expected_commit:
        raise ValueError("native executable was built from a different source commit")
    for name in FILES:
        digest = hashlib.sha256()
        with (directory / name).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != data["files"].get(name):
            raise ValueError(f"native deployed file differs from build manifest: {name}")
    return data
