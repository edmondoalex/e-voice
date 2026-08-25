"""HACS release packaging regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from scripts.build_hacs_release import build


def test_hacs_zip_contains_installable_beta11_connector(tmp_path: Path) -> None:
    output = tmp_path / "ekonex_voice.zip"
    build(output)

    with ZipFile(output) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert "evcp.py" in names
        assert not any(name.startswith("custom_components/") for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == "0.1.8-beta.11"
        assert b"correlation_id" in archive.read("evcp.py")


def test_hacs_manifest_requires_named_release_zip() -> None:
    manifest = json.loads(Path("hacs.json").read_text(encoding="utf-8"))
    assert manifest == {
        "name": "Ekonex Voice",
        "filename": "ekonex_voice.zip",
        "hide_default_branch": True,
        "zip_release": True,
    }
