"""How much did the quiet parts move? One second at a time, source against normalized.

    venv\\Scripts\\python.exe scripts\\loudness_profile.py <source> <normalized> [--step 1]

A peak normalizer's whole claim is that it changes the *level* and not the *dynamics* unless it is driven
into a limiter, and a single peak figure cannot tell you which of those happened: a file lifted 5 dB and a
file whose quiet halves were lifted 12 dB have the same peak. So this reports the RMS of every window of both
files and the difference between them, which is the thing an ear notices.

Written because the report from a real test was "the quieter parts are still in a lower volume", and the
question that settles it is *how much did each window move* — not what the whole file's peak is.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from normalizer.process import capture, tool  # noqa: E402


def windows(path: Path) -> list[tuple[float, float]]:
    """The momentary loudness of every 100 ms of the file, in LUFS.

    `ebur128` and not `astats`, because the question is what a *listener* hears: `astats` reports an RMS
    figure that weights a 30 Hz rumble and a 3 kHz vowel equally, and what "the quiet parts are still quiet"
    means is a perceived loudness. The momentary figure is the one that follows the ear.

    An m-line is printed every 100 ms whether or not the window has anything in it, so `-inf` windows — real
    digital silence — are dropped rather than averaged in as −120 dB.
    """
    _, _, errors = capture(
        [
            tool("ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=none",
            "-f",
            "null",
            "-",
        ],
        timeout=3600,
    )
    # `t: 12.3  TARGET:-23 LUFS  M: -17.4 S:... I: ... LUFS  LRA: ...` — the timestamp and the momentary
    # figure, which is the one that follows the ear.
    return [
        (float(at), float(momentary))
        for at, momentary in re.findall(
            r"t:\s*([\d.]+).*?M:\s*(-?[\d.]+|-inf)", errors
        )
        if momentary != "-inf"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-window loudness of two files, and the difference.")
    parser.add_argument("source", type=Path)
    parser.add_argument("normalized", type=Path)
    parser.add_argument("--every", type=int, default=50, help="print every Nth window, plus the extremes")
    options = parser.parse_args()

    for label, path in (("source", options.source), ("normalized", options.normalized)):
        if not path.is_file():
            print(f"{label} is not a file: {path}")
            return 1

    before = windows(options.source)
    after = windows(options.normalized)

    def figures(samples: list[tuple[float, float]]) -> str:
        values = [one for _, one in samples]
        return f"{len(values)} windows — min {min(values):.1f}, max {max(values):.1f} LUFS"

    print(f"source     : {figures(before)}")
    print(f"normalized : {figures(after)}")

    # Loudness is measured on a 400 ms sliding window, so the two files report at the same instants and their
    # momentary figures pair up by timestamp rather than by index.
    table = {at: one for at, one in after}
    pairs = [(at, one, table[at]) for at, one in before if at in table]
    if not pairs:
        print("\nno moment was measured in both files")
        return 1

    print(f"\n{'at':>9} {'source':>10} {'normalized':>12} {'moved':>9}")
    for index, (at, one, two) in enumerate(pairs):
        if index % options.every == 0:
            print(f"{at:>8.1f}s {one:>10.1f} {two:>12.1f} {two - one:>+9.1f}")

    ordered = sorted(pairs, key=lambda row: row[1])
    fifth = max(1, len(ordered) // 5)
    quiet_move = sum(two - one for _, one, two in ordered[:fifth]) / fifth
    loud_move = sum(two - one for _, one, two in ordered[-fifth:]) / fifth
    before_values = [one for _, one in before]
    after_values = [one for _, one in after]

    print(f"\nthe quietest fifth of the source moved {quiet_move:+.1f} dB on average")
    print(f"the loudest  fifth of the source moved {loud_move:+.1f} dB on average")
    print(f"\nloudness range: source {max(before_values) - min(before_values):.1f} LU -> "
          f"normalized {max(after_values) - min(after_values):.1f} LU")
    print(f"the quiet parts moved {quiet_move - loud_move:+.1f} dB relative to the loud ones: "
          f"{'they were lifted' if quiet_move > loud_move else 'the file was moved as a whole and its dynamics are unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
