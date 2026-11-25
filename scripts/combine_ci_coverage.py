"""Combine the two named CI coverage artifacts and emit JSON and XML reports."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from coverage import Coverage, CoverageData


def combine(artifacts, output):
    """Read both complete shards explicitly; a broken shard must fail the gate."""
    with TemporaryDirectory() as temporary:
        coverage = Coverage(data_file=Path(temporary) / ".coverage")
        combined = coverage.get_data()
        for name in ("functional", "migrations"):
            path = artifacts / f"pytest-{name}" / f".coverage.{name}"
            if not path.is_file():
                raise ValueError(f"Missing coverage shard: {path}")
            shard = CoverageData(basename=path)
            shard.read()
            if not shard.measured_files():
                raise ValueError(f"No measured files in coverage shard: {path}")
            combined.update(shard)
        coverage.json_report(outfile=str(output / "coverage.json"))
        coverage.xml_report(outfile=str(output / "coverage.xml"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()
    combine(args.artifacts, Path.cwd())
