import subprocess
import sys

import numpy as np
from PIL import Image

from scripts import import_depth_capture


def test_import_depth_capture_delegates_to_depth_capture_import_main(monkeypatch):
    calls = []
    monkeypatch.setattr(import_depth_capture.depth_capture_import, "main", lambda argv: calls.append(argv) or 10)

    code = import_depth_capture.main(["screens", "frames"])

    assert code == 10
    assert calls == [["screens", "frames"]]


def test_import_depth_capture_script_runs_from_repo_root(tmp_path):
    source = tmp_path / "screens"
    output = tmp_path / "frames"
    source.mkdir()
    Image.fromarray(np.zeros((1, 1), dtype=np.uint8)).save(source / "depth.png")

    result = subprocess.run(
        [sys.executable, "scripts/import_depth_capture.py", str(source), str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (output / "frame_0000.npy").exists()
