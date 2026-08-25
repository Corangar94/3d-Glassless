import os
import sys

import pytest

# Add project root to sys.path so tests can import from the tracker package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolate_diagnostics_runtime_asset_fixtures(request, monkeypatch):
    """Keep report-logic tests independent from native DLL fixture layout.

    The production invariant that missing runtime DLLs block readiness is covered
    separately in test_diagnostics_runtime_assets.py.
    """
    if request.module.__name__.endswith("test_diagnostics"):
        from launcher import diagnostics

        monkeypatch.setattr(
            diagnostics,
            "missing_overlay_runtime_assets",
            lambda _exe: [],
        )
