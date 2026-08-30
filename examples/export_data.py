"""Fetch a symbol's history and write it to a flat file (format is a
Registry(FileFormat, ...) entry -- "csv"/"parquet" ship built in), independent
of any backtest -- no strategies, no report, just data in, data out. Reuses
the same `data:` config section a backtest config already declares.

Usage:
    python -m examples.export_data examples/export_mu_config.yaml

For a transform (a `DataFrame -> DataFrame` UDF applied before writing), call
`tam.data.export.run_export()`/`export_history()` directly from a script or
notebook instead -- arbitrary code has no YAML representation, so this CLI
only covers the declarative fetch+write path.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tam.data.export import run_export


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to an export YAML config (data: + export: sections)")
    args = parser.parse_args()

    out_path = run_export(args.config)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
