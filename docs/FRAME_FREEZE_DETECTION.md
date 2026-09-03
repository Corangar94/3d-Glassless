# Bounded camera freeze detection

Some webcam drivers continue returning successful reads while repeating one captured frame indefinitely. The latest-frame worker samples frame identity every 250 ms and treats three seconds of exact repetition as a camera failure so the existing reconnect path can take over.

## Two-stage fingerprint

Hashing every byte of a high-resolution frame at every check wastes memory bandwidth while a healthy camera is changing normally. The detector now uses two stages:

1. Large NumPy frames receive a deterministic spatial fingerprint of at most 320×180 sample locations.
2. A full-buffer fingerprint is captured only after the spatial fingerprint repeats.
3. At the freeze timeout, another full-buffer fingerprint must match the earlier exact baseline before the detector reports a freeze.

A spatial collision therefore cannot create a false freeze. If pixels outside the grid changed, the exact comparison fails and starts a new identity episode from the current frame. Once a freeze is established, each checked frame is also compared exactly, so a change outside the grid clears the frozen state.

Small frames up to 256 KiB and generic contiguous buffer objects keep the direct full-buffer path. Non-contiguous and opaque objects remain unsupported by this optional safety gate, preserving existing third-party frame compatibility.

## Regular-check cost

For an 8-bit three-channel camera frame, the bounded fingerprint hashes 172,800 bytes:

| Frame | Full buffer | Regular fingerprint | Byte reduction |
| --- | ---: | ---: | ---: |
| 1280×720 | 2,764,800 | 172,800 | 16× |
| 1920×1080 | 6,220,800 | 172,800 | 36× |
| 3840×2160 | 24,883,200 | 172,800 | 144× |

A changing camera usually alters the spatial fingerprint on each check and therefore performs no full-buffer hashes. A genuinely frozen camera normally performs one full hash on the first repeated sampled frame and one at the three-second confirmation boundary.

The grid includes both image boundaries and uniformly distributed interior rows and columns. The index arrays are cached by dimension so regular checks allocate only the bounded sampled frame and hash state.

## Timing behavior

At the normal 250 ms check cadence, the exact baseline is established on the first repeated sample, well before the three-second timeout, so freeze timing is unchanged.

A sparse direct caller that supplies only the first frame and then jumps straight to the timeout has no earlier exact baseline. In that unusual case, the timeout observation establishes the full baseline and one later checked observation confirms it. The detector never treats one full fingerprint as proof of repetition.

## Diagnostics

`FrameFreezeDetectorSnapshot.fingerprint_count` remains the number of scheduled frame checks that produced a supported fingerprint. `full_fingerprint_count` reports how many of those checks or confirmation steps hashed the complete buffer. Freeze episode and frozen-age counters retain their previous meaning.
