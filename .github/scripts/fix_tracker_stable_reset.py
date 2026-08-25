from pathlib import Path

path = Path("launcher/mainwindow.py")
text = path.read_text(encoding="utf-8")
old = '''        if summary is None:
            self._shm_tile.setText("SHM\\nWaiting")
            self._depth_tile.setText("Depth\\nWaiting")
            self._capture_tile.setText("Capture\\nWaiting")
            return

        self._maybe_recover_overlay(summary)
'''
new = '''        if summary is None:
            self._shm_tile.setText("SHM\\nWaiting")
            self._depth_tile.setText("Depth\\nWaiting")
            self._capture_tile.setText("Capture\\nWaiting")
            return

        # TrackerProcess emits a status only when it changes. Sample sustained
        # health from the one-second runtime timer so the stable-reset interval
        # can complete during an unchanged tracking session.
        if self._tracking_status == "tracking" and self._tracker_is_running():
            self._recovery.mark_healthy("tracker")

        self._maybe_recover_overlay(summary)
'''
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("runtime-health insertion point changed unexpectedly")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
