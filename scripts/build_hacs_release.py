"""Build the deterministic HACS ZIP release asset."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

INTEGRATION_PATH = Path("custom_components/ekonex_voice")
ARCHIVE_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def build(output: Path) -> None:
    """Write integration contents at the ZIP root, as required by HACS."""
    files = sorted(
        path
        for path in INTEGRATION_PATH.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = ZipInfo(path.relative_to(INTEGRATION_PATH).as_posix(), ARCHIVE_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path, default=Path("ekonex_voice.zip"))
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
