"""Measuring what was written.

The verifier is the part of this product that is allowed to say *no*, so its three verdicts — `passed`,
`failed` and `not_checked` — are tested as three separate things. Collapsing `not_checked` into
`passed` is how a report stops meaning anything, and collapsing a failure into a warning is how a
normalizer delivers a file at the wrong level and calls it normal.

Nothing here launches ffmpeg: `media.probe`, `process.measure_levels`, `media.stream_duration`,
`media.packet_times` and the two checksum readers are all monkeypatched, because the thing under test
is the *decision* each figure leads to, not ffmpeg's ability to produce it.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from conftest import a_plan, a_video
from normalizer import media as prober
from normalizer import normalize as normalizer
from normalizer import process as process_module
from normalizer import verify as verifier

WORK = Path("C:/work")


@pytest.fixture
def output(tmp_path):
    """A file that exists, so `verify` gets past its first sentence."""
    where = tmp_path / "talk - normalized.m4a"
    where.write_bytes(b"pretend this is an m4a")
    return where


@pytest.fixture
def measured(monkeypatch):
    """Everything `verify` reads back, as values a test sets.

    One dictionary rather than six monkeypatches per test: a test says what the file it wrote *is*,
    and the verifier's job is to say whether that is right.
    """
    state: dict = {
        "peak": -6.0,
        "mean": -9.0,
        "format": "mov,mp4,m4a,3gp,3g2,mj2",
        "sound_seconds": 6.0,
        "picture_times": [],
        "frames": 150,
        "checksums": lambda path, start, count, rate: [f"0:{start + i}:hash{start + i}" for i in range(count)],
        "decode_ok": True,
    }

    def fake_probe(path):
        audio = prober.AudioStream(codec="aac", sample_rate=48000, channels=2)
        video = prober.VideoStream(
            codec="h264",
            profile="High",
            level=41,
            width=1920,
            height=1080,
            pix_fmt="yuv420p",
            rate=Fraction(25, 1),
            frames=state["frames"],
            timebase=Fraction(1, 12800),
            has_b_frames=True,
        )
        return prober.MediaInfo(
            path=Path(path),
            container=state["format"],
            duration=6.0,
            size_bytes=1234,
            audio=audio,
            video=video if state.get("has_picture", True) else None,
        )

    def fake_levels(path, timeout=None):
        return process_module.Levels(
            mean_dbfs=state["mean"], max_dbfs=state["peak"], seconds=6.0
        )

    monkeypatch.setattr(verifier.media_module, "probe", fake_probe)
    monkeypatch.setattr(verifier, "measure_levels", fake_levels)
    monkeypatch.setattr(verifier.media_module, "stream_duration", lambda path, stream="a:0": state["sound_seconds"])
    monkeypatch.setattr(verifier.media_module, "packet_times", lambda path: list(state["picture_times"]))
    monkeypatch.setattr(verifier.media_module, "frame_checksums", state["checksums"])
    monkeypatch.setattr(verifier.media_module, "count_frames", lambda path: state["frames"])

    def fake_probe_json(args, timeout=None):
        return {"format": {"format_name": state["format"]}}

    monkeypatch.setattr(verifier, "probe_json", fake_probe_json)
    # `_sound_decodes` keeps the *module* — `process.capture` — rather than the function, so this is the
    # seam. Patching `verifier.capture` would be patching a name that does not exist, and the failure it
    # produces is an `AttributeError` in the fixture rather than a failing assertion.
    monkeypatch.setattr(
        verifier.process_module,
        "capture",
        lambda *a, **k: (0, "", "")
        if state["decode_ok"]
        else (1, "", "decode_band_types: Input buffer exhausted before END element found"),
    )
    return state


def row(result: verifier.VerifyResult, name: str) -> verifier.Check:
    for check in result.checks:
        if check.name == name:
            return check
    raise AssertionError(f"no check named {name!r}; there are {[c.name for c in result.checks]}")


# ---------------------------------------------------------------------------------------
# The level, which is the point of the product
# ---------------------------------------------------------------------------------------


def test_a_level_on_the_target_passes(measured, output):
    measured["peak"] = -6.3
    result = verifier.verify(a_plan(), output)
    assert row(result, "target level").status == "passed"
    assert "-6.30 dBFS" in row(result, "target level").detail
    assert result.ok


def test_a_level_off_the_target_fails_and_says_by_how_much(measured, output):
    measured["peak"] = -1.0
    result = verifier.verify(a_plan(), output)
    assert row(result, "target level").status == "failed"
    assert "+5.00 dB" in row(result, "target level").detail
    assert not result.ok, "a normalizer that missed its target has failed its own job"


def test_the_tolerance_is_the_codec_s_resolution_and_not_the_instrument_s(measured, output):
    """`volumedetect` reads to 0.1 dB; the delivered peak is one lossy encode away from the plan.

    Measured: a master written at exactly −6.00 dBFS in PCM comes back out of an AAC decode at −5.40. A
    tolerance of 0.3 would fail this product's own correct runs, and one of 1.0 fails a chain run whose
    material is quiet enough that its limiter never engages — 1.30 dB was the worst of 36 measured
    combinations, and it is the chain's own second-order term: its out fader is derived from a
    measurement taken *before* the master's encode.
    """
    assert normalizer.PEAK_TOLERANCE_DB == 1.5
    measured["peak"] = -6.6
    assert row(verifier.verify(a_plan(), output), "target level").status == "passed"
    measured["peak"] = -4.4
    assert row(verifier.verify(a_plan(), output), "target level").status == "failed"


def test_silence_stays_silent(measured, output):
    measured["peak"] = None
    measured["mean"] = None
    result = verifier.verify(a_plan(peak=None, mean=None), output)
    assert row(result, "target level").status == "passed"
    assert "no gain was applied" in row(result, "target level").detail


def test_a_silent_source_that_came_back_loud_is_a_failure(measured, output):
    """Something added signal that was not there, and that is worth failing over."""
    measured["peak"] = -20.0
    result = verifier.verify(a_plan(peak=None, mean=None), output)
    assert row(result, "target level").status == "failed"


def test_sound_that_was_lost_is_a_failure(measured, output):
    measured["peak"] = None
    result = verifier.verify(a_plan(), output)
    assert row(result, "target level").status == "failed"
    assert "The sound was lost" in row(result, "target level").detail


# ---------------------------------------------------------------------------------------
# Three verdicts, and they must not look alike
# ---------------------------------------------------------------------------------------


def test_a_file_with_no_picture_has_no_picture_to_compare(measured, output):
    measured["has_picture"] = False
    plan = a_plan()  # sound-only, so the plan has no picture either
    result = verifier.verify(plan, output)
    check = row(result, "picture copied")
    assert check.status == "not_checked"
    assert check.status != "passed", "a check that did not run must not read as a pass"
    assert result.ok, "not_checked does not make a run fail"
    assert result.unchecked


def test_a_picture_that_was_copied_passes_and_one_that_was_not_fails(measured, output, monkeypatch):
    plan = a_plan(a_video())
    assert row(verifier.verify(plan, output), "picture copied").status == "passed"

    # The output's frames differ from the source's at every sampled point. `frame_checksums` takes the
    # path, so the fake has to ask which file it was handed — the source is the one that is not the
    # file under test.
    source = str(plan.source.path)

    def differing(path, start, count, rate):
        digest = "as-copied" if str(path) == source else "re-encoded"
        return [f"0:{start + i}:{digest}" for i in range(count)]

    monkeypatch.setattr(verifier.media_module, "frame_checksums", differing)
    result = verifier.verify(plan, output)
    assert row(result, "picture copied").status == "failed"
    assert not result.ok
    assert "re-encoded, or the wrong stream was mapped" in row(result, "picture copied").detail


def test_frames_are_matched_by_presentation_time_and_not_by_position(measured, output, monkeypatch):
    """An input seek lands in a different place in two files that share a picture but not a keyframe
    layout, so comparing the decodes position by position reports a copy as broken.

    This is the defect the check had first: on the reference clip, 4/11 matched and the mismatch was
    the seek.
    """
    plan = a_plan(a_video())
    source = str(plan.source.path)

    def by_time(path, start, count, rate):
        # The output's decode begins two frames *later* on the timeline, so the same material appears at
        # a presentation time two higher. Both files therefore agree about a frame — the hash is that
        # frame's — and disagree about which frame a window began on, which is exactly the seek. A
        # version that compared the two lists by position would pair every frame with its neighbour.
        offset = 0 if str(path) == source else 2
        return [f"0:{start + i + offset}:frame{start + i + offset}" for i in range(count)]

    monkeypatch.setattr(verifier.media_module, "frame_checksums", by_time)
    result = verifier.verify(plan, output)
    check = row(result, "picture copied")
    assert check.status == "passed", check.detail
    # Position by position the two lists are shifted against each other at every window, which is what
    # the check had wrong first: it compared them by index and reported 4 of 11 frames as differing on
    # a copy that was bit-exact.
    compared = int(check.detail.split("/")[0])
    assert compared > 0, "some frames had a presentation time in common"


# ---------------------------------------------------------------------------------------
# The container, which is the check that would have caught AAC-in-WAV
# ---------------------------------------------------------------------------------------


def test_the_container_is_checked_against_the_muxer_that_was_asked_for(measured, output):
    """ffmpeg's WAV muxer accepts AAC, writes a valid WAV header, and stores ADTS frames in the data
    chunk. `ffprobe` reports `format_name: wav`, one `aac` stream, the right duration — and the first
    decode fails. Nothing in a duration or an exit code catches it."""
    measured["format"] = "wav"
    result = verifier.verify(a_plan(), output)
    assert row(result, "container").status == "failed"
    assert "plays as nothing" in row(result, "container").detail


def test_a_container_the_muxer_really_writes_passes(measured, output):
    measured["format"] = "mov,mp4,m4a,3gp,3g2,mj2"
    assert row(verifier.verify(a_plan(), output), "container").status == "passed"


def test_a_sound_that_will_not_decode_is_a_failure(measured, output):
    measured["decode_ok"] = False
    result = verifier.verify(a_plan(), output)
    check = row(result, "the sound decodes")
    assert check.status == "failed"
    assert "decode_band_types" in check.detail
    assert not result.ok


# ---------------------------------------------------------------------------------------
# Length, and the shape of the streams
# ---------------------------------------------------------------------------------------


def test_the_sound_and_the_picture_must_end_together(measured, output):
    measured["picture_times"] = [i / 25 for i in range(150)]
    result = verifier.verify(a_plan(a_video()), output)
    assert row(result, "sound and picture end together").status == "passed"

    measured["sound_seconds"] = 7.5
    result = verifier.verify(a_plan(a_video()), output)
    assert row(result, "sound and picture end together").status == "failed"


def test_the_sound_keeps_the_source_s_rate_and_channels(measured, output):
    result = verifier.verify(a_plan(), output)
    assert row(result, "sound shape").status == "passed"

    def wrong_rate(path):
        return prober.MediaInfo(
            path=Path(path),
            container="mov,mp4",
            duration=6.0,
            size_bytes=1,
            audio=prober.AudioStream(codec="aac", sample_rate=44100, channels=2),
            video=None,
        )

    assert verifier._stream_check(a_plan(), output, wrong_rate(output))[1].status == "failed"


def test_a_file_that_was_never_written_is_not_a_verdict(tmp_path):
    with pytest.raises(verifier.FFmpegError, match="nothing to verify"):
        verifier.verify(a_plan(), tmp_path / "absent.m4a")


def test_the_report_says_every_row(measured, output):
    measured["picture_times"] = [i / 25 for i in range(150)]
    text = verifier.verify(a_plan(), output).report()
    assert "target level" in text
    assert "ok  " in text, "a pass is marked as one"
