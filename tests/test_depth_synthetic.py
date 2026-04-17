import numpy as np

from tracker.depth_synthetic import (
    main,
    make_breathing_depth_sequence,
    make_static_depth_sequence,
    write_depth_sequence,
)


def test_make_static_depth_sequence_repeats_same_gradient():
    frames = make_static_depth_sequence(frame_count=3, width=4, height=2)

    assert len(frames) == 3
    assert frames[0].shape == (2, 4)
    assert np.array_equal(frames[0], frames[1])
    assert frames[0][0, 0] == 0.0
    assert frames[0][0, -1] == 1.0


def test_make_breathing_depth_sequence_changes_over_time():
    frames = make_breathing_depth_sequence(frame_count=3, width=2, height=2, amplitude=0.1)

    assert len(frames) == 3
    assert not np.array_equal(frames[0], frames[1])
    assert np.all(frames[1] <= 1.0)
    assert np.all(frames[1] >= 0.0)


def test_write_depth_sequence_creates_sorted_npy_files(tmp_path):
    frames = make_static_depth_sequence(frame_count=2, width=2, height=2)

    write_depth_sequence(tmp_path, frames)

    assert sorted(p.name for p in tmp_path.glob("*.npy")) == [
        "frame_0000.npy",
        "frame_0001.npy",
    ]
    loaded = np.load(tmp_path / "frame_0001.npy")
    assert np.array_equal(loaded, frames[1])


def test_main_writes_static_sequence(tmp_path, capsys):
    code = main([
        str(tmp_path),
        "--mode",
        "static",
        "--frames",
        "2",
        "--width",
        "2",
        "--height",
        "2",
    ])

    assert code == 0
    assert (tmp_path / "frame_0000.npy").exists()
    assert "wrote 2 depth frames" in capsys.readouterr().out
