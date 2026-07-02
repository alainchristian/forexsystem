"""Timestamped versioning for the model artifacts under models/.

main.py always loads from the fixed models/ path (lstm_model.keras,
lstm_scaler.pkl, xgboost_model.json, xgboost_meta.pkl). This module keeps a
history of every trained version under models/versions/<id>/ and promotes
one of them into that fixed path, so a bad retrain can be rolled back
instead of silently overwriting the working model with no way back.
"""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ModelVersioning")

ARTIFACT_FILES = [
    "lstm_model.keras", "lstm_scaler.pkl",
    "xgboost_model.json", "xgboost_meta.pkl",
]


def _manifest_path(models_dir: Path) -> Path:
    return models_dir / "versions" / "manifest.json"


def _load_manifest(models_dir: Path) -> dict:
    path = _manifest_path(models_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"active": None, "versions": []}


def _save_manifest(models_dir: Path, manifest: dict) -> None:
    path = _manifest_path(models_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def new_version_dir(models_dir: Path) -> Path:
    """Create a fresh timestamped directory for a training run's artifacts."""
    version_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    version_dir = models_dir / "versions" / version_id
    version_dir.mkdir(parents=True, exist_ok=False)
    return version_dir


def record_version(models_dir: Path, version_dir: Path, metadata: dict) -> None:
    """Write metadata.json for a version and add it to the manifest."""
    (version_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str), encoding="utf-8"
    )
    manifest = _load_manifest(models_dir)
    manifest["versions"].append({"id": version_dir.name, **metadata})
    _save_manifest(models_dir, manifest)


def promote(models_dir: Path, version_id: str) -> None:
    """Make a recorded version live by copying its artifacts into models/.

    Copies land as .tmp files first and are atomically renamed into place one
    at a time, so a crash mid-promote can't leave models/ with a mix of files
    from two different versions.
    """
    version_dir = models_dir / "versions" / version_id
    missing = [f for f in ARTIFACT_FILES if not (version_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Version {version_id} is missing artifacts: {missing}")

    for f in ARTIFACT_FILES:
        tmp_dest = models_dir / f"{f}.tmp"
        shutil.copy2(version_dir / f, tmp_dest)
        tmp_dest.replace(models_dir / f)

    manifest = _load_manifest(models_dir)
    manifest["active"] = version_id
    _save_manifest(models_dir, manifest)
    logger.info(f"Promoted version {version_id} to live models/")


def bootstrap_baseline(models_dir: Path) -> Optional[str]:
    """Capture whatever's already live as version zero, the first time this
    runs against a models/ directory that predates versioning. Without this,
    upgrading an existing deployment would have nothing to roll back to."""
    manifest = _load_manifest(models_dir)
    if manifest["versions"]:
        return None
    if not all((models_dir / f).exists() for f in ARTIFACT_FILES):
        return None

    mtime = max((models_dir / f).stat().st_mtime for f in ARTIFACT_FILES)
    version_id = datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S") + "_baseline"
    version_dir = models_dir / "versions" / version_id
    version_dir.mkdir(parents=True, exist_ok=True)
    for f in ARTIFACT_FILES:
        shutil.copy2(models_dir / f, version_dir / f)

    metadata = {
        "trained_at": datetime.fromtimestamp(mtime).isoformat(),
        "note": "captured from pre-existing live models before versioning was introduced",
    }
    record_version(models_dir, version_dir, metadata)
    manifest = _load_manifest(models_dir)
    manifest["active"] = version_id
    _save_manifest(models_dir, manifest)
    logger.info(f"Captured pre-existing live models as baseline version {version_id}")
    return version_id


def list_versions(models_dir: Path) -> list:
    return _load_manifest(models_dir)["versions"]


def active_version(models_dir: Path) -> Optional[str]:
    return _load_manifest(models_dir)["active"]


def rollback(models_dir: Path, version_id: Optional[str] = None) -> str:
    """Promote a specific version, or the one before the active version if none given."""
    manifest = _load_manifest(models_dir)
    versions = manifest["versions"]
    if not versions:
        raise RuntimeError("No versions recorded - nothing to roll back to")

    ids = [v["id"] for v in versions]
    if version_id is None:
        active = manifest["active"]
        if active in ids and ids.index(active) > 0:
            version_id = ids[ids.index(active) - 1]
        else:
            raise RuntimeError("No earlier version available to roll back to")
    elif version_id not in ids:
        raise ValueError(f"Unknown version_id: {version_id}")

    promote(models_dir, version_id)
    return version_id


def prune(models_dir: Path, keep: int = 8) -> None:
    """Delete version directories beyond the most recent `keep`, always
    preserving whichever version is currently active."""
    manifest = _load_manifest(models_dir)
    versions = manifest["versions"]
    if len(versions) <= keep:
        return

    ids_oldest_first = [v["id"] for v in versions]
    keep_ids = set(ids_oldest_first[-keep:])
    if manifest["active"]:
        keep_ids.add(manifest["active"])

    for vid in ids_oldest_first:
        if vid not in keep_ids:
            version_dir = models_dir / "versions" / vid
            if version_dir.exists():
                shutil.rmtree(version_dir)
            logger.info(f"Pruned old model version {vid}")

    manifest["versions"] = [v for v in versions if v["id"] in keep_ids]
    _save_manifest(models_dir, manifest)
