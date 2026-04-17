# Overlay-First Pivot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pivot Glassless3D so the standalone overlay is the primary supported workflow, while ReShade and WoW-specific flows become explicitly experimental.

**Architecture:** Keep the existing tracker and overlay pipeline intact, but rewrite onboarding, launcher behavior, and docs so the supported path is `launcher -> tracker -> overlay`. Preserve the ReShade backend and tests as opt-in tooling, but remove it from first-run UX and top-level product messaging.

**Tech Stack:** Python 3.11, PySide6, pytest, YAML, C++ overlay runtime, Markdown docs

---

## File Structure

### Files to modify

- `launcher/wizard.py`
  First-run flow; remove game-install/ReShade-first onboarding and replace it with overlay-first setup pages.
- `launcher/mainwindow.py`
  Update user-facing status/error behavior so starting tracking clearly means starting the overlay path.
- `launcher/overlay_process.py`
  Improve overlay-start error reporting so missing binaries/models are handled as first-class product issues.
- `tests/test_wizard.py`
  Replace ReShade/game-directory onboarding assertions with overlay-first wizard assertions.
- `tests/test_overlay_process.py`
  Extend coverage for overlay-first readiness and user-facing failures.
- `docs/ARCHITECTURE.md`
  Rewrite architecture ownership and labels so overlay is primary, ReShade experimental.

### Files to create

- `docs/ROADMAP.md`
  Research-aligned milestone roadmap with overlay-first sequencing and WoW as a later feasibility gate.

### Files to leave in place but relabel through docs

- `launcher/reshade_install.py`
- `tests/test_reshade_install.py`
- `setup.py`
- `addon/`
- `profiles/`

These remain supported only as experimental integrations in this plan.

---

### Task 1: Add the Overlay-First Roadmap

**Files:**
- Create: `docs/ROADMAP.md`
- Test: no automated test; verify content manually

- [ ] **Step 1: Write the roadmap document**

Create `docs/ROADMAP.md` with these sections and milestone ordering:

```md
# Glassless3D Roadmap

## Product Direction

Glassless3D is a Windows desktop glasses-free overlay runtime.
The standalone overlay is the primary supported backend.
ReShade and game-injected paths are experimental.
World of Warcraft is a later policy-aware feasibility gate, not a current target.

## Current State

- Tracker writes `G3D` and `FT_SharedMem`
- Launcher starts tracker and overlay
- Overlay captures the desktop and runs monocular depth inference
- ReShade tooling still exists in the repo, but it should no longer define the main product story

## Milestone 1: Calibration And Tracking Reliability

- stabilize camera selection
- validate screen sizing and head-distance defaults
- keep tracker + overlay startup reliable

## Milestone 2: Overlay Quality And Temporal Stability

- improve temporal depth stability
- improve edge handling and disocclusion behavior
- improve debug surfaces and operator feedback

## Milestone 3: Overlay UX And Diagnostics

- first-run overlay readiness checks
- actionable startup errors
- settings and troubleshooting docs

## Milestone 4: Performance Hardening

- inference cadence
- frame pacing
- GPU/CPU pipeline profiling

## Milestone 5: Experimental Backends

- ReShade path retained as opt-in
- other display/game integrations only after overlay path is stable

## Milestone 6: WoW Feasibility Gate

- policy review
- technical viability review
- explicit go/no-go decision
```

- [ ] **Step 2: Verify the roadmap says overlay-first and WoW-last**

Review the final file and confirm these phrases or equivalent meaning are present:

- `standalone overlay is the primary supported backend`
- `ReShade and game-injected paths are experimental`
- `World of Warcraft is a later policy-aware feasibility gate`

Expected result: the roadmap no longer presents WoW or ReShade as the main entry path.

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: add overlay-first roadmap"
```

---

### Task 2: Rewrite the First-Run Wizard Around the Overlay

**Files:**
- Modify: `launcher/wizard.py`
- Test: `tests/test_wizard.py`

- [ ] **Step 1: Write the failing wizard tests for the new page flow**

Replace the old ReShade/game-directory expectations with overlay-first tests like:

```python
from launcher.wizard import (
    WelcomePage,
    CameraScreenPage,
    DonePage,
    OverlayReadyPage,
    SetupWizard,
)


def test_setup_wizard_has_four_pages(qapp, tmp_path):
    wizard = SetupWizard(config_path=str(tmp_path / "config.yaml"))
    assert len(wizard.pageIds()) == 4


def test_done_page_mentions_overlay_workflow(qapp, tmp_path):
    page = DonePage(config_path=str(tmp_path / "config.yaml"))
    assert "overlay" in page.subTitle().lower()
    assert "reshade" not in page.subTitle().lower()


def test_overlay_ready_page_reports_missing_overlay(qapp, tmp_path, monkeypatch):
    from launcher import wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "find_overlay_exe", lambda: None)
    monkeypatch.setattr(wizard_mod, "find_depth_model", lambda: None)

    page = OverlayReadyPage()
    page.initializePage()

    assert "overlay executable" in page._status_label.text().lower()
```

- [ ] **Step 2: Run the wizard tests to confirm they fail first**

Run:

```bash
pytest tests/test_wizard.py -q
```

Expected: FAIL because `GameDirPage` / `InstallPage` are still part of the wizard and `OverlayReadyPage` does not exist yet.

- [ ] **Step 3: Replace the wizard flow with overlay-first pages**

Refactor `launcher/wizard.py` so the page sequence is:

1. `WelcomePage`
2. `CameraScreenPage`
3. `OverlayReadyPage`
4. `DonePage`

Use a page shaped like this:

```python
class OverlayReadyPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Overlay readiness")
        self.setSubTitle(
            "Glassless3D uses the standalone desktop overlay as the primary runtime."
        )
        self._status_label = QLabel("")
        layout = QVBoxLayout(self)
        layout.addWidget(self._status_label)

    def initializePage(self) -> None:
        problems: list[str] = []
        if find_overlay_exe() is None:
            problems.append("Overlay executable missing")
        if find_depth_model() is None:
            problems.append("Depth model missing")

        if problems:
            self._status_label.setText(
                "Overlay not fully ready:\n- " + "\n- ".join(problems)
            )
        else:
            self._status_label.setText(
                "Overlay executable and depth model were found."
            )
```

Also update `DonePage` subtitle so it says the user should launch the Glassless3D overlay workflow from the app instead of opening ReShade in a game.

- [ ] **Step 4: Keep config writing overlay-centric**

Ensure `_write_config()` still writes camera, screen, tracking, and `gui` data, and expand it to include explicit overlay defaults if absent:

```python
config = {
    "camera": {"index": self._camera_index},
    "screen": {
        "width_cm": self._screen_width_cm,
        "height_cm": self._screen_height_cm,
    },
    "tracking": _DEFAULT_TRACKING,
    "overlay": {
        "strength_x": 1.0,
        "strength_y": 1.0,
        "virtual_depth_cm": 30.0,
    },
    "gui": {"compact_mode": False},
}
```

- [ ] **Step 5: Run wizard tests to verify they pass**

Run:

```bash
pytest tests/test_wizard.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add launcher/wizard.py tests/test_wizard.py
git commit -m "feat: pivot first-run wizard to overlay-first onboarding"
```

---

### Task 3: Make Overlay Startup the Supported Runtime Path

**Files:**
- Modify: `launcher/mainwindow.py`
- Modify: `launcher/overlay_process.py`
- Test: `tests/test_overlay_process.py`

- [ ] **Step 1: Write the failing tests for clearer overlay startup reporting**

Add tests like:

```python
def test_start_raises_actionable_message_when_overlay_missing(monkeypatch):
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: None)
    with pytest.raises(OverlayStartError) as exc_info:
        OverlayProcess().start()
    assert "overlay" in str(exc_info.value).lower()
    assert "bootstrap.py" in str(exc_info.value)


def test_start_warns_when_model_missing_but_continues(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)

    fake_popen = MagicMock()
    fake_popen.return_value.poll.return_value = None
    with patch.object(overlay_process.subprocess, "Popen", fake_popen):
        OverlayProcess().start()

    assert "flat fallback depth" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run overlay-process tests to confirm the new assertions fail first**

Run:

```bash
pytest tests/test_overlay_process.py -q
```

Expected: FAIL if the new assertions do not match the current launcher-facing behavior yet.

- [ ] **Step 3: Improve overlay startup messaging in `launcher/overlay_process.py`**

Keep the behavior but tighten the messages so the overlay is clearly the main runtime:

```python
if exe is None:
    raise OverlayStartError(
        "Glassless3DOverlay.exe not found. The desktop overlay is the primary "
        "runtime path. Run `python scripts/bootstrap.py` to build it."
    )

if model is None:
    print(
        "[overlay] WARNING: depth model not found at "
        f"{DEPTH_MODEL_REL}. The overlay will still start, but only with "
        "flat fallback depth until the model is restored.",
        file=sys.stderr,
    )
```

- [ ] **Step 4: Surface overlay startup failure in the main window**

Update `_on_tracker_status_for_overlay()` in `launcher/mainwindow.py` so the status becomes visible to the user rather than only logged:

```python
try:
    self._overlay.start()
except OverlayStartError as e:
    self._on_status("error")
    self._status_label.setText("✕ OVERLAY ERROR")
    self._status_label.setToolTip(str(e))
    _log.warning("overlay launch failed: %s", e)
```

Optionally add a small label on the tracker tab if the existing UI already has a clean place for it, but do not expand the scope into a larger UI redesign.

- [ ] **Step 5: Run overlay-process tests**

Run:

```bash
pytest tests/test_overlay_process.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add launcher/overlay_process.py launcher/mainwindow.py tests/test_overlay_process.py
git commit -m "feat: reinforce overlay startup as the primary runtime path"
```

---

### Task 4: Rewrite the Architecture Document Around Core vs Experimental Paths

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Test: no automated test; verify content manually

- [ ] **Step 1: Rewrite the system overview and shared-memory descriptions**

Update the top of `docs/ARCHITECTURE.md` so it starts with language like:

```md
## 1. System Overview

Glassless3D's primary supported runtime is the standalone desktop overlay:

`launcher -> tracker -> G3D / G3D_Settings -> Glassless3DOverlay.exe`

The ReShade addon and `FT_SharedMem` path remain in the repository as experimental integrations.
They are not the default onboarding or support path.
```

Also revise the shared-memory table entry for `FT_SharedMem`:

```md
| `FT_SharedMem` | 92 bytes | Tracker → ReShade addon / FreeTrack readers | Compatibility channel for experimental integrations |
```

- [ ] **Step 2: Relabel the ReShade section as experimental**

Rename the section heading and opening paragraph to make the status explicit:

```md
## 7. Experimental ReShade Backend

The ReShade addon is retained for opt-in experimentation with process-injected
game rendering paths. It is not the primary supported runtime.
```

Add one short policy note under that section:

```md
Protected or multiplayer titles may disable depth access or treat injected
third-party tooling as unsupported. World of Warcraft should be treated as a
later feasibility gate, not a default target.
```

- [ ] **Step 3: Review the doc for mixed messaging**

Search the file manually and remove or rewrite any language that implies:

- WoW is the main target
- ReShade is the main onboarding path
- overlay and ReShade are equal support tiers

Expected result: the document has one consistent architecture story.

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: mark overlay as primary architecture path"
```

---

### Task 5: Preserve ReShade as Experimental, Not Default

**Files:**
- Modify: `tests/test_reshade_install.py` (only if comments/assertion framing needs relabeling)
- Optionally modify: `launcher/reshade_install.py` docstring only
- Optionally modify: `setup.py` help text only

- [ ] **Step 1: Update ReShade-facing comments and help text to say experimental**

Keep runtime behavior intact, but revise text-only surfaces such as:

```python
"""Install the experimental ReShade backend into a game directory."""
```

and:

```python
parser = argparse.ArgumentParser(
    description="Glassless3D experimental ReShade backend installer"
)
```

Do not change installer semantics in this task.

- [ ] **Step 2: Keep installer tests green**

Run:

```bash
pytest tests/test_reshade_install.py -q
```

Expected: PASS with no behavior regressions.

- [ ] **Step 3: Commit**

```bash
git add launcher/reshade_install.py setup.py tests/test_reshade_install.py
git commit -m "docs: relabel reshade tooling as experimental"
```

---

### Task 6: Final Verification

**Files:**
- Verify: `docs/ROADMAP.md`
- Verify: `docs/ARCHITECTURE.md`
- Verify: `launcher/wizard.py`
- Verify: `launcher/mainwindow.py`
- Verify: `launcher/overlay_process.py`
- Verify: `tests/test_wizard.py`
- Verify: `tests/test_overlay_process.py`
- Verify: `tests/test_reshade_install.py`

- [ ] **Step 1: Run the focused verification suite**

Run:

```bash
pytest tests/test_wizard.py tests/test_overlay_process.py tests/test_reshade_install.py -q
```

Expected: PASS.

- [ ] **Step 2: Do a manual wording pass**

Open the changed docs and launcher strings and verify:

- default onboarding does not mention ReShade
- default onboarding does not mention WoW
- overlay is the named primary runtime
- ReShade is described as experimental

- [ ] **Step 3: Inspect the diff**

Run:

```bash
git diff -- docs/ROADMAP.md docs/ARCHITECTURE.md launcher/wizard.py launcher/mainwindow.py launcher/overlay_process.py tests/test_wizard.py tests/test_overlay_process.py tests/test_reshade_install.py launcher/reshade_install.py setup.py
```

Expected: only overlay-first pivot changes appear.

- [ ] **Step 4: Commit the final verification state**

```bash
git add docs/ROADMAP.md docs/ARCHITECTURE.md launcher/wizard.py launcher/mainwindow.py launcher/overlay_process.py tests/test_wizard.py tests/test_overlay_process.py tests/test_reshade_install.py launcher/reshade_install.py setup.py
git commit -m "feat: complete overlay-first product pivot"
```
