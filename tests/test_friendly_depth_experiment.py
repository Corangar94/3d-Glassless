import numpy as np

from tracker import friendly_depth_experiment


def _write_sequence(path, delta):
    path.mkdir()
    np.save(path / "a.npy", np.zeros((1, 1), dtype=np.float32))
    np.save(path / "b.npy", np.full((1, 1), delta, dtype=np.float32))


def test_run_experiment_blocks_without_policy_approval(tmp_path):
    external = tmp_path / "external"
    monocular = tmp_path / "mono"
    _write_sequence(external, 0.01)
    _write_sequence(monocular, 0.01)

    result = friendly_depth_experiment.run_experiment(
        title="Friendly Offline Title",
        external_depth_dir=external,
        monocular_depth_dir=monocular,
        policy_approved=False,
        offline_title=True,
    )

    assert result.allowed is False
    assert result.decision == "NO_GO"
    assert result.comparison is None


def test_run_experiment_compares_approved_offline_depth_sources(tmp_path):
    external = tmp_path / "external"
    monocular = tmp_path / "mono"
    _write_sequence(external, 0.01)
    _write_sequence(monocular, 0.01)

    result = friendly_depth_experiment.run_experiment(
        title="Friendly Offline Title",
        external_depth_dir=external,
        monocular_depth_dir=monocular,
        policy_approved=True,
        offline_title=True,
    )

    assert result.allowed is True
    assert result.decision == "GO"
    assert result.comparison is not None
    assert result.comparison.mean_delta_ratio == 1.0


def test_main_returns_nonzero_when_gate_blocks(tmp_path, capsys):
    external = tmp_path / "external"
    monocular = tmp_path / "mono"
    _write_sequence(external, 0.01)
    _write_sequence(monocular, 0.01)

    code = friendly_depth_experiment.main([
        "--title",
        "Protected Title",
        "--external-depth-dir",
        str(external),
        "--monocular-depth-dir",
        str(monocular),
    ])

    assert code == 1
    assert "decision=NO_GO" in capsys.readouterr().out
