#!/usr/bin/env python3
"""
scripts/export_vda_onnx.py
Export Video-Depth-Anything-Small (non-streaming) to ONNX for Glassless3D.

The exported model is a drop-in replacement for depth_anything_v2_small_fp16.onnx:
  - Input:  float32 [1, 3, 518, 518]  (same as DAv2)
  - Output: float32 [1, 518, 518]      (same as DAv2)
  - Size:   ~120-150 MB
  - Temporal consistency: better than DAv2 (trained with Temporal Gradient
    Matching loss), but not as strong as streaming mode (no KV-cache).

Usage:
    python scripts/export_vda_onnx.py [--install] [--replace]

  --install   Clone the VDA repo and download weights if not already present.
  --replace   After export, make video_depth_anything_vits_518.onnx the active
              depth model by renaming the existing one to .bak and symlinking.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import urllib.request

ROOT        = pathlib.Path(__file__).parent.parent
VDA_REPO    = ROOT / "vendor" / "video-depth-anything"
WEIGHTS     = ROOT / "models" / "video_depth_anything_vits.pth"
OUTPUT      = ROOT / "models" / "video_depth_anything_vits_518.onnx"
ACTIVE      = ROOT / "models" / "depth_anything_v2_small_fp16.onnx"
ACTIVE_BAK  = ROOT / "models" / "depth_anything_v2_small_fp16.onnx.bak"

VDA_GITHUB    = "https://github.com/DepthAnything/Video-Depth-Anything.git"
WEIGHTS_URL   = (
    "https://huggingface.co/depth-anything/Video-Depth-Anything-Small"
    "/resolve/main/video_depth_anything_vits.pth"
)

INPUT_H = INPUT_W = 518


# ── helpers ───────────────────────────────────────────────────────────────────

def _download(url: str, dest: pathlib.Path, label: str) -> None:
    if dest.exists():
        print(f"  already present: {dest.relative_to(ROOT)}  ({dest.stat().st_size // 1024} KB)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {label}...", end="", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    print(f" {dest.stat().st_size // 1024} KB")


def _clone_vda() -> None:
    if VDA_REPO.exists():
        print(f"  already cloned: {VDA_REPO.relative_to(ROOT)}")
        return
    print(f"  cloning Video-Depth-Anything...", end="", flush=True)
    r = subprocess.run(
        ["git", "clone", "--depth=1", VDA_GITHUB, str(VDA_REPO)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f" FAILED\n{r.stderr}")
        sys.exit(1)
    print(" OK")


def _require_torch() -> None:
    try:
        __import__("torch")
    except ImportError:
        print("ERROR: PyTorch is required to export VDA.")
        print("Install: pip install torch --index-url https://download.pytorch.org/whl/cpu")
        sys.exit(1)


# ── model wrapper for normalised I/O ─────────────────────────────────────────

def _build_wrapper(model):
    """
    Wrap VideoDepthAnything so ONNX export sees:
      input:  [1, 3, H, W]  float32
      output: [1, H, W]     float32

    The underlying model may expect [B, T, C, H, W] with T=sequence_length.
    We detect this at trace time and insert an unsqueeze if needed.
    """
    import torch
    import torch.nn as nn

    class VDAWrapper(nn.Module):
        def __init__(self, m, needs_t: bool):
            super().__init__()
            self.m = m
            self.needs_t = needs_t

        def forward(self, x):
            # x: [1, 3, H, W]
            if self.needs_t:
                x = x.unsqueeze(1)          # -> [1, T=1, 3, H, W]
            out = self.m(x)
            if isinstance(out, dict):
                out = out.get("depth", list(out.values())[0])
            # Collapse any leading dims down to [1, H, W].
            while out.dim() > 3:
                out = out.squeeze(1)
            if out.dim() == 2:
                out = out.unsqueeze(0)
            return out

    dummy_4d = torch.zeros(1, 3, INPUT_H, INPUT_W, dtype=torch.float32)
    dummy_5d = torch.zeros(1, 1, 3, INPUT_H, INPUT_W, dtype=torch.float32)

    model.eval()
    needs_t = False
    with torch.no_grad():
        for dummy, desc, use_t in [
            (dummy_4d, "BCHW",  False),
            (dummy_5d, "BTCHW", True),
        ]:
            try:
                out = model(dummy)
                if isinstance(out, dict):
                    out = out.get("depth", list(out.values())[0])
                print(f"  Model accepts {desc}, raw output shape: {tuple(out.shape)}")
                needs_t = use_t
                break
            except Exception as e:
                print(f"  {desc} failed: {e}")
        else:
            print("ERROR: Could not run model with either BCHW or BTCHW input. "
                  "Check VDA repo version.")
            sys.exit(1)

    wrapper = VDAWrapper(model, needs_t)
    wrapper.eval()

    # Sanity check the wrapper output shape
    with torch.no_grad():
        test_out = wrapper(dummy_4d)
    assert test_out.shape == (1, INPUT_H, INPUT_W), \
        f"Wrapper output shape {test_out.shape} != expected (1, {INPUT_H}, {INPUT_W})"
    print(f"  Wrapper verified. Output shape: {tuple(test_out.shape)}")
    return wrapper


# ── export ────────────────────────────────────────────────────────────────────

def export() -> None:
    import torch

    sys.path.insert(0, str(VDA_REPO))
    try:
        from video_depth_anything.video_depth_anything import VideoDepthAnything
    except ImportError as e:
        print(f"ERROR: cannot import VideoDepthAnything from {VDA_REPO}: {e}")
        print("Make sure the repo is at vendor/video-depth-anything/ and "
              "contains video_depth_anything/video_depth_anything.py")
        sys.exit(1)

    print(f"\n  loading weights from {WEIGHTS.relative_to(ROOT)} ...")
    model = VideoDepthAnything(encoder="vits", features=64,
                               out_channels=[48, 96, 192, 384])
    state = torch.load(str(WEIGHTS), map_location="cpu", weights_only=True)
    # Some checkpoints wrap weights under a top-level key.
    if isinstance(state, dict) and "model" in state and "encoder" not in state:
        state = state["model"]
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"  Warning: {len(missing)} missing keys (e.g. {missing[:3]})")
    if unexpected:
        print(f"  Warning: {len(unexpected)} unexpected keys")

    print("  probing model I/O shape ...")
    wrapper = _build_wrapper(model)

    print(f"  exporting to {OUTPUT.relative_to(ROOT)} ...")
    dummy = torch.zeros(1, 3, INPUT_H, INPUT_W, dtype=torch.float32)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            (dummy,),
            str(OUTPUT),
            opset_version=17,
            input_names=["input"],
            output_names=["depth"],
            dynamic_axes=None,          # fixed shape — DirectML-friendly
            do_constant_folding=True,
        )
    print(f"  exported  {OUTPUT.stat().st_size // (1024*1024)} MB")


def verify() -> None:
    try:
        import onnxruntime as ort
        import numpy as np
    except ImportError:
        print("  (onnxruntime not installed, skipping verification)")
        return

    print("  verifying ONNX model ...")
    sess = ort.InferenceSession(str(OUTPUT), providers=["CPUExecutionProvider"])
    inp  = sess.get_inputs()[0]
    outp = sess.get_outputs()[0]
    print(f"    input:  {inp.name} {inp.shape} {inp.type}")
    print(f"    output: {outp.name} {outp.shape} {outp.type}")

    dummy = np.zeros((1, 3, INPUT_H, INPUT_W), np.float32)
    result = sess.run(None, {inp.name: dummy})
    assert result[0].shape == (1, INPUT_H, INPUT_W), \
        f"Unexpected output shape: {result[0].shape}"
    print(f"    output shape: {result[0].shape}  OK")


def replace_active() -> None:
    if ACTIVE.exists() and not ACTIVE.is_symlink():
        print(f"  backing up {ACTIVE.relative_to(ROOT)} -> .bak")
        ACTIVE.rename(ACTIVE_BAK)
    elif ACTIVE.is_symlink():
        ACTIVE.unlink()

    # Copy rather than symlink for Windows compatibility
    print(f"  copying VDA model as active depth model ...")
    shutil.copy2(str(OUTPUT), str(ACTIVE))
    print(f"  OK  {ACTIVE.relative_to(ROOT)} now points to VDA-Small")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true",
                    help="Clone VDA repo and download weights if missing")
    ap.add_argument("--replace", action="store_true",
                    help="Replace active depth model with exported VDA model")
    args = ap.parse_args()

    _require_torch()

    print("\n[VDA export] Checking prerequisites ...")
    if args.install:
        _clone_vda()
        _download(WEIGHTS_URL, WEIGHTS, "VDA-Small weights (~116 MB)")
    else:
        if not VDA_REPO.exists():
            print(f"ERROR: VDA repo not found at {VDA_REPO.relative_to(ROOT)}")
            print("Run with --install to clone it automatically, or manually:")
            print(f"  git clone --depth=1 {VDA_GITHUB} {VDA_REPO.relative_to(ROOT)}")
            sys.exit(1)
        if not WEIGHTS.exists():
            print(f"ERROR: weights not found at {WEIGHTS.relative_to(ROOT)}")
            print("Run with --install to download automatically, or manually:")
            print(f"  Download from: {WEIGHTS_URL}")
            sys.exit(1)

    if OUTPUT.exists():
        print(f"\n  already exported: {OUTPUT.relative_to(ROOT)}  "
              f"({OUTPUT.stat().st_size // (1024*1024)} MB)")
    else:
        print("\n[VDA export] Exporting ...")
        export()

    print("\n[VDA export] Verifying ...")
    verify()

    if args.replace:
        print("\n[VDA export] Activating VDA as depth model ...")
        replace_active()

    print(f"""
Done! Video-Depth-Anything-Small exported to:
  {OUTPUT.relative_to(ROOT)}

To use it, either:
  python scripts/export_vda_onnx.py --replace
or copy it manually:
  copy models\\video_depth_anything_vits_518.onnx models\\depth_anything_v2_small_fp16.onnx

The overlay will automatically pick it up on next launch.
""")


if __name__ == "__main__":
    main()
