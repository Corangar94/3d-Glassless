from pathlib import Path


def test_validation_matrix_covers_real_and_failure_boundaries():
    matrix = Path(
        "docs/OPENCV_FLOW_CONSISTENCY_VALIDATION.md"
    ).read_text(encoding="utf-8")

    for phrase in (
        "Perfect translation and uniform scale",
        "reverse path misses every origin by 2 px",
        "Six consistent points and two 3 px",
        "Accepted 0.75 px round-trip error",
        "Malformed reverse point/status/error lengths",
        "Forward or reverse LK backend exception",
        "Real synthetic image translated by 4×3 px",
        "same-frame cascade path",
    ):
        assert phrase in matrix
