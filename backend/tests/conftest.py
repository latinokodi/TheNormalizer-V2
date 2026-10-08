"""Shared fixtures and helpers for the engine's tests.

## What is deliberately not here

No media files. Every test in this suite works on a literal `MediaInfo(...)` with a `Fraction` rate, on
argument lists built from a plan, or on a monkeypatched seam — so the suite runs on a machine with no
footage, in about a second, and never launches ffmpeg except the one health check. The proof that these
argument lists are ones ffmpeg *accepts* is `scripts/check_normalize.py`, which runs the real engine over
real files it builds itself.

## Why the seams are the project's own

`process.run`, `process.measure_levels` and `media.probe` are patched rather than `subprocess.Popen` at
large. Patching the standard library tests the mock: a test that replaces `Popen` asserts that the code
under test called `Popen`, which is not a claim anyone cares about, and it stops working the moment the
implementation uses a different constructor. The seams here are the three functions this project owns,
and each one is a *decision* — "here is where a process starts", "here is where a level is measured",
"here is where a file is read".
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from normalizer import media as prober  # noqa: E402
from normalizer import normalize as normalizer  # noqa: E402


def an_audio(path: Path = Path("C:/media/talk.wav"), *, rate: int = 48000, channels: int = 2):
    """A sound-only file, as `probe` would report it. No disk is touched."""
    return prober.MediaInfo(
        path=path,
        container="wav",
        duration=6.0,
        size_bytes=1_152_000,
        audio=prober.AudioStream(codec="pcm_s16le", sample_rate=rate, channels=channels),
        video=None,
        streams=(("audio", "pcm_s16le"),),
    )


def a_video(path: Path = Path("C:/media/clip.mp4"), *, frames: int = 150, rate: Fraction = Fraction(25, 1)):
    """A file with a picture and a sound, as `probe` would report it."""
    return prober.MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=float(Fraction(frames, 1) / rate),
        size_bytes=5_000_000,
        audio=prober.AudioStream(codec="aac", sample_rate=48000, channels=2, bit_rate=192_000),
        video=prober.VideoStream(
            codec="h264",
            profile="High",
            level=41,
            width=1920,
            height=1080,
            pix_fmt="yuv420p",
            rate=rate,
            frames=frames,
            timebase=Fraction(1, 12800),
            has_b_frames=True,
        ),
        streams=(("video", "h264"), ("audio", "aac")),
    )


def a_spec(source: Path = Path("C:/media/talk.wav"), **overrides):
    """A request with this product's defaults, and only the named fields changed.

    The output path is built by `normalizer.output_for`, which is what the route and the script use. A
    fixture that wrote `parent / (stem + suffix)` itself would be a second spelling of "where the file
    goes" — and the first thing it would get wrong is a source whose own name contains a dot.
    """
    fields = {
        "source": source,
        "output": normalizer.output_for(source),
        "target_dbfs": normalizer.DEFAULT_TARGET_DBFS,
        "strategy": "gain",
        "makeup_db": normalizer.DEFAULT_MAKEUP_DB,
        "ceiling_dbfs": normalizer.DEFAULT_CEILING_DBFS,
        "trim_db": 0.0,
        "audio_exports": (),
        "audio_bitrate": "192k",
    }
    fields.update(overrides)
    return normalizer.NormalizeSpec(**fields)


def a_plan(source=None, *, peak: float | None = -27.1, mean: float | None = -30.1, **overrides):
    """A plan for that file, without touching the disk or starting anything.

    `plan_normalize` is pure, which is exactly what makes it usable here: the whole arithmetic of the
    product can be exercised by passing it numbers.
    """
    info = source if source is not None else an_audio()
    spec = a_spec(info.path, **overrides)
    return normalizer.plan_normalize(spec, info, peak, mean)


@pytest.fixture
def no_processes(monkeypatch):
    """Replace the seams with recorders, and give back what they recorded.

    Returns a dictionary with:

    * ``commands`` — the argument lists `run` was asked to execute;
    * ``measured`` — the files a level was measured from, in order;
    * ``levels`` — the level the *stage* file measures at (the samples the encoder was handed);
    * ``overshoot`` — how far the decoder exceeds those samples, added for a file whose name ends
      `-decoded.wav`. It defaults to 0.6 dB, which is what this machine's ffmpeg does to a tone, so the
      fake behaves like the codec rather than like a convenience;
    * ``default`` — that stage level, −6.0 dBFS by default.

    The two levels are different by construction, because that difference *is* the thing the run
    measures. A double that returned one number for both would make `overshoot_db` zero and quietly
    stop exercising the correction the whole two-pass arrangement exists for — which is exactly what
    happened when this fixture was written first.
    """
    from normalizer import process as process_module

    recorded: dict = {"commands": [], "measured": [], "levels": {}, "overshoot": 0.6, "default": -6.0}

    def fake_run(args, **kwargs):
        recorded["commands"].append(list(args))
        return None

    def fake_measure(path, timeout=None):
        where = Path(path)
        recorded["measured"].append(where)
        base = recorded["levels"].get(where.name, recorded["default"])
        value = base
        if where.name.endswith("-decoded.wav"):
            value = base + recorded["overshoot"]
        return process_module.Levels(mean_dbfs=value, max_dbfs=value, seconds=6.0)

    monkeypatch.setattr(process_module, "run", fake_run)
    monkeypatch.setattr(normalizer, "run", fake_run, raising=False)
    monkeypatch.setattr(normalizer, "measure_levels", fake_measure, raising=False)
    # `_measure_decoded_aac` decodes through `capture`, and it must succeed: the fake measurement above
    # is what the decoded file would have said.
    monkeypatch.setattr(normalizer, "capture", lambda *a, **k: (0, "", ""))
    return recorded
