# Native Capture Resilience Design

**Status:** Approved for planning
**Date:** 2026-07-10

## Goal

Make the standalone D3D11/DXGI overlay recover predictably from display changes, desktop-duplication loss, and device removal without dereferencing invalid resources, copying invalid regions, or falling back to an in-process backend.

## Scope

This phase changes only the standalone desktop-capture path. It does not add injection, anti-cheat detection, Windows Graphics Capture, multi-output composition, or any protection workaround.

## Capture binding

`CaptureBinding` will describe the selected adapter/output: adapter LUID, output `HMONITOR`/device name, desktop rectangle, output rotation, and the logical capture rectangle. The overlay selects the output containing the target window (or the primary output for full-desktop capture), not output index zero.

A target window that crosses output boundaries is capture-unavailable. The overlay reports the condition and retries only after the window/output configuration changes. It never synthesizes a one-pixel box or performs cross-output copies.

The process becomes Per-Monitor-V2 DPI-aware before creating overlay windows. `WM_DISPLAYCHANGE`, `WM_DPICHANGED`, and target-monitor changes mark the binding dirty and cause a rebind.

## Recovery state machine

The capture lifecycle has four states:

```text
Running -> Rebinding -> Running
Running -> DeviceRecovery -> Rebinding -> Running
Running/Rebinding -> Unavailable -> Rebinding
```

- `DXGI_ERROR_WAIT_TIMEOUT` keeps the last valid frame without calling `ReleaseFrame`.
- `DXGI_ERROR_ACCESS_LOST` and duplication `INVALID_CALL` release duplication/capture resources, then re-enumerate and recreate the binding with bounded backoff.
- Device-removed/reset/hung errors from D3D calls or `Present` query `GetDeviceRemovedReason`, destroy all device-owned resources (including depth worker resources), recreate device/swap chain/renderer, then rebind capture.
- Protected, display-only, unsupported, session-disconnected, or unavailable capture enters `Unavailable`; the overlay disables the effect and emits a clear diagnostic. It never escalates to injection or retries every frame.

## Frame and resource safety

Frame acquisition uses a scoped lease so every successful `AcquireNextFrame` has exactly one `ReleaseFrame`, including copy/depth failures. Crop geometry uses an actual rectangle intersection and validates non-empty, in-bounds source and destination regions before `CopySubresourceRegion`.

The duplicator always captures one full selected output. Rotation is normalized into an upright logical scene texture before crop/depth/render processing. Capture-size changes rebuild capture SRV/texture and reinitialize size-dependent depth staging resources. Depth cleanup releases both current and previous depth textures/SRVs before replacement.

## Verification

- Unit-test pure output-selection, intersection, crop-bounds, rotation, and state-transition helpers.
- Add static source-contract tests for ReleaseFrame pairing, Present/device-loss handling, failed-rebind guards, and depth resource reset.
- Build the native overlay and verify the staged executable timestamp/hash.
- Test target movement between outputs, display/DPI events, duplication loss, and protected/unavailable capture with diagnostics/log assertions.

## References

- [Desktop Duplication API](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api)
- [AcquireNextFrame](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgioutputduplication-acquirenextframe)
- [ReleaseFrame](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgioutputduplication-releaseframe)
- [Device-loss recovery](https://learn.microsoft.com/en-us/windows/uwp/gaming/handling-device-lost-scenarios)
- [CopySubresourceRegion bounds](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-copysubresourceregion)
