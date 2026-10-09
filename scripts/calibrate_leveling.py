"""What does `Even out` do to a file's spread? One row per source, measured fifth against fifth.

    venv\\Scripts\\python.exe scripts\\make_calibration_sources.py
    venv\\Scripts\\python.exe scripts\\calibrate_leveling.py

## Why this exists

The autodetector has to choose `Even out` for a file it has not heard, and the only honest way to choose a
number is to know what the number *does*. This runs the engine over sources whose spread is known and reports
what comes out, so the ratio it encodes is measured rather than guessed.

## The metric, and why not `ebur128`'s LRA

The **spread**: the average loudness of the quietest fifth of the material against the loudest fifth. This is
the metric the real-interview work used, so the figures here and the figures from that file are the same
quantity and can be compared.

`ebur128`'s own `LRA` is not used, and the reason is measured: on these sources — whose plateaus are exactly
12.0, 18.0, 24.0 and 6.0 LU apart, verified from the momentary profile — it reports **2.6, 2.9, 3.0 and 1.8 LU**.
It gates in 400 ms blocks and takes a range over its own percentiles, and two alternating tones are not the
material that statistic was designed for. The fifth-to-fifth spread reports 12.0, 18.0, 24.0 and 6.0 for the
same four files, which is what was asked for. **Use the metric that agrees with the thing being measured.**
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from normalizer import media as prober  # noqa: E402
from normalizer import normalize as normalizer  # noqa: E402
from normalizer.process import capture, measure_levels, tool  # noqa: E402

#: Anything quieter than this is a pause rather than a quiet passage.
SILENCE_LUFS = -70.0


def moments(path: Path) -> list[float]:
    """The momentary loudness of every 100 ms of sound in the file, with the pauses dropped."""
    _, _, errors = capture(
        [tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=none",
         "-f", "null", "-"],
        timeout=3600,
    )
    return [
        float(value)
        for value in re.findall(r"M:\s*(-?[\d.]+|-inf)", errors)
        if value != "-inf" and float(value) > SILENCE_LUFS
    ]


def spread(path: Path) -> float:
    """The distance between the quietest fifth of the material and the loudest, in LU."""
    values = sorted(moments(path))
    if not values:
        return 0.0
    fifth = max(1, len(values) // 5)
    return sum(values[-fifth:]) / fifth - sum(values[:fifth]) / fifth


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate Even out against a source's spread.")
    parser.add_argument("--sources", type=Path, default=Path("build/calibration"),
                        help="built by scripts/make_calibration_sources.py")
    parser.add_argument("--levels", type=float, nargs="+", default=[0.0, 4.0, 8.0, 12.0, 16.0, 24.0])
    parser.add_argument("--target", type=float, default=-6.0)
    options = parser.parse_args()

    sources = sorted(options.sources.glob("spread-*.wav"))
    if not sources:
        print(f"no sources in {options.sources}. Build them first:")
        print("  venv\\Scripts\\python.exe scripts\\make_calibration_sources.py")
        return 1

    where = options.sources / "out"
    where.mkdir(parents=True, exist_ok=True)
    try:
        print(f"{'source':>10} {'Even out':>9} {'spread out':>11} {'closed':>8} {'fraction':>9} {'peak':>7}")
        rows: list[tuple[float, float, float]] = []
        for source in sources:
            info = prober.probe(source)
            levels = prober.levels(info)
            before = spread(source)
            for leveling in options.levels:
                spec = normalizer.NormalizeSpec(
                    source=source,
                    output=where / f"{source.stem}-{leveling:g}.m4a",
                    target_dbfs=options.target,
                    strategy="chain",
                    makeup_db=12.0,
                    ceiling_dbfs=options.target,
                    leveling_db=leveling,
                )
                plan = normalizer.plan_normalize(spec, info, levels.max_dbfs, levels.mean_dbfs)
                result = normalizer.run_normalize(plan)
                after = spread(result.output)
                closed = before - after
                fraction = closed / before if before > 0 else 0.0
                peak = measure_levels(result.output).max_dbfs
                if before > 0:
                    rows.append((before, leveling, fraction))
                print(f"{before:>10.1f} {leveling:>9.1f} {after:>11.1f} {closed:>8.1f} "
                      f"{fraction:>9.2f} {peak:>7.2f}")
                result.output.unlink()

        print("\nWhat the detector encodes: the smallest leveling figure that gets the most closure.")
        print(f"{'source spread':>14} {'leveling':>9} {'fraction':>9} {'closed':>8}")
        for before in sorted({round(row[0]) for row in rows}):
            mine = [row for row in rows if abs(row[0] - before) < 0.6 and row[1] > 0]
            if not mine:
                continue
            best = max(row[2] for row in mine)
            enough = min(row[1] for row in mine if row[2] >= best - 0.03)
            print(f"{before:>14} {enough:>9.1f} {best:>9.2f} {before * best:>8.1f}")
    finally:
        shutil.rmtree(where, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
