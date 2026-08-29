from tracker.safe_video_capture import SafeVideoCapture


class _AmbiguousStatus:
    def __bool__(self) -> bool:
        raise ValueError("ambiguous backend status")


class _NativeCapture:
    def read(self):
        return _AmbiguousStatus(), object()

    def retrieve(self):
        return _AmbiguousStatus(), object()


def test_read_status_boolean_conversion_failure_is_contained():
    cap = SafeVideoCapture(_factory=lambda: _NativeCapture())

    assert cap.read() == (False, None)
    assert cap.failures[-1].stage == "read"
    assert cap.failures[-1].error_type == "ValueError"


def test_retrieve_status_boolean_conversion_failure_is_contained():
    cap = SafeVideoCapture(_factory=lambda: _NativeCapture())

    assert cap.retrieve() == (False, None)
    assert cap.failures[-1].stage == "retrieve"
    assert cap.failures[-1].error_type == "ValueError"
