import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from src import model_versioning as mv


def _write_artifacts(directory: Path, content: str = "x") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for f in mv.ARTIFACT_FILES:
        (directory / f).write_text(content)


def _make_version(models_dir: Path, version_id: str, content: str, **metadata) -> None:
    """Test helper: build a version directory directly (bypassing the
    real-time-based new_version_dir) so tests can control ordering/ids."""
    version_dir = models_dir / "versions" / version_id
    _write_artifacts(version_dir, content)
    mv.record_version(models_dir, version_dir, metadata)


def test_new_version_dir_is_unique_and_under_versions(tmp_path):
    v1 = mv.new_version_dir(tmp_path)
    assert v1.parent == tmp_path / "versions"
    assert v1.exists()


def test_promote_copies_artifacts_and_sets_active(tmp_path):
    _make_version(tmp_path, "v1", content="one")

    mv.promote(tmp_path, "v1")

    for f in mv.ARTIFACT_FILES:
        assert (tmp_path / f).read_text() == "one"
    assert mv.active_version(tmp_path) == "v1"


def test_promote_missing_artifact_raises(tmp_path):
    version_dir = tmp_path / "versions" / "broken"
    version_dir.mkdir(parents=True)
    (version_dir / mv.ARTIFACT_FILES[0]).write_text("x")  # only one of four files

    try:
        mv.promote(tmp_path, "broken")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_bootstrap_baseline_captures_preexisting_live_models(tmp_path):
    _write_artifacts(tmp_path, content="legacy")  # simulates a pre-versioning deployment

    version_id = mv.bootstrap_baseline(tmp_path)

    assert version_id is not None
    assert mv.active_version(tmp_path) == version_id
    versions = mv.list_versions(tmp_path)
    assert len(versions) == 1
    for f in mv.ARTIFACT_FILES:
        assert (tmp_path / "versions" / version_id / f).read_text() == "legacy"


def test_bootstrap_baseline_is_noop_when_versions_already_exist(tmp_path):
    _make_version(tmp_path, "v1", content="one")
    _write_artifacts(tmp_path, content="live")  # unrelated live files present too

    result = mv.bootstrap_baseline(tmp_path)

    assert result is None
    assert len(mv.list_versions(tmp_path)) == 1


def test_bootstrap_baseline_is_noop_when_nothing_live(tmp_path):
    assert mv.bootstrap_baseline(tmp_path) is None
    assert mv.list_versions(tmp_path) == []


def test_rollback_to_previous_version(tmp_path):
    _make_version(tmp_path, "v1", content="one")
    _make_version(tmp_path, "v2", content="two")
    mv.promote(tmp_path, "v1")
    mv.promote(tmp_path, "v2")
    assert mv.active_version(tmp_path) == "v2"

    restored = mv.rollback(tmp_path)

    assert restored == "v1"
    assert mv.active_version(tmp_path) == "v1"
    assert (tmp_path / mv.ARTIFACT_FILES[0]).read_text() == "one"


def test_rollback_to_explicit_version(tmp_path):
    _make_version(tmp_path, "v1", content="one")
    _make_version(tmp_path, "v2", content="two")
    _make_version(tmp_path, "v3", content="three")
    mv.promote(tmp_path, "v3")

    restored = mv.rollback(tmp_path, "v1")

    assert restored == "v1"
    assert (tmp_path / mv.ARTIFACT_FILES[0]).read_text() == "one"


def test_rollback_raises_when_no_earlier_version(tmp_path):
    _make_version(tmp_path, "v1", content="one")
    mv.promote(tmp_path, "v1")

    try:
        mv.rollback(tmp_path)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_rollback_raises_when_no_versions_recorded(tmp_path):
    try:
        mv.rollback(tmp_path)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_prune_keeps_most_recent_and_active(tmp_path):
    for i in range(1, 6):
        _make_version(tmp_path, f"v{i}", content=str(i))
    mv.promote(tmp_path, "v1")  # active is the oldest, deliberately

    mv.prune(tmp_path, keep=2)

    remaining_ids = {v["id"] for v in mv.list_versions(tmp_path)}
    # keep=2 most recent (v4, v5) plus the active one (v1) that would
    # otherwise have been pruned.
    assert remaining_ids == {"v1", "v4", "v5"}
    assert not (tmp_path / "versions" / "v2").exists()
    assert not (tmp_path / "versions" / "v3").exists()
    assert (tmp_path / "versions" / "v1").exists()


def test_prune_below_keep_threshold_is_noop(tmp_path):
    _make_version(tmp_path, "v1", content="one")
    _make_version(tmp_path, "v2", content="two")

    mv.prune(tmp_path, keep=8)

    assert len(mv.list_versions(tmp_path)) == 2
    assert (tmp_path / "versions" / "v1").exists()
    assert (tmp_path / "versions" / "v2").exists()
