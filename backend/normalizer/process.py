"""Starting ffmpeg, watching it, stopping it.

The only module in the engine that spawns anything. Everything above it deals in argument lists and
callbacks, which is what makes the planning layer testable without a process and without footage.

## No shell, ever

Every command is an argument list handed to ``Popen`` directly. Nothing is assembled into a string and
handed to a shell, because a path with a space, a quote or an ``&`` in it is ordinary on Windows and
would be a command injection anywhere. This is also why an ``-i`` value is always a separate list
element and never a fragment of a filtergraph — a filtergraph is parsed as one string, so a Windows
path with a colon in it is a syntax error rather than a filename.

## Why progress is read from stdout and the log from stderr

ffmpeg reports its position on the stream named by ``-progress``, and everything it has to say about
what it is doing on stderr. Reading both from different pipes means one reader per pipe and no
interleaving, and it is the reason this module needs threads at all: two pipes that are both filling
up and only one being read is a deadlock, not a slow run.

## Why the level measurement is a parse and not a filter

``volumedetect`` prints its answer to the log rather than to a stream — ``mean_volume: -23.4 dB`` and
``max_volume: -3.9 dB`` — so a measurement of a file is a *capture* with a regular expression over its
stderr, not a ``Popen`` to be watched. It lives here rather than in ``media`` because this module owns
everything that starts a process, and a second module that spawns would be a second place for the
argument-list discipline to be forgotten.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

#: Windows: no console window. A black rectangle flashing beside the application on every pass reads
#: as a fault, and there is nothing in it a person can use.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: How often the loop wakes to look at the cancel token. The progress pipe reports far more often
#: than this while work is happening, so this only governs how quickly a *silent* ffmpeg is stopped.
POLL_SECONDS = 0.2

#: How often a silent pass says it is still alive, so a window can tell "working" from "wedged".
HEARTBEAT_SECONDS = 1.0


class FFmpegError(RuntimeError):
    """ffmpeg refused, failed, or ran past its limit.

    Carries the argument list and the tail of stderr, because those two together are the difference
    between a report someone can act on and the word "failed". The message stays readable by keeping
    the tail to twelve lines: a full ffmpeg log is hundreds, and the useful part is always the end.
    """

    def __init__(self, message: str, command: list[str] | None = None, stderr: str = "") -> None:
        self.command = list(command or [])
        self.stderr = stderr
        lines = [message]
        if self.command:
            lines.append("$ " + " ".join(self.command))
        tail = [line for line in stderr.splitlines() if line.strip()][-12:]
        lines.extend(tail)
        super().__init__("\n".join(lines))


class Cancelled(RuntimeError):
    """The operator asked for this to stop. A decision, not a failure."""


class CancelToken:
    """A flag one thread sets and another reads. The only cancellation mechanism in the engine.

    Not a task cancellation, because the work is not a task: it is a ``Popen`` on a worker thread, and
    a thread cannot be cancelled from outside. What can be done is to ask the process to stop, which
    is what this token is polled for. Cancelling an ``await`` on a thread would leave ffmpeg running
    and the output file half-written, which is worse than not cancelling at all.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class Ticks:
    """One position ffmpeg reported.

    ``expected_seconds`` is what the pass is expected to produce, supplied by the caller that built the
    command; it is carried here rather than looked up so that the fraction is a division of two
    numbers that came from the same place.
    """

    out_seconds: float
    frame: int | None
    speed: float | None
    size: int | None
    expected_seconds: float | None

    @property
    def fraction(self) -> float | None:
        if self.expected_seconds is None or self.expected_seconds <= 0:
            return None
        return min(1.0, max(0.0, self.out_seconds / self.expected_seconds))

    @property
    def remaining(self) -> float | None:
        """Seconds left, from ffmpeg's own throughput rather than from how long we have waited."""
        if self.expected_seconds is None or self.speed is None or self.speed <= 0:
            return None
        left = self.expected_seconds - self.out_seconds
        return 0.0 if left <= 0 else left / self.speed


# ---------------------------------------------------------------------------------------
# Finding the tools
# ---------------------------------------------------------------------------------------

_TOOLS: dict[str, str] = {}


def tool(name: str) -> str:
    """Where ``ffmpeg`` or ``ffprobe`` is, or a refusal that says where it looked.

    Checked in the order that respects the operator: an explicit environment variable first, so a
    machine with several builds can be pointed at one without editing anything; then ``PATH``; then the
    places a Windows build actually lands, because "install it and make sure it is on PATH" is the
    step people skip and the error it produces is a ``FileNotFoundError`` naming a temp directory.
    """
    if name in _TOOLS:
        return _TOOLS[name]

    override = os.environ.get(f"THE_NORMALIZER_{name.upper()}")
    if override and Path(override).is_file():
        _TOOLS[name] = override
        return override

    found = shutil.which(name)
    if found:
        _TOOLS[name] = found
        return found

    candidates = [
        Path(r"C:\ffmpeg\bin") / f"{name}.exe",
        Path(r"C:\Program Files\ffmpeg\bin") / f"{name}.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe",
        Path(r"C:\ProgramData\chocolatey\bin") / f"{name}.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            _TOOLS[name] = str(candidate)
            return str(candidate)

    looked = ", ".join(str(candidate) for candidate in candidates)
    raise FFmpegError(
        f"{name} was not found. It was looked for on PATH and in: {looked}. "
        f"Install ffmpeg, or set THE_NORMALIZER_{name.upper()} to the full path of {name}.exe."
    )


def version(name: str = "ffmpeg") -> str:
    return capture([tool(name), "-version"])[1].splitlines()[0]


def encoders() -> str:
    return capture([tool("ffmpeg"), "-hide_banner", "-encoders"])[1]


def filters() -> str:
    """Every filter this ffmpeg has.

    Asked once, at ``/api/health``, because ``loudnorm`` and ``ebur128`` ship in the *full* builds and
    not in the ``essentials`` ones — measured on this machine: ``ffmpeg 8.0.1-essentials_build`` has
    ``alimiter``, ``acompressor``, ``volumedetect`` and ``dynaudnorm``, and has neither ``loudnorm``
    nor ``ebur128``. A window that offered a loudness target without asking would let an operator
    choose it and find out at the last pass of a run.
    """
    return capture([tool("ffmpeg"), "-hide_banner", "-filters"])[1]


# ---------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------

def capture(args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a short command and give back everything it said. For probes, never for work."""
    finished = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )
    return finished.returncode, finished.stdout, finished.stderr


def probe_json(args: list[str], timeout: float = 120.0) -> dict:
    """``ffprobe -of json``, parsed, with a sentence when it is not JSON."""
    code, out, err = capture(args, timeout=timeout)
    if code != 0:
        raise FFmpegError("ffprobe could not read the file", args, err)
    try:
        return json.loads(out)
    except json.JSONDecodeError as failure:
        raise FFmpegError(f"ffprobe did not answer with JSON: {failure}", args, err) from failure


#: `volumedetect`'s two figures, as it prints them at the end of a decode.
#:
#: The unit is `dBFS` and the figure is **negative**, and `-inf` is a real answer for digital silence:
#: a file with no signal in it has a maximum of negative infinity, not of zero. A parser that required
#: a number would raise on exactly the case the product most needs to refuse politely, so the pattern
#: accepts `-inf` and the reader turns it into `None`.
#:
#: **The `\s*` after the colon is not decoration.** ffmpeg prints its two figures as
#: `mean_volume: -30.1 dB` — with a space, from a `%6.1f` and not from a `%.1f` — while the
#: `n_samples:` line above them has no space at all, and the histogram lines below are named by an
#: integer. A pattern of `max_volume:([\d.-]+)` therefore matches nothing on a real log, and the
#: failure it produces is this module's own sentence about ffmpeg not reporting a level — which reads
#: as a broken ffmpeg rather than as a broken regular expression. It was written that way first.
_VOLUME = re.compile(r"(mean|max)_volume:\s*(-?[\d.]+|-inf)\s*dB", re.IGNORECASE)


@dataclass(frozen=True)
class Levels:
    """What ``volumedetect`` measured over a whole stream: two figures, in dBFS.

    ``None`` means digital silence — ``volumedetect`` prints ``-inf`` for it. It is deliberately not
    ``-1000.0``: a sentinel number is a number somebody eventually does arithmetic on, and "there was
    no signal" is not a very quiet signal.
    """

    mean_dbfs: float | None
    max_dbfs: float | None
    seconds: float

    @property
    def has_signal(self) -> bool:
        return self.max_dbfs is not None


#: The level below which a sound is **silence**, and not merely quiet.
#:
#: 16-bit PCM cannot represent exact zero for a *negative* sample: the range is −32768..32767, so a
#: file of nothing but zeros is already a tiny DC offset, and a "silent" file made by a generator is
#: usually one LSB of dither as well. Measured: `anullsrc` written as 16-bit stereo reads
#: `max_volume: -91.0 dB` — which is 1 LSB in a 16-bit word, and which `volumedetect` will happily
#: report as a peak.
#:
#: That distinction matters here rather than being pedantry. Without it, "digital silence" is a case
#: this product never sees, and a silent file is instead a file 85 dB below the target — so the run
#: computes an +85 dB gain, applies it to the noise floor, and reports that as normalization. With it,
#: silence is recognized and left alone. −90 dBFS is nine orders of magnitude below full scale and is
#: below the noise floor of every microphone, converter and codec this product will ever be handed.
SILENCE_DBFS = -90.0


def _dbfs(text: str) -> float | None:
    if text.strip().lower().startswith("-inf"):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value <= SILENCE_DBFS else value


def measure_levels(path: Path, timeout: float = 1800.0) -> Levels:
    """Decode a whole stream and report its mean and peak level, in dBFS.

    This is the measurement the product is built on: the input's peak decides the gain that brings it
    to the target, and the output's peak is what proves the target was reached. It is therefore taken
    by **decoding every sample**, not by reading a header field — a container carries no peak, and the
    one thing a normalizer may not do is guess what the sound does between two packets.

    ``-vn`` so nothing here can touch the picture, and therefore so a 4K master costs the audio decode
    rather than a video one. The sample format is left alone: ``volumedetect`` reports what is there.

    The cost is one full audio decode per measurement, and there are two per run — the source before
    it and the result after it. On the reference 17-minute episode that is about **8 seconds** each,
    which is the price of a peak that is measured rather than assumed.
    """
    args = [
        tool("ffmpeg"), "-hide_banner", "-nostats", "-vn", "-i", str(path),
        "-af", "volumedetect", "-f", "null", "-",
    ]
    _, _, err = capture(args, timeout=timeout)
    # The group is `mean` or `max`, not `mean_volume` — the `_volume` suffix is part of the pattern
    # rather than of the capture, so the key has to be put back together. Reading `found["max_volume"]`
    # after this line is the bug that made every file look like it had no level at all.
    found = {f"{name.lower()}_volume": _dbfs(value) for name, value in _VOLUME.findall(err)}
    if "max_volume" not in found:
        raise FFmpegError(
            "ffmpeg did not report a level for this file, so it cannot be normalized against one",
            args,
            err,
        )
    return Levels(
        mean_dbfs=found.get("mean_volume"),
        max_dbfs=found.get("max_volume"),
        seconds=_last_time(err),
    )


#: The clock ffmpeg prints on its own progress line, `time=00:01:02.34`.
_CLOCK = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def _last_time(log: str) -> float:
    """The last position ffmpeg's log mentioned, in seconds. `0.0` when it mentioned none.

    This is the *decoded* length of the stream rather than the container's duration field, which is a
    different number for the reason ``docs/DESIGN.md`` §2 gives. It is reported and not relied on: the
    plan's arithmetic uses the probed duration, and this is a second opinion shown beside it.
    """
    last = 0.0
    for hours, minutes, rest in _CLOCK.findall(log):
        try:
            last = int(hours) * 3600 + int(minutes) * 60 + float(rest)
        except ValueError:
            continue
    return last


def _stop(process: subprocess.Popen) -> None:
    """Ask, then insist.

    ``terminate`` gives ffmpeg the chance to close its output and write a valid trailer. ``kill`` does
    not, and a killed muxer leaves a file that is not playable — which matters because the engine
    writes to a temporary name and only renames on success. The wait between them is the difference
    between a cancelled run that leaves nothing and one that leaves a truncated file to clean up.
    """
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(
    args: list[str],
    *,
    cancel: CancelToken | None = None,
    log=None,
    progress=None,
    heartbeat=None,
    expected_seconds: float | None = None,
    timeout: float | None = None,
) -> None:
    """Run ffmpeg to completion, reporting as it goes, and refuse to run forever.

    ``expected_seconds`` is the length of output the pass should produce. It is used for two things: the
    fraction the progress bar draws, and the time limit, which is derived from it rather than fixed,
    because a fixed limit is either too short for a long file or too long to notice a wedged
    process. The multiplier is deliberately loose — a slow disk is not a fault — and the floor keeps
    a short pass from being killed on a machine that is busy with something else.
    """
    command = list(args)
    if log is not None:
        log("$ " + " ".join(command))

    limit = timeout
    if limit is None:
        limit = max(180.0, (expected_seconds if expected_seconds else 60.0) * 10.0 + 60.0)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        creationflags=NO_WINDOW,
    )

    lines: queue.Queue[str] = queue.Queue()
    errors: list[str] = []
    ending = "__thenormalizer_end__"

    def pump_progress() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.strip())
        lines.put(ending)

    def pump_errors() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            text = line.rstrip("\r\n")
            errors.append(text)
            if log is not None and text.strip():
                log(text)

    for pump in (pump_progress, pump_errors):
        threading.Thread(target=pump, daemon=True).start()

    started = time.monotonic()
    last_beat = started
    fields: dict[str, str] = {}
    finished = False

    while not finished:
        if cancel is not None and cancel.cancelled():
            _stop(process)
            raise Cancelled("the run was cancelled")

        try:
            line = lines.get(timeout=POLL_SECONDS)
        except queue.Empty:
            now = time.monotonic()
            if now - started > limit:
                _stop(process)
                raise FFmpegError(
                    f"ffmpeg ran for more than the {limit:.0f}s allowed for this pass and was "
                    f"stopped. It had produced {fields.get('out_time', 'nothing')}.",
                    command,
                    "\n".join(errors),
                )
            if heartbeat is not None and now - last_beat > HEARTBEAT_SECONDS:
                heartbeat()
                last_beat = now
            continue

        if line == ending:
            finished = True
            continue

        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
        if key.strip() == "progress" and progress is not None:
            tick = _tick(fields, expected_seconds)
            if tick is not None:
                progress(tick)
            last_beat = time.monotonic()

    process.wait()
    if process.returncode != 0:
        tail = "\n".join(errors)
        # Exit 1 with no signal marker is how Windows reports a process that was killed. Saying
        # "stopped" rather than "failed" keeps it from being read as a bad argument.
        what = "ffmpeg stopped" if process.returncode == 1 else f"ffmpeg failed ({process.returncode})"
        raise FFmpegError(what, command, tail)


def _tick(fields: dict[str, str], expected: float | None) -> Ticks | None:
    """One ``-progress`` block as a position.

    ``out_time_us`` is the field to read. ``out_time_ms`` also exists and is microseconds — a
    long-standing misnomer in ffmpeg — so it is accepted only as a fallback, and ``out_time``, which is
    a formatted clock, only after that. A block with none of them is not a position.
    """
    seconds: float | None = None
    for key in ("out_time_us", "out_time_ms"):
        raw = fields.get(key)
        if raw and raw.lstrip("-").isdigit():
            seconds = int(raw) / 1_000_000.0
            break
    if seconds is None:
        raw = fields.get("out_time", "")
        if raw and ":" in raw:
            try:
                hours, minutes, rest = raw.split(":")
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(rest)
            except ValueError:
                seconds = None
    if seconds is None:
        return None

    speed: float | None = None
    raw_speed = fields.get("speed", "").strip()
    if raw_speed.endswith("x"):
        try:
            speed = float(raw_speed[:-1])
        except ValueError:
            speed = None

    def whole(key: str) -> int | None:
        raw = fields.get(key)
        if raw and raw.lstrip("-").isdigit():
            return int(raw)
        return None

    return Ticks(
        out_seconds=max(0.0, seconds),
        frame=whole("frame"),
        speed=speed,
        size=whole("total_size"),
        expected_seconds=expected,
    )
