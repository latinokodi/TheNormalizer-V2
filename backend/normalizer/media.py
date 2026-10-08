"""What a file is.

Probing, and the measurements that read a result back: the streams a container holds, the level of its
sound, its length, and the checksums that prove a stream was copied rather than re-encoded.

## Why the level is measured and not asked about

No container carries a peak. There is no header field, no metadata key and no ``ffprobe`` entry that
says how loud a file is — a container describes how the samples are *stored*, and loudness is a
property of the samples. So the only honest answer comes from decoding the sound and looking at it,
which is what ``process.measure_levels`` does with ``volumedetect``, and what makes the gain this
product applies a measurement rather than a guess.

## Why ``volumedetect`` and not a loudness meter

An EBU R128 measurement (``ebur128``, ``loudnorm``) reports **LUFS**: loudness as a listener perceives
it, gated and frequency-weighted. It is the better measure of *annoyance*, and it is what a streaming
platform asks for. It is not what this product uses, for two reasons that are worth writing down:

* it is not in every ffmpeg — the ``essentials`` build that ships with most Windows machines has
  ``volumedetect`` and neither ``loudnorm`` nor ``ebur128``, measured on this machine's
  ``ffmpeg 8.0.1-essentials_build``;
* and the level this product produces is the level its sibling produces. ``TheStitcher`` normalizes by
  an operator's Premiere dynamics chain — a flat +12 dB into a −6 dBFS limiter — and a file normalized
  here has to sit beside a stitched episode without one of them sounding wrong.

``/api/health`` reports whether this machine's ffmpeg has ``loudnorm``, so a future loudness target is
a feature that can be offered where it exists rather than a promise that fails on most machines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .process import FFmpegError, capture, measure_levels, measure_loudness, probe_json, tool

#: A frame is black when its mean luma is at or below this. Video black is 16 in limited range and 0
#: in full range; 20 admits both and still excludes a fade, which is at 30 and climbing by the time it
#: is a frame rather than a field.
BLACK_LUMA = 20.0


@dataclass(frozen=True)
class AudioStream:
    """The sound: what it is stored as, at what rate, in how many channels."""

    codec: str
    sample_rate: int
    channels: int
    bit_rate: int = 0

    def describe(self) -> str:
        return f"{self.codec} {self.sample_rate} Hz, {self.channels}ch"


@dataclass(frozen=True)
class VideoStream:
    """The picture, as much of it as this product is allowed to know.

    It is deliberately small. Nothing here is scaled, cropped or re-timed, so the profile, the level
    and the B-frame depth are recorded and never acted on — they are what a refusal or a report can
    cite when a copy turns out not to be possible.
    """

    codec: str
    profile: str
    level: int
    width: int
    height: int
    pix_fmt: str
    rate: Fraction
    frames: int
    timebase: Fraction
    has_b_frames: bool

    @property
    def rate_text(self) -> str:
        return f"{float(self.rate):g}"


@dataclass(frozen=True)
class MediaInfo:
    """Everything the planner and the verifier are allowed to know about a file.

    One type for both kinds of source, because the product takes both kinds: a video file is a file
    with a picture and a sound, an audio file is a file with a sound. ``video`` being ``None`` is
    therefore a *fact about the file* and not an error — it is what decides whether the output keeps a
    picture, and it is the first thing the plan states.
    """

    path: Path
    container: str
    duration: float
    size_bytes: int
    audio: AudioStream | None
    video: VideoStream | None
    #: Every codec in the container, by stream type, as ffprobe reports them. Used for one thing: the
    #: refusal that names what is in a file the product cannot put back into the same kind of
    #: container after re-encoding the sound.
    streams: tuple[tuple[str, str], ...] = ()

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def has_picture(self) -> bool:
        return self.video is not None

    @property
    def kind(self) -> str:
        """``"video"`` or ``"audio"``. The word the window uses for the file, decided by the file."""
        return "video" if self.video is not None else "audio"

    @property
    def frames(self) -> int:
        return 0 if self.video is None else self.video.frames

    @property
    def rate(self) -> Fraction:
        return Fraction(0) if self.video is None else self.video.rate

    def summary(self) -> str:
        audio = "no audio" if self.audio is None else self.audio.describe()
        if self.video is None:
            return f"{self.container}, {self.duration:.3f}s of sound, {audio}"
        return (
            f"{self.video.width}x{self.video.height} {self.video.codec} {self.video.profile} at "
            f"{self.video.rate_text} fps, {self.video.frames} frames, {self.duration:.3f}s, {audio}"
        )


def _fraction(value: str | None) -> Fraction:
    """``"30/1"`` or ``"30000/1001"`` as an exact rational. Never a float: 29.97 is not 29.97."""
    if not value:
        return Fraction(0)
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _pick(streams: list[dict], kind: str) -> dict | None:
    for stream in streams:
        if stream.get("codec_type") == kind:
            return stream
    return None


def probe(path: Path) -> MediaInfo:
    """Read a file's streams. The one place a source is measured.

    A file with no audio is refused here, and it is the only refusal this function makes. There is
    nothing to normalize in a silent video, and finding that out at the planning stage rather than at
    the end of a decode is the difference between a sentence and a wasted minute.
    """
    result = probe_json([
        tool("ffprobe"), "-v", "error",
        "-show_streams", "-show_format", "-of", "json", str(path),
    ])
    streams = result.get("streams", [])
    video_stream = _pick(streams, "video")
    audio_stream = _pick(streams, "audio")

    if audio_stream is None:
        raise FFmpegError(
            f"{path.name} has no audio stream, so there is nothing to normalize. This product changes "
            f"the sound and copies the picture; a file with no sound has neither."
        )

    container = str(result.get("format", {}).get("format_name") or "?")
    duration = float(result.get("format", {}).get("duration") or 0.0)
    size_bytes = int(result.get("format", {}).get("size") or 0)

    video: VideoStream | None = None
    if video_stream is not None:
        rate = _fraction(video_stream.get("r_frame_rate")) or _fraction(
            video_stream.get("avg_frame_rate")
        )
        frames_raw = video_stream.get("nb_frames")
        frames = int(frames_raw) if frames_raw and str(frames_raw).isdigit() else 0
        if frames <= 0 and duration > 0 and rate > 0:
            # A file whose header omits the count still has a duration, and duration on the rate grid
            # is the count. It is a derived number, and the verifier re-measures with `count_frames`.
            frames = int(round(duration * float(rate)))
        video = VideoStream(
            codec=str(video_stream.get("codec_name") or "?"),
            profile=str(video_stream.get("profile") or "?"),
            level=int(video_stream.get("level") or 0),
            width=int(video_stream.get("width") or 0),
            height=int(video_stream.get("height") or 0),
            pix_fmt=str(video_stream.get("pix_fmt") or "?"),
            rate=rate,
            frames=frames,
            timebase=_fraction(video_stream.get("time_base")),
            has_b_frames=int(video_stream.get("has_b_frames") or 0) > 0,
        )
        if duration <= 0:
            duration = float(video_stream.get("duration") or 0.0)

    audio = AudioStream(
        codec=str(audio_stream.get("codec_name") or "?"),
        sample_rate=int(audio_stream.get("sample_rate") or 0),
        channels=int(audio_stream.get("channels") or 0),
        bit_rate=int(audio_stream.get("bit_rate") or 0),
    )
    if audio.sample_rate <= 0:
        raise FFmpegError(f"{path.name} does not declare an audio sample rate")

    return MediaInfo(
        path=path,
        container=container,
        duration=duration,
        size_bytes=size_bytes,
        audio=audio,
        video=video,
        streams=tuple(
            (str(stream.get("codec_type") or "?"), str(stream.get("codec_name") or "?"))
            for stream in streams
        ),
    )


def loudness(info: MediaInfo, timeout: float = 1800.0):
    """``ebur128``'s figures for this file: how loud it sounds, and how wide its dynamics are.

    A thin pass-through to ``process.measure_loudness``, like ``levels`` is to ``measure_levels``, so that a
    caller does not have to hold two modules to ask one question.

    ## Why this is a second decode rather than part of the first

    It is the honest cost. ``volumedetect`` reports the two figures the *arithmetic* runs on — the source
    peak decides the gain and the output peak proves the target was reached — and ``ebur128`` reports the two
    figures a *person* cares about. No one filter reports all four, and swapping the peak measurement for a
    loudness one would change what every number in this program means.

    So a file with a sound is decoded twice when it is added: once for the peak, once for the loudness. On
    the reference 17-minute episode that is about eight seconds each, and it buys the sentence that explains
    a file's own dynamics — which is the thing an operator asked for by asking why the quiet parts are quiet.
    """
    return measure_loudness(info.path, timeout=timeout)


def levels(info: MediaInfo, timeout: float = 1800.0):
    """``volumedetect``'s two figures for this file, by decoding its sound.

    A thin pass-through to ``process.measure_levels`` so that a caller does not have to hold two
    modules to ask one question, and so that the *only* place a level is measured is one call.
    """
    return measure_levels(info.path, timeout=timeout)


# ---------------------------------------------------------------------------------------
# Reading a result back
# ---------------------------------------------------------------------------------------

def count_frames(path: Path) -> int:
    """The frames actually in the file, counted rather than trusted.

    ``nb_frames`` in the header is a field a muxer writes and a muxer can be wrong about; this decodes
    the index. It is the measurement ``verified`` rests on, so it is the one that does not take the
    container's word for anything.
    """
    result = probe_json([
        tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "json", str(path),
    ], timeout=600.0)
    streams = result.get("streams", [])
    raw = streams[0].get("nb_read_frames") if streams else None
    return int(raw) if raw and str(raw).isdigit() else 0


def stream_duration(path: Path, stream: str = "a:0") -> float:
    """A stream's own duration, in seconds. ``0.0`` when the container does not carry one."""
    result = probe_json([
        tool("ffprobe"), "-v", "error", "-select_streams", stream,
        "-show_entries", "stream=duration", "-of", "json", str(path),
    ])
    streams = result.get("streams", [])
    raw = streams[0].get("duration") if streams else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def audio_frames(path: Path, sample_rate: int) -> int:
    """How many audio samples the file holds, counted from its packets.

    This is the sound's length in the one form that is exact, and it is what the output is cut to: the
    container's ``duration`` field is written from the last packet in *file* order, which is decode
    order, so an encoder with any lookahead writes a number that is a few hundred samples short of the
    truth (``docs/DESIGN.md`` §2, and the same field that failed a correct join in the sibling
    product). The packet timestamps have no such problem.
    """
    result = probe_json([
        tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "packet=pts_time,duration_time", "-of", "json", str(path),
    ], timeout=600.0)
    best = 0.0
    for packet in result.get("packets", []):
        try:
            start = float(packet.get("pts_time") or 0.0)
            length = float(packet.get("duration_time") or 0.0)
        except (TypeError, ValueError):
            continue
        best = max(best, start + length)
    if best <= 0:
        return 0
    return int(round(best * sample_rate))


def packet_times(path: Path) -> list[float]:
    """Every video packet's presentation time, in seconds, ascending.

    Not the container's ``duration`` field. That field is written from the **last packet in file
    order**, and file order is decode order — so with B-frames the last packet decoded is not the last
    frame shown, and the field is short by the reorder depth. Measured on the sibling product's
    reference join: `1041.666567 s` against a true `1041.733233 s`, exactly 2.00 frames and exactly
    that file's `has_b_frames`. A verifier that compares frames-over-rate against it fails a file that
    is correct.
    """
    args = [
        tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path),
    ]
    _, out, _ = capture(args, timeout=900.0)
    times: list[float] = []
    for line in out.splitlines():
        text = line.strip().rstrip(",")
        if not text:
            continue
        try:
            times.append(float(text))
        except ValueError:
            continue
    return sorted(times)


def frame_checksums(path: Path, start: int, count: int, rate: Fraction) -> list[str]:
    """Checksums of ``count`` decoded frames beginning at ``start``, as ``"pts:hash"``.

    This is what makes "the picture was copied" a claim rather than a setting. A stream copy reproduces
    the source's compressed packets exactly, so the decoded frames must be *identical* to the
    source's — and if they are not, something re-encoded, or the decoder was handed the wrong
    parameter sets. Both are worth knowing about, and neither is visible in a duration.

    ## Why the presentation time travels with the checksum

    An input seek is *not* frame-exact across two files: ffmpeg seeks to the keyframe at or before the
    requested time and discards forward, and two files with the same frames but a different keyframe
    layout therefore land on different frames. Measured on the reference clip: seeking both the source
    and its stream-copied output to 2.000 s gave the source frames at pts 1 and 2 and the output frames
    at pts 0 and 1 — so comparing the lists **by position** reported that the copy had failed, when the
    frame at pts 1 was in fact bit-identical in both.

    A frame's presentation time is what it *is*; its position in a seeked decode is an accident of where
    the decoder landed. So the time is carried out with the hash and the comparison matches on it, which
    makes the check exact rather than approximately aligned.

    ## Why this seeks instead of trimming from the beginning

    ``trim=start_frame=N`` decodes from frame 0 to reach frame N, which on a long master is a full
    decode of every frame before it — measured at 258 seconds on the sibling product's reference join,
    where almost all of its verification time went. Seeking to ``start / rate`` as an *input* option is
    frame-accurate *for one file* and lands within a frame or two for another, which is what the
    timestamp matching above is for.

    ``rate`` is required rather than looked up: the caller already has it, and probing the same file
    again inside a sampling loop would undo the saving.
    """
    args = [tool("ffmpeg"), "-v", "error"]
    if start > 0:
        args += ["-ss", f"{float(Fraction(start, 1) / rate):.6f}"]
    args += [
        "-i", str(path), "-map", "0:v:0",
        "-frames:v", str(count), "-f", "framemd5", "-",
    ]
    _, out, _ = capture(args, timeout=900.0)
    return _parse_framemd5(out)


def _parse_framemd5(text: str) -> list[str]:
    """`framemd5`'s output as ``"<timeline>:<pts>:<hash>"`` lines.

    The pts column is in the stream's own timebase, so it is an integer on both files and can be
    compared without a division — which is the point: two files that share a picture share this number
    exactly, whatever either of them calls a second. A `dts` of `-1` on the first frame of a stream is
    dropped by using `pts`, which is never negative for a frame that is shown.

    ## Why the guard is six and not seven

    A data row is ``stream, dts, pts, duration, size, hash`` — **six** fields. The first version of this
    function required seven, on the reasoning that a row has a stream index, two timestamps, a duration,
    a size and a hash, and did not count them. The effect was that every row was dropped, every
    comparison had nothing to compare, and the frame check reported *"no presentation time was common to
    both decodes"* — a sentence that reads like a container problem and was a miscounted list. It is
    written down because the failure is silent: an empty parse and a genuine mismatch produce the same
    verdict.
    """
    rows: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 6:
            continue
        digest = fields[-1]
        if not digest:
            continue
        rows.append(f"{fields[0]}:{fields[2]}:{digest}")
    return rows


def audio_checksums(path: Path, start: float, count: int) -> list[str]:
    """Checksums of ``count`` decoded audio frames beginning at ``start`` seconds.

    The sound's counterpart to ``frame_checksums``, and it answers the same question one layer down:
    when the dynamics chain is **off**, the sound is stream-copied, and a copy that was really a copy
    must decode to the same samples as its source. It is never run when the chain is on, because a
    processed signal is supposed to differ — comparing it would report a difference that was chosen.
    """
    args = [tool("ffmpeg"), "-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.6f}"]
    args += [
        "-i", str(path), "-map", "0:a:0",
        "-frames:a", str(count), "-f", "framemd5", "-",
    ]
    _, out, _ = capture(args, timeout=900.0)
    return [
        line.rsplit(",", 1)[-1].strip()
        for line in out.splitlines()
        if line.strip() and not line.startswith("#")
    ]


# ---------------------------------------------------------------------------------------
# Black frames
# ---------------------------------------------------------------------------------------

_LUMA = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def _luma(path: Path, count: int, seek: list[str]) -> list[float]:
    """Mean luma for ``count`` frames, converted from whatever ffmpeg's log says.

    ``seek`` is an *input* option list (``-sseof`` for the tail) and is placed before ``-i`` because
    that is where an input option belongs. Putting ``-map`` there instead — which is what the sibling
    product did first — is not a seek at all: ``-map`` is an output option, ffmpeg rejects the command,
    and the scan returns no frames so the black count comes back as **zero**. A measurement that
    silently returns nothing is worse than one that fails, because zero black frames is a plausible
    answer.

    ``metadata=print`` writes to the log rather than to a file. That is deliberate: a ``file=``
    argument is a path interpolated into a filtergraph, and a filtergraph is parsed as a single string,
    so a Windows path with a colon in it is a syntax error rather than a filename.
    """
    args = [tool("ffmpeg"), "-v", "info", "-hide_banner", *seek, "-i", str(path),
            "-map", "0:v:0", "-vf", "signalstats,metadata=print",
            "-frames:v", str(count), "-f", "null", "-"]
    _, _, err = capture(args, timeout=600.0)
    return [float(match) for match in _LUMA.findall(err)]


def black_head_frames(info: MediaInfo, limit: int = 90) -> int:
    """How many frames at the start are black. Measured, not inferred from an interval.

    ``blackdetect`` reports an interval and the interval is ambiguous at its edges — the sibling
    product documents the off-by-one it produces in both directions. Reading the luma of each frame has
    no such ambiguity, and it is why a file that *begins* on black is known to begin on black.
    """
    if info.video is None or info.frames <= 0:
        return 0
    count = min(limit, info.frames)
    values = _luma(info.path, count, [])
    leading = 0
    for value in values:
        if value > BLACK_LUMA:
            break
        leading += 1
    return leading
