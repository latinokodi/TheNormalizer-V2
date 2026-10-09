"""Build a source with a known loudness range, the simplest way that works.

    venv\\Scripts\\python.exe scripts\\make_calibration_sources.py [--out DIR]

## Why this file exists several times over

`scripts/calibrate_leveling.py` needs a source whose loudness range is *known*, so that what the leveler closes
can be attributed to the leveler. Five earlier attempts built one from a single `aeval` expression or a
`concat` filter graph, and every one failed on ffmpeg's filter-syntax trivia rather than on the idea:

1. `aeval` has no sample-rate option — the rate is an output option.
2. `aeval` has no duration option, and a lavfi source with no duration cannot be seeked at all.
3. `aeval` splits its arguments on commas, so every comma inside the expression needs escaping.
4. A label declared to the *left* of a filter is an input, not an output, so `[0:a]volume=-24dB[q]` consumed
   `[q]` as an input and left the `volume` with nowhere to write — every source came out at full level.
5. Filter *chains* need `;` between them, and without it the whole graph is one chain that fails with
   "Trailing garbage after a filter".

Each arrived as a message that said nothing about its cause. **Two plain files joined by the concat demuxer has
none of that machinery**: a tone at one level, a tone at another, and a list of the two. The only thing left
that can be wrong is whether ffmpeg wrote what was asked, and that is checked by measuring the result.

## The measurement, and the honest limit of it

`ebur128` on a pair of alternating pure tones reports a loudness range, and the figure moves with the level
difference — so the source is usable for *comparing* leveling settings. It is **not** the same number a real
interview reports for the same nominal spread: a meter's loudness range depends on the spectrum and on how the
material moves, and two tones are not speech. The calibration is therefore read as *"how much of this source's
range does each leveling figure close"*, which is the shape of the relationship, and the detector's constants
are set from that shape rather than from the absolute figures.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from normalizer.process import capture, tool  # noqa: E402


def loudness_range(path: Path) -> float | None:
    """The loudness range `ebur128` reports for a whole file, in LU. `None` when it reports nothing."""
    _, _, errors = capture(
        [tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=none",
         "-f", "null", "-"],
        timeout=1800,
    )
    tail = errors[errors.rfind("Integrated loudness"):]
    found = re.search(r"LRA:\s*(-?[\d.]+)\s*LU", tail)
    return float(found.group(1)) if found else None


def build(destination: Path, spread_db: float, plateaus: int = 8, seconds: float = 2.0) -> float | None:
    """Alternate `plateaus` stretches of a loud tone and a `spread_db`-quieter one; report the range.

    Many short plateaus rather than a few long ones: `ebur128` gates in 400 ms blocks and needs enough blocks
    above its gate to report a range at all, and a single pair of two-second plateaus measurably did not give
    it enough.
    """
    where = Path(tempfile.mkdtemp(prefix="calib-"))
    try:
        loud = where / "loud.wav"
        quiet = where / "quiet.wav"
        for level, target in ((0.0, loud), (-spread_db, quiet)):
            capture(
                [tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
                 f"sine=frequency=220:duration={seconds:g}", "-af", f"volume={level:.6f}dB",
                 "-ar", "48000", "-c:a", "pcm_s24le", str(target)],
                timeout=300,
            )
        listing = where / "list.txt"
        listing.write_text(
            "".join(f"file '{loud.as_posix()}'\nfile '{quiet.as_posix()}'\n" for _ in range(plateaus // 2)),
            encoding="utf-8",
        )
        capture(
            [tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(destination)],
            timeout=300,
        )
        return loudness_range(destination)
    finally:
        shutil.rmtree(where, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a source with a known loudness range.")
    parser.add_argument("--out", type=Path, default=Path("build/calibration"))
    parser.add_argument("--spreads", type=float, nargs="+", default=[24.0, 18.0, 12.0, 6.0])
    parser.add_argument("--seconds", type=float, default=2.0, help="the length of one plateau")
    options = parser.parse_args()

    options.out.mkdir(parents=True, exist_ok=True)
    print(f"{'asked for':>10} {'measured LRA':>13}  file")
    for spread in options.spreads:
        target = options.out / f"spread-{spread:g}.wav"
        measured = build(target, spread, seconds=options.seconds)
        shown = "none" if measured is None else f"{measured:.1f} LU"
        print(f"{spread:>10.1f} {shown:>13}  {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
