# Releasing Glassless3D

Glassless3D uses two separate Windows workflows:

- **Standalone Windows package** builds an unlicensed CI evaluation candidate on pull requests and relevant `master` changes.
- **Publish Windows release** rebuilds the exact release commit, enforces legal and software-quality gates, and publishes only from an existing matching tag.

The release workflow fails closed until the project owner has selected and reviewed a root `LICENSE` and `THIRD_PARTY_NOTICES.md`.

## Standalone package layout

The release uses a PyInstaller one-folder distribution:

```text
Glassless3D-<version>-windows-x64/
├── Glassless3D.exe
├── _internal/
│   ├── Glassless3DOverlay.exe
│   ├── onnxruntime.dll
│   ├── DirectML.dll
│   ├── models/
│   └── Python, Qt, OpenCV, and MediaPipe runtime files
├── documentation/
│   ├── README.md
│   ├── TROUBLESHOOTING.md
│   ├── ARCHITECTURE.md
│   └── software_acceptance/
├── SBOM.cdx.json
├── release-manifest.json
└── SHA256SUMS.txt
```

A one-folder build avoids extracting the large native runtime and depth model into a temporary directory on every launch. It also makes the exact shipped files inspectable and hashable.

## Local candidate build

From a clean Windows checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install pip-audit==2.10.1

python scripts/bootstrap.py
python -m scripts.software_acceptance `
  --output-dir software_acceptance `
  --generate-demo `
  --fail-on-regression

New-Item -ItemType Directory -Force release | Out-Null
python -m pip_audit `
  --progress-spinner off `
  --desc off `
  --skip-editable `
  --format cyclonedx-json `
  --output release/python-environment.sbom.cdx.json

python -m PyInstaller --clean --noconfirm Glassless3D.spec
$env:SOURCE_DATE_EPOCH = git show -s --format=%ct HEAD
python -m scripts.package_windows_release `
  --bundle-dir dist/Glassless3D `
  --output-dir release `
  --acceptance-dir software_acceptance `
  --sbom release/python-environment.sbom.cdx.json `
  --summary-json release/package-summary.json
```

Until a project license is selected, the candidate contains `UNLICENSED_PREVIEW.txt` and is not publishable.

## Publication gates

`python -m scripts.check_release_ready` requires all of the following:

1. The tag matches `[project].version` in `pyproject.toml`.
2. `LICENSE` is present, reviewed, non-placeholder text.
3. `THIRD_PARTY_NOTICES.md` is present, reviewed, non-placeholder text.
4. Deterministic software acceptance passed.
5. The packaged archive exists and records the same legal/acceptance state.
6. The publication tag points to the exact commit that produced the assets.

The full Windows test suite, dependency audit, native bootstrap/build, frozen entrypoint smoke tests, deterministic packaging, and release readiness validation all run again on the release workflow.

## Dry run

Open **Actions → Publish Windows release → Run workflow** and supply:

- `tag`: the proposed existing-version tag text, such as `v0.1.0`
- `dry_run`: enabled

A dry run builds everything and uploads workflow artifacts, but does not create a GitHub Release. The legal gate still applies; use the normal **Standalone Windows package** workflow to inspect unlicensed evaluation candidates.

## Publishing

1. Merge the version, `LICENSE`, and reviewed third-party notices.
2. Confirm all required checks pass on the exact commit.
3. Create and push an annotated tag matching the project version.
4. The tag-triggered **Publish Windows release** workflow rebuilds and verifies the commit.
5. The workflow invokes `gh release create --verify-tag`, attaches the ZIP, checksums, manifest, CycloneDX SBOM, and software-acceptance reports, then marks alpha/beta/RC tags as prereleases.

The workflow never invents a tag. It publishes only an existing tag that resolves to the checked-out build commit.

## Reproducibility and provenance

The ZIP container is deterministic for a fixed source tree, PyInstaller output, dependency environment, and `SOURCE_DATE_EPOCH`. Its entries are sorted and receive the source commit timestamp. The package manifest records:

- product version and source commit;
- Python and PyInstaller versions;
- exact file sizes and SHA-256 values;
- pinned native/model input URLs and hashes;
- the resolved Python build environment;
- software-acceptance status; and
- whether the legal release files were present.

PE binaries may still contain toolchain-generated metadata that prevents byte-for-byte equality across different runner images. The package manifest and per-file hashes make such differences visible.

## Signing

The current workflow does not Authenticode-sign `Glassless3D.exe` or `Glassless3DOverlay.exe`. Add certificate-backed signing before describing a release as publisher-verified. Do not place a long-lived code-signing certificate or password directly in repository secrets; use a managed signing service or short-lived identity flow.
