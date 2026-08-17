# ReShade integration and redistribution

Glassless3D's injected ReShade add-on is an optional, acknowledged
offline-single-player integration. It is not included in the packaged
Glassless3D application and must never be used for online play.

The official ReShade download page currently publishes ReShade 6.7.3 and
instructs third parties not to share ReShade binaries or shader files, but to
link users to the official website instead. `Glassless3D.spec` therefore does
not package ReShade DLLs, ReShade shader files, or the Glassless3D ReShade
add-on. Developers who explicitly prepare the optional integration use:

```powershell
python scripts/bootstrap.py --with-reshade
```

That workflow downloads the full-add-on installer directly from
`https://reshade.me`, verifies a pinned SHA-256 digest, extracts the matching
32-bit and 64-bit DLLs locally, downloads the matching SDK source from the
official `crosire/reshade` repository, and builds Glassless3D's own add-ons.
Generated third-party DLLs remain ignored by Git.

ReShade source is BSD-3-Clause licensed. Glassless3D's small compatibility
shader include is derived from the official CC0-1.0 `ReShade.fxh` and retains
that notice. No third-party effect shader package is redistributed.

Official references:

- https://reshade.me/
- https://github.com/crosire/reshade
- https://github.com/crosire/reshade/blob/main/LICENSE.md
- https://github.com/crosire/reshade-shaders/blob/slim/Shaders/ReShade.fxh
