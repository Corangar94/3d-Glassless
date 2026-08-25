from pathlib import Path

path = Path("launcher/mainwindow.py")
text = path.read_text(encoding="utf-8")
old = '''        tracker.status_changed.connect(self._on_status)
        tracker.status_changed.connect(self._on_tracker_status_for_overlay)
        tracker.stopped.connect(self._on_tracker_stopped)
'''
new = '''        tracker.status_changed.connect(self._on_status)
        tracker.status_changed.connect(self._on_tracker_status_for_overlay)
        # TrackerProcess.stopped has no payload. Bind the process owner here so
        # a queued stop from a retired tracker cannot clear a newer replacement.
        tracker.stopped.connect(
            lambda owner=tracker: self._on_tracker_stopped(owner)
        )
'''
if new not in text:
    if text.count(old) != 1:
        raise RuntimeError("tracker signal connection block changed unexpectedly")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
