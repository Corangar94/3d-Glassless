from pathlib import Path

path = Path("tracker/main.py")
text = path.read_text(encoding="utf-8")
old = '''                    else:
                        output = self._predict_filter()
                        status = "tracking" if output.confidence >= 0.20 else "hold"
'''
new = '''                    else:
                        # Preserve the public state contract: a predicted/replayed
                        # pose during an async result gap is still a hold sample.
                        output = self._predict_filter()
                        status = "hold"
'''
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("tracking hold block changed unexpectedly")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
