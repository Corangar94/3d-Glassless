# Transactional webcam control locking

After a stable camera-quality warm-up, Glassless3D may disable autofocus and automatic exposure to reduce focus pumping and brightness hunting. Camera backends disagree on property support, accepted values, and whether failures return `False`, invalid numbers, or exceptions, so this optimization must fail without degrading the image.

## Safe transaction

Each automatic control is handled independently:

1. Read the current manual value first (`FOCUS` or `EXPOSURE`).
2. Read the current automatic-mode value when available.
3. If the manual value is unavailable or non-finite, leave automatic mode unchanged.
4. Disable the automatic mode using the backend-specific conventions.
5. Restore the exact manual value captured before the mode change.
6. If restoration fails, immediately try to re-enable the automatic mode.

Autofocus manual mode uses `0`. Automatic exposure tries `0.25` first for DirectShow-style backends and then `0` for backends using a boolean convention. Exposure rollback tries the previously reported automatic value followed by the common `0.75` and `1.0` automatic-mode values.

A control group is reported complete only when both the automatic-mode lock and manual-value preservation succeed. Older direct callers that provide only the historical `*_locked` result keys retain their previous retry semantics.

## Failure behavior

- Missing manual property IDs never cause the corresponding automatic mode to be disabled.
- Read failures and non-finite values perform no hardware writes for that group.
- Rejected or throwing property writes are contained and reported in the result dictionary.
- A failed restoration triggers a best-effort rollback and remains an incomplete lock attempt even when rollback succeeds.
- The existing bounded retry controller may try again after the configured interval, then continues tracking if the hardware cannot be locked safely.

This policy favors a functioning automatic camera over an uncertain manual state. Camera-control locking remains optional and never determines whether face tracking itself can run.
