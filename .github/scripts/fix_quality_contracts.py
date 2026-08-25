from pathlib import Path


files = {
    path: Path(path).read_text(encoding="utf-8")
    for path in (
        "tracker/camera_geometry.py",
        "tests/test_wizard.py",
    )
}


def replace_once(path: str, old: str, new: str) -> None:
    text = files[path]
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    files[path] = text.replace(old, new, 1)


replace_once(
    "tracker/camera_geometry.py",
    '''def euler_degrees_from_rotation_matrix(rotation: np.ndarray) -> tuple[float, float, float]:
    matrix = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    sy = math.hypot(float(matrix[0, 0]), float(matrix[1, 0]))
    if sy > 1e-8:
        pitch = math.atan2(float(matrix[2, 1]), float(matrix[2, 2]))
        yaw = math.atan2(-float(matrix[2, 0]), sy)
        roll = math.atan2(float(matrix[1, 0]), float(matrix[0, 0]))
    else:
        pitch = math.atan2(-float(matrix[1, 2]), float(matrix[1, 1]))
        yaw = math.atan2(-float(matrix[2, 0]), sy)
        roll = 0.0
    return tuple(math.degrees(value) for value in (yaw, pitch, roll))
''',
    '''def euler_degrees_from_rotation_matrix(rotation: np.ndarray) -> tuple[float, float, float]:
    """Invert ``Rz(roll) @ Rx(pitch) @ Ry(yaw)`` without cross-axis drift."""
    matrix = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    pitch = math.asin(max(-1.0, min(1.0, float(matrix[2, 1]))))
    cosine_pitch = math.cos(pitch)
    if abs(cosine_pitch) > 1e-8:
        yaw = math.atan2(-float(matrix[2, 0]), float(matrix[2, 2]))
        roll = math.atan2(-float(matrix[0, 1]), float(matrix[1, 1]))
    else:
        # Gimbal lock: preserve yaw from the remaining horizontal basis and
        # choose a neutral roll because the two axes are not independently observable.
        yaw = math.atan2(float(matrix[0, 2]), float(matrix[0, 0]))
        roll = 0.0
    return tuple(math.degrees(value) for value in (yaw, pitch, roll))
''',
)

replace_once(
    "tests/test_wizard.py",
    '''        "depth_performance_mode": "balanced",
''',
    '''        "depth_performance_mode": "auto",
''',
)

for path, content in files.items():
    Path(path).write_text(content, encoding="utf-8", newline="\n")
