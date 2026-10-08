"""What would a real leveler do to this file, with the limiter doing only its own job?

    venv\\Scripts\\python.exe scripts\\leveler_probe.py <source> [--seconds 90]

The product's `chain` reproduces the operator's Premiere Track Fx verbatim, and that compressor is `ratio=1`
— transparent — so the only dynamics it changes are the ones its limiter clamps. The question a real file
raises is what a *leveler* would add: a compressor with an actual ratio, lifting the body of the material
rather than only shaving its peaks.

This runs a small set of candidates over one excerpt and reports the same three figures for each, so the
trade is visible rather than asserted:

* **evened out** — how much further the loud parts moved than the quiet ones. Zero is a plain gain.
* **peak** — whether the limiter still holds the target, which is the one thing that must not break.
* **mean** — how much louder the material actually got at a listener's ear.

Every candidate ends the same way: the limiter at the ceiling, then a fader that puts the result exactly on
the target. That fader is what makes the figures comparable — without it, a candidate that clamps harder
simply comes out quieter and looks better than it is.
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
from normalizer.normalize import _linear  # noqa: E402
from normalizer.process import capture, measure_levels, tool  # noqa: E402

SILENCE_LUFS = -70.0


def momentary(path: Path) -> dict[float, float]:
    _, _, errors = capture(
        [tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=none", "-f", "null", "-"],
        timeout=3600,
    )
    return {
        round(float(at), 1): float(level)
        for at, level in re.findall(r"t:\s*([\d.]+).*?M:\s*(-?[\d.]+|-inf)", errors)
        if level != "-inf" and float(level) > SILENCE_LUFS
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="What a levelling compressor would add.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--target", type=float, default=-6.0)
    parser.add_argument("--ceiling", type=float, default=-6.0)
    options = parser.parse_args()

    limit = _linear(options.ceiling)
    operator = "acompressor=threshold=-20dB:ratio=1:attack=1:release=50:makeup=1"
    # The limiter and the out fader are the same in every candidate, so the comparison is about what is in
    # front of them and nothing else.
    tail = f"alimiter=limit={limit}:release=50:level=0,volume={-options.ceiling}dB"

    candidates = {
        "today: the operator's chain, +12":
            f"volume=12dB,{operator},{tail}",
        "today with more drive, +24":
            f"volume=24dB,{operator},{tail}",
        "gentle leveler (3:1, -24) then the chain":
            f"acompressor=threshold=-24dB:ratio=3:attack=15:release=250:makeup=1,volume=12dB,{operator},{tail}",
        "firm leveler (6:1, -30) then the chain":
            f"acompressor=threshold=-30dB:ratio=6:attack=8:release=200:makeup=1,volume=12dB,{operator},{tail}",
        "leveler only (6:1, -30), no +12 of drive":
            f"acompressor=threshold=-30dB:ratio=6:attack=8:release=200:makeup=1,{tail}",
        "hard leveler (12:1, -34)":
            f"acompressor=threshold=-34dB:ratio=12:attack=5:release=180:makeup=1,{tail}",
    }

    where = Path(tempfile.mkdtemp(prefix="leveler-"))
    try:
        piece = where / "excerpt.mkv"
        info = prober.probe(options.source)
        start = max(0.0, (info.duration or 0.0) / 2 - options.seconds / 2)
        capture([tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{options.seconds:.3f}",
                 "-i", str(options.source), "-map", "0", "-c", "copy", "-f", "matroska", str(piece)],
                timeout=1800)

        before = momentary(piece)
        if not before:
            print("the excerpt has no measurable sound in it")
            return 1
        ordered = sorted(before.items(), key=lambda pair: pair[1])
        fifth = max(1, len(ordered) // 5)
        quiet_src = sum(level for _, level in ordered[:fifth]) / fifth
        loud_src = sum(level for _, level in ordered[-fifth:]) / fifth
        print(f"excerpt: {options.seconds:g}s of {options.source.name}, peak "
              f"{measure_levels(piece).max_dbfs:.2f} dBFS")
        print(f"source: quietest fifth {quiet_src:.1f} LUFS, loudest fifth {loud_src:.1f} LUFS, "
              f"spread {loud_src - quiet_src:.1f} dB\\n")
        print(f"{'candidate':<44} {'peak':>7} {'mean':>7} {'quiet':>7} {'loud':>7} {'evened':>8} {'spread':>7}")

        for name, graph in candidates.items():
            out = where / (re.sub(r"\W+", "-", name) + ".mkv")
            capture([tool("ffmpeg"), "-y", "-v", "error", "-i", str(piece), "-af", graph,
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)], timeout=1800)
            after = momentary(out)
            pairs = [(at, level) for at, level in before.items() if at in after]
            if not pairs:
                print(f"{name:<44}  no comparable moments")
                continue
            moves = sorted((after[at] - level, level) for at, level in pairs)
            fifth = max(1, len(moves) // 5)
            quiet = sum(move for move, _ in moves[:fifth]) / fifth
            loud = sum(move for move, _ in moves[-fifth:]) / fifth
            mean = sum(move for move, _ in moves) / len(moves)
            values = sorted(after[at] for at, _ in pairs)
            spread = sum(values[-fifth:]) / fifth - sum(values[:fifth]) / fifth
            peak = measure_levels(out).max_dbfs
            print(f"{name:<44} {peak:>6.2f} {mean:>+6.1f} {quiet:>+6.1f} {loud:>+6.1f} "
                  f"{loud - quiet:>+7.1f} {spread:>6.1f}")
            out.unlink()
        print(f"\\nsource spread {loud_src - quiet_src:.1f} dB; a smaller 'spread' is a flatter file.")
    finally:
        shutil.rmtree(where, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
