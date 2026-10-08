"""How much does more drive actually flatten a file? One row per drive value.

    venv\\Scripts\\python.exe scripts\\drive_sweep.py <source> [--levels 0 6 12 18 24] [--skip SLOW]

The question this answers is the one a report from a real test asked: *"I set +12 dB and the quieter parts
are still at a lower volume."* The answer is not a matter of opinion — a limiter's setting decides how much
of the material is clamped, and the only way to say what a drive value does is to run it and measure the
loudness of the quiet parts against the loud ones.

For each drive it runs the engine over a **short excerpt** with the given make-up, then measures the
momentary loudness of every 100 ms of the source and of the result and reports two figures:

* **the lift** — how much louder the result is, averaged over the material that has sound in it;
* **the flattening** — how much *further* the quietest fifth of the source moved than the loudest fifth.
  Zero means the file was moved as a whole and its dynamics are untouched; ten means the quiet parts were
  brought ten decibels closer to the loud ones.

Windows that are digital silence are dropped, because a file with a pause in it would otherwise report a
loudness range of a hundred decibels and mean nothing.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from normalizer import media as prober  # noqa: E402
from normalizer import normalize as normalizer  # noqa: E402
from normalizer.process import capture, measure_levels, tool  # noqa: E402

#: Anything quieter than this is a pause rather than a quiet passage, and is dropped from the statistics.
SILENCE_LUFS = -70.0


def momentary(path: Path) -> list[tuple[float, float]]:
    """The momentary loudness of every 100 ms, with the pauses dropped."""
    _, _, errors = capture(
        [tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=none", "-f", "null", "-"],
        timeout=3600,
    )
    return [
        (round(float(at), 1), float(level))
        for at, level in re.findall(r"t:\s*([\d.]+).*?M:\s*(-?[\d.]+|-inf)", errors)
        if level != "-inf" and float(level) > SILENCE_LUFS
    ]


def excerpt(source: Path, destination: Path, seconds: float) -> Path:
    """A short piece from the **middle** of the file, which is where speech actually is."""
    info = prober.probe(source)
    start = max(0.0, (info.duration or 0.0) / 2 - seconds / 2)
    capture(
        [tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
         "-i", str(source), "-map", "0", "-c", "copy", "-f", "matroska", str(destination)],
        timeout=1800,
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="What each drive value does to a file's dynamics.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--levels", type=float, nargs="+", default=[0.0, 6.0, 12.0, 18.0, 24.0, 36.0])
    parser.add_argument("--seconds", type=float, default=90.0, help="length of the excerpt to test on")
    parser.add_argument("--target", type=float, default=-6.0)
    parser.add_argument("--ceiling", type=float, default=-6.0)
    options = parser.parse_args()

    if not options.source.is_file():
        print(f"not a file: {options.source}")
        return 1

    where = Path(tempfile.mkdtemp(prefix="drive-sweep-"))
    try:
        piece = excerpt(options.source, where / "excerpt.mkv", options.seconds)
        print(f"excerpt: {options.seconds:g}s from the middle of {options.source.name}")
        print(f"target {options.target:.1f} dBFS, limiter ceiling {options.ceiling:.1f} dBFS\n")

        before = momentary(piece)
        if not before:
            print("the excerpt has no measurable sound in it")
            return 1

        print(f"{'drive':>7} {'peak':>9} {'mean lift':>10} {'quiet moved':>12} {'loud moved':>11} "
              f"{'evened out':>11}")
        for drive in options.levels:
            info = prober.probe(piece)
            levels = prober.levels(info)
            spec = normalizer.NormalizeSpec(
                source=piece,
                output=where / f"out-{drive:g}.mkv",
                target_dbfs=options.target,
                strategy="chain",
                makeup_db=drive,
                ceiling_dbfs=options.ceiling,
            )
            plan = normalizer.plan_normalize(spec, info, levels.max_dbfs, levels.mean_dbfs)
            result = normalizer.run_normalize(plan)

            after = {at: level for at, level in momentary(result.output)}
            pairs = [(at, one, after[at]) for at, one in before if at in after]
            if not pairs:
                print(f"{drive:>7.1f}  no comparable moments")
                continue

            moves = sorted(((two - one, one, two) for _, one, two in pairs))
            fifth = max(1, len(moves) // 5)
            quiet = sum(move for move, _, _ in moves[:fifth]) / fifth
            loud = sum(move for move, _, _ in moves[-fifth:]) / fifth
            peak = measure_levels(result.output).max_dbfs
            # `loud - quiet` and not the other way round: a *positive* figure means the loud parts moved
            # further and the quiet ones therefore did not keep up, which is the thing an operator is
            # asking about when they say "the quieter parts are still quieter".
            print(f"{drive:>7.1f} {peak:>8.2f} {sum(m for m, _, _ in moves) / len(moves):>+9.1f} "
                  f"{quiet:>+11.1f} {loud:>+10.1f} {loud - quiet:>+10.1f}")
            result.output.unlink()
    finally:
        shutil.rmtree(where, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
