"""Exception-safe OpenCV ``VideoCapture`` boundary for webcam drivers.

OpenCV backends are inconsistent at the hardware boundary: unsupported or
recently unplugged devices may return failure values, return malformed values,
or raise from construction, ``isOpened``, property access, frame reads, and
cleanup.  The tracker recovery state machine expects ordinary failure values,
so this adapter converts backend exceptions into that contract while retaining
small diagnostic records for logs and support bundles.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable

import cv2


CaptureFactory = Callable[..., object]


@dataclass(frozen=True)
class CaptureBoundaryFailure:
    """One contained driver/backend failure."""

    stage: str
    error_type: str

    def render(self) -> str:
        return f"{self.stage}:{self.error_type}"


class SafeVideoCapture:
    """Proxy a native capture object without allowing driver exceptions out."""

    __g3d_safe_video_capture__ = True

    def __init__(
        self,
        *args: object,
        _factory: CaptureFactory,
        **kwargs: object,
    ) -> None:
        self._factory = _factory
        self._capture: object | None = None
        self._released = False
        self._failures: list[CaptureBoundaryFailure] = []
        try:
            self._capture = _factory(*args, **kwargs)
        except Exception as error:  # hardware/driver boundary
            self._record("construct", error)

    def _record(self, stage: str, error: BaseException) -> None:
        failure = CaptureBoundaryFailure(stage, type(error).__name__)
        if not self._failures or self._failures[-1] != failure:
            self._failures.append(failure)

    @property
    def failures(self) -> tuple[CaptureBoundaryFailure, ...]:
        return tuple(self._failures)

    @property
    def failure_summary(self) -> str:
        return ", ".join(failure.render() for failure in self._failures)

    @property
    def native_capture(self) -> object | None:
        """Expose the wrapped object for diagnostics and deterministic tests."""
        return self._capture

    def isOpened(self) -> bool:  # OpenCV spelling is part of the public API
        if self._capture is None or self._released:
            return False
        try:
            return bool(self._capture.isOpened())
        except Exception as error:  # hardware/driver boundary
            self._record("isOpened", error)
            return False

    def read(self) -> tuple[bool, object | None]:
        if self._capture is None or self._released:
            return False, None
        try:
            result = self._capture.read()
        except Exception as error:  # hardware/driver boundary
            self._record("read", error)
            return False, None
        if not isinstance(result, tuple) or len(result) != 2:
            self._record("read", TypeError("malformed capture result"))
            return False, None
        return bool(result[0]), result[1]

    def grab(self) -> bool:
        if self._capture is None or self._released:
            return False
        try:
            return bool(self._capture.grab())
        except Exception as error:  # hardware/driver boundary
            self._record("grab", error)
            return False

    def retrieve(self, *args: object, **kwargs: object) -> tuple[bool, object | None]:
        if self._capture is None or self._released:
            return False, None
        try:
            result = self._capture.retrieve(*args, **kwargs)
        except Exception as error:  # hardware/driver boundary
            self._record("retrieve", error)
            return False, None
        if not isinstance(result, tuple) or len(result) != 2:
            self._record("retrieve", TypeError("malformed capture result"))
            return False, None
        return bool(result[0]), result[1]

    def set(self, property_id: int, value: float) -> bool:
        if self._capture is None or self._released:
            return False
        try:
            return bool(self._capture.set(property_id, value))
        except Exception as error:  # hardware/driver boundary
            self._record("set", error)
            return False

    def get(self, property_id: int) -> float:
        if self._capture is None or self._released:
            return 0.0
        try:
            return float(self._capture.get(property_id))
        except Exception as error:  # hardware/driver boundary
            self._record("get", error)
            return 0.0

    def open(self, *args: object, **kwargs: object) -> bool:
        """Support the less-common empty-constructor-then-open OpenCV pattern."""
        if self._released:
            return False
        if self._capture is None:
            try:
                self._capture = self._factory()
            except Exception as error:  # hardware/driver boundary
                self._record("construct", error)
                return False
        try:
            return bool(self._capture.open(*args, **kwargs))
        except Exception as error:  # hardware/driver boundary
            self._record("open", error)
            return False

    def release(self) -> None:
        if self._capture is None or self._released:
            return
        # Mark first so a throwing backend cannot be called repeatedly by nested
        # cleanup paths or object finalization.
        self._released = True
        try:
            self._capture.release()
        except Exception as error:  # hardware/driver boundary
            self._record("release", error)

    def getBackendName(self) -> str:
        if self._capture is None or self._released:
            return ""
        try:
            return str(self._capture.getBackendName())
        except Exception as error:  # hardware/driver boundary
            self._record("getBackendName", error)
            return ""

    def __enter__(self) -> "SafeVideoCapture":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def __getattr__(self, name: str) -> Any:
        capture = self._capture
        if capture is None:
            raise AttributeError(name)
        return getattr(capture, name)


_INSTALL_LOCK = threading.Lock()


def install_safe_video_capture() -> type[SafeVideoCapture]:
    """Install an idempotent adapter on the process-wide OpenCV module.

    ``tracker.main`` imports the reconnect policy before opening a webcam, so
    installing here protects every capture candidate and every later operation
    on the selected handle. Tests can still monkeypatch ``cv2.VideoCapture`` in
    the normal way.
    """
    with _INSTALL_LOCK:
        current = cv2.VideoCapture
        if getattr(current, "__g3d_safe_video_capture__", False):
            return current

        original_factory = current

        class InstalledSafeVideoCapture(SafeVideoCapture):
            __g3d_safe_video_capture__ = True
            __g3d_original_factory__ = original_factory

            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(
                    *args,
                    _factory=original_factory,
                    **kwargs,
                )

        InstalledSafeVideoCapture.__name__ = "VideoCapture"
        InstalledSafeVideoCapture.__qualname__ = "VideoCapture"
        InstalledSafeVideoCapture.__module__ = "cv2"
        cv2.VideoCapture = InstalledSafeVideoCapture  # type: ignore[assignment]
        return InstalledSafeVideoCapture
