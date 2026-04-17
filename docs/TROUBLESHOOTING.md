# Glassless3D Troubleshooting

Start with the overlay-first diagnostics report:

```powershell
python -m launcher.diagnostics
```

To save a shareable support report:

```powershell
python -m launcher.diagnostics --output diagnostics.txt
```

The report checks:

- overlay executable discovery
- depth model discovery
- config file readability
- latest `overlay.log` runtime summary, when available
- primary and experimental display backend status
- project root and Python executable
- useful recovery commands

If the report says `NOT READY`, fix the listed problems before tuning depth or
tracking. The most common recovery step is:

```powershell
python scripts/bootstrap.py
```

## Tracking Feels Weak Or Jittery

Open the live monitor:

```powershell
python -m tracker.debug_monitor
```

Or use the launcher Advanced tab: `Diagnostics` -> `Open tracking quality
monitor`.

Treat the tracking-quality panel as the first gate:

- `GOOD`: proceed to depth tuning
- `WARN`: usable for debugging, but not for judging final 3D quality
- `DANGER`: fix lighting, camera placement, camera selection, or occlusion first

## Depth Looks Flat

Check the diagnostics report for a missing depth model. The overlay can still
run without the model, but it will use flat fallback depth.

Then press `Ctrl+D` in the overlay to show the depth debug view. If the depth
view is almost uniform, the depth model is not producing useful scene structure
for the current content.

## Overlay Does Not Start

Run:

```powershell
python -m launcher.diagnostics
```

If `Glassless3DOverlay.exe` is missing, rebuild with:

```powershell
python scripts/bootstrap.py
```

If the launcher status shows `OVERLAY ERROR`, hover the status label for the
actionable startup message.

## Overlay Log Warnings

When `overlay.log` is present, `python -m launcher.diagnostics` parses the
latest once-per-second summary and warns about:

- stale tracker shared memory
- no active depth inference
- no captured desktop frame

These warnings are runtime health signals. They do not replace the live tracking
quality monitor, but they make support reports useful even after the overlay has
been closed.

## ReShade And Protected Games

ReShade and game-injected integrations are experimental. Do not use them as the
default troubleshooting path for the standalone overlay. World of Warcraft and
protected multiplayer titles remain policy-gated feasibility work, not the
primary runtime target.
