"""How even is the voice, before and after? One answer, from the two files.

    venv\\Scripts\\python.exe scripts/voice_evenness.py <source> <normalized>

The objective of this product is that the **voices come out even** — a quiet guest as loud as a loud host, one
file as loud as the next. That is a statement about loudness over time, and no peak figure can check it: a
peak is one sample, and a file can hit its peak exactly while its voices are twenty decibels apart.

So this measures what the objective is about. `ebur128` reports the momentary loudness of every 100 ms; the
windows that are digitally silent are dropped, because a pause is not a quiet passage and including it would
report a range of a hundred decibels and mean nothing. What is left is the material, and the figures below
describe how far apart its quiet passages and its loud ones are:

* **the spread** — the quietest fifth of the material against the loudest fifth. This is the headline: it is
  the number that says whether a listener would call the voices even.
* **the deviation** — how far a typical passage sits from the file's own average. The spread can be driven by
  one outlier; this cannot.
* **the integrated loudness** — the whole file's perceived level, which is the figure two files have to share
  to sound equally loud beside each other.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from normalizer.process import capture, tool  # noqa: E402

#: Anything below this is a pause rather than a quiet passage.
SILENCE_LUFS = -70.0


def moments(path: Path) -> list[tuple[float, float]]:
    """The momentary loudness of every 100 ms of sound in the file, with the pauses dropped."""
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


def integrated(path: Path) -> float | None:
    _, _, errors = capture(
        [tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=none", "-f", "null", "-"],
        timeout=3600,
    )
    found = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)", errors)
    return None if found is None or found.group(1) == "-inf" else float(found.group(1))


def figures(path: Path) -> dict[str, float]:
    levels = [level for _, level in moments(path)]
    levels.sort()
    fifth = max(1, len(levels) // 5)
    quiet = sum(levels[:fifth]) / fifth
    loud = sum(levels[-fifth:]) / fifth
    # The central nine tenths: one clipped word or one cough should not decide how even a file is.
    trimmed = levels[len(levels) // 20 : len(levels) - len(levels) // 20] or levels
    return {
        "quiet": quiet,
        "loud": loud,
        "spread": loud - quiet,
        "deviation": statistics.pstdev(trimmed),
        "integrated": integrated(path) or 0.0,
        "windows": float(len(levels)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="How even are the voices, before and after?")
    parser.add_argument("source", type=Path)
    parser.add_argument("normalized", type=Path)
    options = parser.parse_args()

    for label, path in (("source", options.source), ("normalized", options.normalized)):
        if not path.is_file():
            print(f"{label} is not a file: {path}")
            return 1

    before = figures(options.source)
    after = figures(options.normalized)

    print(f"{before['windows'] / 10:.0f} seconds of sound in each, pauses dropped\n")
    print(f"{'':<22} {'source':>10} {'normalized':>12} {'change':>10}")
    for label, key, unit in (
        ("quietest fifth", "quiet", "LUFS"),
        ("loudest fifth", "loud", "LUFS"),
        ("the spread", "spread", "LU"),
        ("typical deviation", "deviation", "LU"),
        ("integrated loudness", "integrated", "LUFS"),
    ):
        change = after[key] - before[key]
        print(f"{label:<22} {before[key]:>10.1f} {after[key]:>12.1f} {change:>+10.1f} {unit}")

    closed = before["spread"] - after["spread"]
    print()
    if closed > 0:
        print(f"the spread between the quiet passages and the loud ones closed by {closed:.1f} dB — "
              f"{(1 - after['spread'] / before['spread']) * 100:.0f}% of the way to perfectly even")
    else:
        print(f"the spread did not close: it grew by {-closed:.1f} dB")

    if abs(before["deviation"]) > 0 and abs(after["deviation"]) > 0:
        print(f"a typical passage now sits {after['deviation']:.1f} LU from the file's own average, "
              f"against {before['deviation']:.1f} before")
    print(f"\nthe normalized file's whole level is {after['integrated']:.1f} LUFS. Two files at the same "
          f"integrated loudness sound equally loud beside each other; two files at the same *peak* need not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
