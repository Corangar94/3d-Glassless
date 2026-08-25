from pathlib import Path

path = Path("overlay/overlay.cpp")
text = path.read_text(encoding="utf-8")
old = "        poseConfidence = std::clamp(poseV2.confidence, 0.0f, 1.0f);\n"
new = "        poseConfidence = std::max(0.0f, std::min(1.0f, poseV2.confidence));\n"
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("native pose confidence clamp changed unexpectedly")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
