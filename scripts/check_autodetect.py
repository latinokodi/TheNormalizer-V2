"""Autodetect on a real file, end to end, through the running engine's own API.

    venv\\Scripts\\python.exe backend\\server.py          (in one window, or backgrounded)
    venv\\Scripts\\python.exe scripts\\check_autodetect.py

The unit tests, the feature file and the server tests all decide the *rule*. This decides the part none of them
can: that a real file measured by the real engine, through the real route, produces a suggestion that this
product can then run. It builds a source with a spread it knows, asks the engine what to measure, asks the
detector what to do about it, and then runs that suggestion — so the number that comes out at the far end is
the check on the number at the near end.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from normalizer.process import capture, measure_levels, tool  # noqa: E402

BACKEND = "http://127.0.0.1:8767"
#: The two plateaus, and therefore the spread the source is built with.
LOUD_DBFS = -10.0
QUIET_DBFS = -30.0


def health() -> dict | None:
    """The engine's own answer, or `None` when it is not there."""
    try:
        with urllib.request.urlopen(f"{BACKEND}/api/health", timeout=10) as answer:
            body = json.loads(answer.read())
    except Exception:
        return None
    return body if body.get("product") == "TheNormalizer" else None


def get(path: str):
    with urllib.request.urlopen(f"{BACKEND}{path}", timeout=1800) as answer:
        body = answer.read()
        return answer.status, (json.loads(body) if body else None)


def build_source(where: Path) -> Path:
    """A file whose loud and quiet stretches are `LOUD_DBFS - QUIET_DBFS` apart."""
    loud = where / "loud.wav"
    quiet = where / "quiet.wav"
    for level, target in ((LOUD_DBFS, loud), (QUIET_DBFS, quiet)):
        capture(
            [tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
             "-af", f"volume={level}dB", "-ar", "48000", "-c:a", "pcm_s24le", str(target)],
            timeout=300,
        )
    listing = where / "list.txt"
    listing.write_text(
        "".join(f"file '{loud.as_posix()}'\nfile '{quiet.as_posix()}'\n" for _ in range(4)),
        encoding="utf-8",
    )
    source = where / "spread.wav"
    capture([tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(source)], timeout=300)
    return source


def main() -> int:
    engine = health()
    if engine is None:
        print(f"nothing answering as TheNormalizer on {BACKEND}. Start it first:")
        print("  venv\\Scripts\\python.exe backend\\server.py")
        return 1

    where = Path(tempfile.mkdtemp(prefix="autodetect-"))
    failures: list[str] = []

    def check(claim: str, held: bool, detail: str) -> None:
        print(f"  {'ok  ' if held else 'FAIL'} {claim}: {detail}")
        if not held:
            failures.append(claim)

    try:
        source = build_source(where)
        spread_asked = LOUD_DBFS - QUIET_DBFS
        print(f"\nsource: two 6-second plateaus {spread_asked:g} dB apart\n")

        status, probe = get("/api/probe?path=" + urllib.parse.quote(str(source)))
        check("the engine measured the file", status == 200, f"HTTP {status}")
        loudness = probe["loudness"]
        print(f"       peak {probe['levels']['peakText']}, {loudness['integratedText']}, "
              f"spread {loudness['rangeText']}")
        check(
            "the source really has the spread it was built with",
            loudness["rangeLu"] is not None,
            f"the engine reports {loudness['rangeText']} for a file {spread_asked:g} dB apart "
            f"(the metered range is not the plateau distance — see docs/AUTODETECT.md §4)",
        )

        query = urllib.parse.urlencode({
            "peakDbfs": probe["levels"]["peakDbfs"],
            "integratedLufs": loudness["integratedLufs"],
            "rangeLu": loudness["rangeLu"],
            "targetDbfs": -6.0,
            "makeupDb": 12.0,
        })
        status, answer = get("/api/suggestions?" + query)
        check("the route offered a suggestion", status == 200, f"HTTP {status}")
        if status != 200:
            return 1
        suggestion = answer["suggestion"]
        print(f"\nthe engine suggests: level {suggestion['levelDbfs']} dBFS, "
              f"even out {suggestion['evenOut']}, make up {suggestion['makeupDb']:+.1f} dB")
        for reason in suggestion["reasons"]:
            print(f"  - {reason}")

        check(
            "every figure came with its reason",
            len(suggestion["reasons"]) >= 3,
            f"{len(suggestion['reasons'])} reasons",
        )
        check(
            "the suggestion is inside what this product can run",
            -24.0 <= suggestion["levelDbfs"] <= 0.0 and 0.0 <= suggestion["evenOut"] <= 18.0,
            f"level {suggestion['levelDbfs']}, even out {suggestion['evenOut']}",
        )

        # The suggestion, run through the engine and measured at the far end.
        body = {
            "sources": [str(source)],
            "targetDbfs": suggestion["levelDbfs"],
            "ceilingDbfs": suggestion["levelDbfs"],
            "strategy": "chain",
            "makeupDb": suggestion["makeupDb"],
            "levelingDb": suggestion["evenOut"],
            "peakDbfs": probe["levels"]["peakDbfs"],
            "meanDbfs": probe["levels"]["meanDbfs"],
        }
        request = urllib.request.Request(
            f"{BACKEND}/api/normalization-plans", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=1800) as answer_stream:
            plan = json.loads(answer_stream.read())["plan"]
        check(
            "the suggested figures are the ones the plan will run",
            plan["levelingDb"] == suggestion["evenOut"]
            and plan["targetDbfs"] == suggestion["levelDbfs"],
            f"leveler {plan['levelingDb']}, target {plan['targetDbfs']}",
        )
        check(
            "and the plan carries a leveler for a file that needs one",
            plan["levelsDynamics"] is True,
            f"even out {plan['levelingDb']} — the source is "
            f"{spread_asked:g} dB wide",
        )

        written = where / "out.m4a"
        from normalizer import media as prober
        from normalizer import normalize as normalizer

        info = prober.probe(source)
        levels = prober.levels(info)
        spec = normalizer.NormalizeSpec(
            source=source, output=written, target_dbfs=suggestion["levelDbfs"], strategy="chain",
            makeup_db=suggestion["makeupDb"], ceiling_dbfs=suggestion["levelDbfs"],
            leveling_db=suggestion["evenOut"],
        )
        built = normalizer.plan_normalize(spec, info, levels.max_dbfs, levels.mean_dbfs)
        result = normalizer.run_normalize(built)
        delivered = measure_levels(result.output).max_dbfs
        check(
            "the run reached the level the suggestion named",
            abs(delivered - suggestion["levelDbfs"]) <= normalizer.PEAK_TOLERANCE_DB,
            f"asked for {suggestion['levelDbfs']:.1f} dBFS, delivered {delivered:.2f} dBFS",
        )
    finally:
        shutil.rmtree(where, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} claim(s) did not hold:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("every claim held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
