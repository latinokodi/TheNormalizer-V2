"""Measuring what was written.

The product makes two claims at once, and they are measured differently because they are different
kinds of claim.

**The picture was copied.** A claim like that is worth exactly what the measurement behind it is worth,
so this module does not check that the run *said* ``-c:v copy``. It checks that the frames in the output
are the frames that were in the source — which is a thing a stream copy guarantees and a re-encode
cannot fake. One decode of each file, over a sampled window, with the checksums compared.

**The sound is at the target.** This one is a *number*, and the number is the point of the product, so
it is measured rather than assumed: the output is decoded, its peak is read, and the difference from
the target is reported. A normalizer that reports "normalized" without saying to what has told the
operator nothing they can act on.

## Three verdicts, and they must not look alike

A check is ``passed``, ``failed``, or ``not_checked``. The third is not a soft failure and not a pass:
it is the honest answer for a check that could not be run — a file with no picture has no picture to
compare, and a sound that was *deliberately* processed has no copied samples to compare against its
source. Collapsing ``not_checked`` into ``passed`` is how a report stops meaning anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import media as media_module
from . import process as process_module
from .normalize import MUXER_FORMATS, PEAK_TOLERANCE_DB, NormalizePlan
from .process import FFmpegError, measure_levels, probe_json, tool

#: How many frames to compare per sample window. Small, because a mismatch shows up in the first few
#: frames of a run and a checksum of decoded 1080p is not free.
WINDOW = 4

#: How far the sound and the picture may differ in length before a seam is being held. The sound is
#: re-encoded and the picture is not, so the two are independent streams that were cut to the same
#: duration; a mismatch larger than a frame is a stream that was re-timed.
LENGTH_TOLERANCE_SECONDS = 0.1


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "passed" | "failed" | "not_checked"
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True)
class VerifyResult:
    output: Path
    #: What the finished file's sound actually peaks at, in dBFS. `None` is digital silence.
    output_peak_dbfs: float | None
    output_mean_dbfs: float | None
    #: The difference from the target, in dB. `None` when the source was silent and no gain was asked
    #: for — there is no target to miss when the answer is "unchanged".
    peak_error_db: float | None
    #: How much the level actually moved between the source and the result, in dB.
    applied_gain_db: float | None
    picture_seconds: float
    sound_seconds: float
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        """True when nothing failed.

        ``not_checked`` does not make this false, and does not make it true either — it is reported
        separately so a reader can see what was not established.
        """
        return all(check.status != "failed" for check in self.checks)

    @property
    def unchecked(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status == "not_checked")

    def report(self) -> str:
        peak = "silence" if self.output_peak_dbfs is None else f"{self.output_peak_dbfs:.2f} dBFS"
        lines = [
            f"{self.output.name}: {peak}, {self.sound_seconds:.3f}s of sound"
            + (f", {self.picture_seconds:.3f}s of picture" if self.picture_seconds > 0 else "")
        ]
        for check in self.checks:
            mark = {"passed": "ok  ", "failed": "FAIL", "not_checked": "----"}[check.status]
            lines.append(f"  {mark} {check.name}: {check.detail}")
        return "\n".join(lines)


def _sample_windows(frames: int) -> list[int]:
    """Where to compare frames: the start, the middle, and the end.

    Chosen rather than random, because a random sample cannot be re-run: an operator who reads
    "frames 40, 200 and 359 differ" can look at frame 200, and one who reads "3 of 12 samples differ"
    cannot look at anything.
    """
    starts = [0]
    if frames > 2 * WINDOW:
        starts.append(max(0, frames // 2 - WINDOW // 2))
        starts.append(max(0, frames - WINDOW))
    return sorted(set(starts))


def _picture_check(plan: NormalizePlan, output: Path) -> Check:
    """Compare the output's frames against the source's, over sampled windows.

    ## Why the frames are matched by presentation time and not by position

    An input seek lands in a different place in two files that share a picture but not a keyframe
    layout, so the two decodes are *lists of the same frames starting at different offsets*. Comparing
    them position by position therefore reports a copy as broken while the frames in it are identical —
    which is exactly what this check did first, on the reference clip: 4/11 matched and the mismatch was
    the seek, not the copy. `media.frame_checksums` carries each frame's presentation time out with its
    hash, and this matches on that.
    """
    source = plan.source
    if source.video is None:
        return Check(
            name="picture copied",
            status="not_checked",
            detail="this file has no picture, so there is none to copy and none to compare.",
        )

    rate = source.video.rate
    frames = source.video.frames
    if frames <= 0 or rate <= 0:
        return Check(
            name="picture copied",
            status="not_checked",
            detail="the source does not declare a frame count or a rate, so no frame could be chosen.",
        )

    identical = 0
    total = 0
    first_bad: str | None = None
    for start in _sample_windows(frames):
        count = min(WINDOW, frames - start)
        if count <= 0:
            continue
        theirs = media_module.frame_checksums(source.path, start, count, rate)
        ours = media_module.frame_checksums(output, start, count, rate)
        if not theirs or not ours and theirs:
            # One of the two could not be read at this point. Not a failure — the window is a sample
            # and the next one may read fine — but it must not count towards the total either.
            continue
        # Matched on "timeline:pts", which is the same integer in both files for the same frame.
        by_time = {row.rsplit(":", 1)[0]: row.rsplit(":", 1)[1] for row in ours}
        for row in theirs:
            key, digest = row.rsplit(":", 1)
            other = by_time.get(key)
            if other is None:
                continue
            total += 1
            if other == digest:
                identical += 1
            elif first_bad is None:
                first_bad = f"the frame at pts {key.split(':', 1)[1]} differs"

    if total == 0:
        return Check(
            name="picture copied",
            status="not_checked",
            detail=(
                "no presentation time was common to both decodes, so no frame could be compared. The "
                "two files do not appear to share a timeline."
            ),
        )
    if identical == total:
        return Check(
            name="picture copied",
            status="passed",
            detail=f"{identical}/{total} sampled frames are bit-identical to the source.",
        )
    return Check(
        name="picture copied",
        status="failed",
        detail=(
            f"only {identical}/{total} sampled frames match the source ({first_bad}). The picture was "
            f"re-encoded, or the wrong stream was mapped."
        ),
    )


def _stream_check(plan: NormalizePlan, output: Path, written: media_module.MediaInfo) -> list[Check]:
    """The shape of the output: what streams it has, at what rate and in how many channels."""
    expected = plan.source
    checks: list[Check] = []

    if expected.has_picture:
        if written.video is None:
            checks.append(
                Check(
                    name="picture present",
                    status="failed",
                    detail="the source has a picture and the output does not.",
                )
            )
        else:
            same = (written.video.width, written.video.height) == (
                expected.video.width,
                expected.video.height,
            )
            checks.append(
                Check(
                    name="picture present",
                    status="passed" if same else "failed",
                    detail=(
                        f"{written.video.width}x{written.video.height} {written.video.codec}"
                        + ("" if same else f", which is not {expected.video.width}x{expected.video.height}")
                    ),
                )
            )
    else:
        checks.append(
            Check(
                name="picture present",
                status="not_checked",
                detail="this file has no picture, so the output is sound only.",
            )
        )

    if written.audio is None:
        checks.append(
            Check(
                name="sound present",
                status="failed",
                detail="the output has no audio stream at all.",
            )
        )
        return checks

    assert expected.audio is not None  # plan_normalize refuses a source with no sound
    rate_ok = written.audio.sample_rate == expected.audio.sample_rate
    channels_ok = written.audio.channels == expected.audio.channels
    checks.append(
        Check(
            name="sound shape",
            status="passed" if (rate_ok and channels_ok) else "failed",
            detail=(
                f"{written.audio.codec} {written.audio.sample_rate} Hz, {written.audio.channels}ch — "
                f"the source's own {expected.audio.sample_rate} Hz, {expected.audio.channels}ch"
                if (rate_ok and channels_ok)
                else f"{written.audio.describe()}, and the source is {expected.audio.describe()}"
            ),
        )
    )
    return checks


def _container_check(plan: NormalizePlan, output: Path) -> Check:
    """Is the file on disk the container the run asked for?

    ## The defect this exists to catch

    ffmpeg chooses its muxer from the output's extension, and it will accept a codec the container has
    no framing for. Measured on this machine: a `.wav` output holding AAC produces a **valid WAV
    header** around ADTS frames, `ffprobe` reports `format_name: wav` with one `aac` stream and the
    right duration, the muxer exits 0, and the first decode fails with `decode_band_types: Input buffer
    exhausted before END element found`. Nothing in a duration, an exit code or a stream listing
    catches that; the file is simply unplayable.

    So the muxer is now named in the command, and this asks the finished file what it actually is. It
    is the one check that reads a *container* rather than a stream, and it is here because a
    normalizer's whole output is a file somebody has to be able to open.
    """
    result = probe_json([
        tool("ffprobe"), "-v", "error", "-show_entries", "format=format_name", "-of", "json", str(output),
    ])
    name = str(result.get("format", {}).get("format_name") or "?")
    accepted = MUXER_FORMATS.get(plan.muxer, ())
    if not accepted:  # a muxer this module does not know: claim nothing rather than guess
        return Check(
            name="container",
            status="not_checked",
            detail=f"{name} — this build has no listing for the '{plan.muxer}' muxer to compare against.",
        )
    written = {part.strip() for part in name.split(",")}
    if written & set(accepted):
        return Check(name="container", status="passed", detail=f"{name}, written by the {plan.muxer} muxer")
    return Check(
        name="container",
        status="failed",
        detail=(
            f"the file is '{name}' but it was asked for as {plan.container_label} ('{plan.container}'). "
            f"A container that does not match its extension probes correctly and plays as nothing."
        ),
    )


def _sound_decodes(plan: NormalizePlan, output: Path) -> Check:
    """Does the sound in the output actually decode?

    The peak measurement has already decoded every sample, so a file whose decode fails would have
    failed `target level` first — this is the check that says *why* in one line, and it is what makes
    the failure legible when it is a container problem rather than a level problem.

    It is also the one check whose answer a reader might otherwise take for granted. "The file was
    written and it has a sound" and "the sound in it can be played" are different claims, and the
    difference is exactly the AAC-in-WAV case.
    """
    args = [
        tool("ffmpeg"), "-v", "error", "-i", str(output),
        "-map", "0:a:0", "-f", "null", "-",
    ]
    code, _, err = process_module.capture(args, timeout=1800.0)
    if code == 0:
        return Check(
            name="the sound decodes",
            status="passed",
            detail=f"every sample of {output.name} decoded without an error",
        )
    return Check(
        name="the sound decodes",
        status="failed",
        detail="ffmpeg could not decode the sound it just wrote: " + (err.strip().splitlines() or [""])[-1],
    )


def verify(plan: NormalizePlan, output: Path) -> VerifyResult:
    """Measure the file that was written against the plan that produced it."""
    if not output.is_file():
        raise FFmpegError(f"{output} was not written, so there is nothing to verify")

    written = media_module.probe(output)
    measured = measure_levels(output)

    checks: list[Check] = []

    checks.append(_container_check(plan, output))
    checks.append(_sound_decodes(plan, output))

    # ---- What the sound is, which is the point of the product ------------------------------
    expected_peak = plan.expected_peak_dbfs
    if plan.source_peak_dbfs is None:
        # The source was digital silence and no gain was asked for, so there is no target to reach.
        # The honest check is that the output is *also* silent; anything else means a filter put
        # something into the file that was not there.
        if measured.max_dbfs is None:
            checks.append(
                Check(
                    name="target level",
                    status="passed",
                    detail="the source is digital silence and so is the output; no gain was applied.",
                )
            )
        else:
            checks.append(
                Check(
                    name="target level",
                    status="failed",
                    detail=(
                        f"the source is digital silence but the output peaks at "
                        f"{measured.max_dbfs:.2f} dBFS. Something added signal that was not there."
                    ),
                )
            )
        peak_error = None
    elif measured.max_dbfs is None:
        checks.append(
            Check(
                name="target level",
                status="failed",
                detail=(
                    f"the source peaks at {plan.source_peak_dbfs:.2f} dBFS and the output is digital "
                    f"silence. The sound was lost."
                ),
            )
        )
        peak_error = None
    else:
        peak_error = round(measured.max_dbfs - expected_peak, 4)
        if abs(peak_error) <= PEAK_TOLERANCE_DB:
            checks.append(
                Check(
                    name="target level",
                    status="passed",
                    detail=(
                        f"{measured.max_dbfs:.2f} dBFS against the {expected_peak:.1f} dBFS target "
                        f"({peak_error:+.2f} dB)."
                    ),
                )
            )
        else:
            checks.append(
                Check(
                    name="target level",
                    status="failed",
                    detail=(
                        f"{measured.max_dbfs:.2f} dBFS, which is {peak_error:+.2f} dB from the "
                        f"{expected_peak:.1f} dBFS target — more than the {PEAK_TOLERANCE_DB:.1f} dB "
                        f"this is allowed to be out by."
                    ),
                )
            )

    applied: float | None = None
    if plan.source_peak_dbfs is not None and measured.max_dbfs is not None:
        applied = round(measured.max_dbfs - plan.source_peak_dbfs, 4)
        # ## What this row compares, and what it deliberately does not
        #
        # `applied` is the whole run's effect — the chain *and* the fader — because that is the only
        # thing the two measurements it comes from can prove. The plan holds two separate figures for a
        # reason: `measured_chain_gain_db` is what the operator's chain did to this material, and
        # `gain_db` is what the fader did on top of it. Comparing the whole effect against either one of
        # them would be comparing a sum to one of its terms, and this row did exactly that first: a
        # chain run reported "+31.20 dB, against the plan's +19.50" and looked like a failure when it
        # was the two halves of a correct answer. So the row shows the split, and the argument it
        # checks is that the two halves add up.
        chain = plan.measured_chain_gain_db
        parts = f"{plan.source_peak_dbfs:.2f} → {measured.max_dbfs:.2f} dBFS ({applied:+.2f} dB)"
        if chain is not None:
            # ## What this row can and cannot check on a `chain` run
            #
            # The whole effect from the source is `chain + fader` measured *in different places*: the
            # chain moves the source, and the fader moves the chain's output. The two do not add in
            # decibels unless the chain is linear, and this chain's limiter is not — so this row does
            # not attempt arithmetic across the two. It reports both and checks the one claim that is
            # checkable from here and is the one that matters: the level the fader was given a target
            # for, and the level that came out, are the same.
            #
            # The alternative — comparing the whole effect against the fader's value — is what this row
            # did first, and it reported a correct run as a failure: "+31.20 dB against the plan's
            # +19.50".
            detail = (
                f"{parts}; the chain moved it {chain:+.2f} dB and the fader {plan.gain_db:+.2f} dB, "
                f"and the result is {abs(measured.max_dbfs - plan.spec.target_dbfs):.2f} dB from the "
                f"{plan.spec.target_dbfs:.1f} dBFS target"
            )
            agrees = abs(measured.max_dbfs - plan.spec.target_dbfs) <= PEAK_TOLERANCE_DB
        else:
            agrees = abs(applied - plan.gain_db) <= PEAK_TOLERANCE_DB
            detail = f"{parts}, against the plan's {plan.gain_db:+.2f} dB"
        checks.append(
            Check(name="level moved", status="passed" if agrees else "failed", detail=detail)
        )

    checks.extend(_stream_check(plan, output, written))

    # ---- Length: the sound and the picture end together ------------------------------------
    sound_seconds = media_module.stream_duration(output, "a:0") or written.duration
    picture_seconds = 0.0
    if written.video is not None:
        times = media_module.packet_times(output)
        one_frame = 1.0 / float(written.video.rate) if written.video.rate > 0 else 0.0
        picture_seconds = (max(times) + one_frame) if times else written.duration
        drift = abs(picture_seconds - plan.duration)
        checks.append(
            Check(
                name="length kept",
                status="passed" if drift <= max(one_frame, LENGTH_TOLERANCE_SECONDS) else "failed",
                detail=(
                    f"{picture_seconds:.3f}s of picture against the source's {plan.duration:.3f}s "
                    f"({drift * 1000:.0f}ms)"
                ),
            )
        )
        if sound_seconds > picture_seconds + LENGTH_TOLERANCE_SECONDS:
            checks.append(
                Check(
                    name="sound and picture end together",
                    status="failed",
                    detail=(
                        f"{sound_seconds:.3f}s of sound under {picture_seconds:.3f}s of picture — the "
                        f"sound runs {(sound_seconds - picture_seconds) * 1000:.0f}ms past the end"
                    ),
                )
            )
        else:
            checks.append(
                Check(
                    name="sound and picture end together",
                    status="passed",
                    detail=(
                        f"{sound_seconds:.3f}s of sound against {picture_seconds:.3f}s of picture "
                        f"({(picture_seconds - sound_seconds) * 1000:+.0f}ms)"
                    ),
                )
            )
    else:
        checks.append(
            Check(
                name="length kept",
                status=(
                    "passed"
                    if abs(sound_seconds - plan.duration) <= LENGTH_TOLERANCE_SECONDS
                    else "failed"
                ),
                detail=f"{sound_seconds:.3f}s of sound against the source's {plan.duration:.3f}s",
            )
        )

    checks.append(_picture_check(plan, output))

    return VerifyResult(
        output=output,
        output_peak_dbfs=measured.max_dbfs,
        output_mean_dbfs=measured.mean_dbfs,
        peak_error_db=peak_error,
        applied_gain_db=applied,
        picture_seconds=picture_seconds,
        sound_seconds=sound_seconds,
        checks=tuple(checks),
    )
