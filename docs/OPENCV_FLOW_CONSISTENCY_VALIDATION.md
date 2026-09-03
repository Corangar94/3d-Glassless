# OpenCV flow consistency validation matrix

The forward-backward gate is covered at the state-machine and real OpenCV boundaries.

| Case | Expected result |
|---|---|
| Perfect translation and uniform scale with an exact reverse path | Face box and remembered eyes advance; no consistency rejections |
| Forward-valid points whose reverse path misses every origin by 2 px | Flow observation rejected |
| Six consistent points and two 3 px round-trip outliers | Observation retained from six points; two rejections counted |
| Accepted 0.75 px round-trip error | Observation retained with reduced quality |
| Malformed reverse point/status/error lengths | Fail closed without indexing mismatched arrays |
| Forward or reverse LK backend exception | Explicit stage-specific fallback error |
| Non-finite or non-positive round-trip limit | Constructor rejects policy |
| Real synthetic image translated by 4×3 px | Native OpenCV forward/reverse flow follows the patch with subpixel round-trip error |

The source-order contract additionally verifies that reverse flow is attempted only after the existing forward admission, consistency is checked before robust motion estimation and state publication, and a missing flow observation triggers the established same-frame cascade path.
