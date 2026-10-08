"""TheNormalizer's backend: the engine, behind a local HTTP and event-stream API.

## Why there is a server at all

The engine is Python and the interface is a web page, so something has to sit between them. It is a
loopback HTTP server rather than a subprocess protocol because the progress of a run is a *stream*: the
window needs a bar that moves while ffmpeg works, and server-sent events are the smallest thing that
does that without a socket library on both sides.

It binds ``127.0.0.1`` only. Nothing here is reachable from another machine, and there is no
authentication because there is nothing to authenticate against — the same single user who started the
window is the only client that can reach the port.

## The shape of a run

``POST /api/normalizations`` starts a **batch** and returns ``201`` immediately with a batch id and one
job id per source; the work happens on a single worker thread because a run is minutes of ffmpeg and
the engine is synchronous. Everything the worker says — every job's stage, every exact command line,
every position ffmpeg reports, every measurement and every verdict — is pushed onto ``/api/events`` as
it happens.

## Why a batch and not one file at a time

Because the *product* is a batch: an operator normalizes an episode's worth of files, and a window that
made them press the button once per file would make the queue the operator's problem rather than the
tool's. The engine's unit of work is still **one file** — its own peak, its own gain, its own output,
its own verdict — and this module is what puts several of them in a row.

They run **one at a time and in order**, which is deliberate. Two ffmpeg processes on one disk are not
twice as fast, and the second one's progress would make the first one's bar a lie. A batch requested
while a batch is running is a ``409``, for the same reason the sibling product refuses a second stitch:
there is nothing in the interface that can ask for it, and a queue inside the engine would be a queue
nobody could see.

## The one failure that is not a transport error

A file that was written and then *measured* as wrong is a result. The ``finished`` event carries
``verified: false`` and the HTTP request that started it already returned ``201``. Reporting a bad
measurement as a ``500`` would say the server broke, when what happened is the server worked and the
answer was no. A file that could not be processed at all — a refusal, an ffmpeg failure — is that
**job's** failure and does not stop the batch: the remaining files are still the operator's work, and
stopping at the first unreadable file would be this tool deciding they were done.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))

from normalizer import media as prober  # noqa: E402
from normalizer import normalize as normalizer  # noqa: E402
from normalizer import verify as verifier  # noqa: E402
from normalizer.process import CancelToken, Cancelled, FFmpegError, Ticks, tool  # noqa: E402

VERSION = "2.0.0"
PRODUCT = "TheNormalizer"

#: The loopback port, and deliberately not a sibling's.
#:
#: 8765 is the cutting tool's and 8766 is the stitching tool's. Three of these products can be launched
#: side by side on one machine, all three answers carry ``/api/health`` and a planning route, and a
#: port clash would be one tool silently talking to another tool's engine — a failure nobody notices
#: until the numbers are wrong. The health check therefore requires the body to *name this product*, so
#: a stranger on the port is reported as a stranger rather than waited out and called a timeout.
PORT = int(os.environ.get("PORT", "8767"))

#: The routes, in the order the index lists them. One list, so the index cannot advertise a route that
#: does not exist or forget one that does — and the test asserts the two agree.
ROUTES = [
    "health",
    "probe",
    "normalization-plans",
    "normalizations",
    "normalizations/current",
    "events",
]

logger = logging.getLogger("thenormalizer")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


# ---------------------------------------------------------------------------------------
# Everything the window can be told
# ---------------------------------------------------------------------------------------

class Hub:
    """The fan-out from the worker thread to whatever is listening on ``/api/events``.

    A plain ``queue.Queue`` per subscriber, and ``put_nowait`` everywhere: a window that has stopped
    reading must never be able to block a run. Dropping an event for a dead subscriber costs nothing;
    blocking ffmpeg behind a closed browser tab costs the run.

    A ``queue.Queue`` rather than an ``asyncio.Queue`` because the producers are not coroutines: they
    are the worker thread and ffmpeg's stderr reader. ``asyncio.Queue`` is not thread-safe, and the
    machinery needed to make it safe buys nothing that this does not already do.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._history: list[dict[str, Any]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        channel: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4096)
        with self._lock:
            self._subscribers.append(channel)
            for event in self._history:
                try:
                    channel.put_nowait(event)
                except queue.Full:
                    break
        return channel

    def unsubscribe(self, channel: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            stamped = {**event, "at": time.time()}
            # The history is what a window that connects mid-run is caught up with. Bounded, because a
            # batch of forty files says thousands of things and the log keeps the last few hundred.
            self._history.append(stamped)
            if len(self._history) > 600:
                del self._history[:-600]
            for channel in self._subscribers:
                try:
                    channel.put_nowait(stamped)
                except queue.Full:
                    pass

    def reset(self) -> None:
        with self._lock:
            self._history.clear()


HUB = Hub()


class Job:
    """One file's run: its identity, its index in the batch, and its state.

    ``state`` walks ``queued`` → ``measuring`` → ``running`` → ``verifying`` → ``done``, and any of the
    last three can instead become ``failed`` or ``cancelled``. It is a plain string rather than an enum
    because it crosses a JSON boundary, and the window branches on the same words.
    """

    def __init__(self, spec: normalizer.NormalizeSpec, index: int) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.index = index
        self.spec = spec
        self.state = "queued"
        self.cancel = CancelToken()
        self.started = time.monotonic()
        self.plan: normalizer.NormalizePlan | None = None
        self.outcome: dict[str, Any] | None = None
        self.error: str | None = None
        self.reason: str | None = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


class Batch:
    """A queue of files, one worker, and the state of each."""

    def __init__(self, jobs: list[Job]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.jobs = jobs
        self.cancel = CancelToken()
        self.thread: threading.Thread | None = None
        self.started = time.monotonic()
        self.done = threading.Event()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


CURRENT: Batch | None = None
CURRENT_LOCK = threading.Lock()


# ---------------------------------------------------------------------------------------
# The engine's records, as JSON
# ---------------------------------------------------------------------------------------

def _level(text: str) -> str:
    """Which kind of log line this is, from the shape the engine writes it with."""
    stripped = text.strip()
    if stripped.startswith("$"):
        return "command"
    if stripped.startswith("->"):
        return "done" if "ok" in stripped else "error"
    if stripped.startswith("..."):
        return "heartbeat"
    return "stage"


def _ticks(tick: Ticks) -> dict[str, Any]:
    """One progress reading. The engine's own dataclass is the source of every field."""
    return {
        "type": "progress",
        "outSeconds": tick.out_seconds,
        "fraction": tick.fraction,
        "speed": tick.speed,
        "remaining": tick.remaining,
        "frame": tick.frame,
        "size": tick.size,
        "expectedSeconds": tick.expected_seconds,
    }


def _clock(seconds: float) -> str:
    """`H:MM:SS.mmm` — the form an operator reads a position in.

    Nine hours of runtime, not twenty-four, and no frame field: a normalizer's unit is a sample rather
    than a frame, and a timecode with frames in it would be a timecode that invited the reader to
    believe there was a frame grid under the sound.
    """
    whole = max(0.0, seconds)
    hours = int(whole // 3600)
    minutes = int((whole % 3600) // 60)
    rest = whole - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{rest:06.3f}"


def _describe(info: prober.MediaInfo) -> dict[str, Any]:
    """A probed file, as the window reads it. No field here is computed in the interface."""
    video = info.video
    return {
        "path": str(info.path),
        "name": info.name,
        "kind": info.kind,
        "container": info.container,
        "duration": info.duration,
        "durationText": _clock(info.duration),
        "sizeBytes": info.size_bytes,
        "summary": info.summary(),
        "video": None if video is None else {
            "codec": video.codec,
            "profile": video.profile,
            "level": video.level,
            "width": video.width,
            "height": video.height,
            "pixFmt": video.pix_fmt,
            "rate": {"numerator": video.rate.numerator, "denominator": video.rate.denominator},
            "rateText": video.rate_text,
            "frames": video.frames,
            "timebase": f"1/{video.timebase.denominator}" if video.timebase else "?",
            "hasBFrames": video.has_b_frames,
        },
        "audio": (
            None
            if info.audio is None
            else {
                "codec": info.audio.codec,
                "sampleRate": info.audio.sample_rate,
                "channels": info.audio.channels,
                "bitRate": info.audio.bit_rate,
                "describe": info.audio.describe(),
            }
        ),
        "streams": [{"kind": kind, "codec": codec} for kind, codec in info.streams],
    }


def _levels_json(peak: float | None, mean: float | None, seconds: float) -> dict[str, Any]:
    """A measured level, as the window reads it.

    ``peakDbfs`` is ``None`` for digital silence, and that is a value the window has to *render* — the
    honest form of it is the word, not a very negative number, because a number would be arithmetic
    waiting to happen.
    """
    return {
        "peakDbfs": peak,
        "meanDbfs": mean,
        "peakText": "silence" if peak is None else f"{peak:.2f} dBFS",
        "meanText": "silence" if mean is None else f"{mean:.2f} dBFS",
        "seconds": seconds,
        "secondsText": _clock(seconds),
    }


def _audio_json(plan: normalizer.NormalizePlan) -> dict[str, Any]:
    """The sound files this run writes beside the master, and what they will weigh.

    ## Why the paths are here rather than derived in the window

    The `.wav` and the `.mp3` sit beside the master with the same stem, so the window *could* work them
    out. It does not, for the same reason it does not add up dB: a path derived twice is a path that can
    be derived two ways, and the one the operator reads before pressing Start has to be the one the
    engine writes to.

    ## Why the size is here rather than worked out in the window

    A WAV is `seconds × sample rate × channels × 2` bytes and an MP3 is `seconds × bitrate / 8`, and the
    operator ticking these boxes is choosing between about 190 MB and about 44 MB for a 17-minute file.
    That is a figure this product decided to show, so it is a figure the engine measures — the same rule
    that keeps the gain out of the interface. The inputs are the engine's own: the plan's duration, the
    measured sample rate and channel count, and the bitrate constant the MP3 command is built from.
    """
    paths = plan.audio_paths
    if not paths:
        return {"files": [], "taken": []}

    seconds = plan.duration
    audio = plan.source.audio
    sample_rate = audio.sample_rate if audio else 0
    channels = audio.channels if audio else 0
    samples = int(round(seconds * sample_rate)) if sample_rate else 0

    def size_of(path: Path) -> int:
        if path.suffix == ".wav":
            # 16-bit PCM: two bytes a sample, once per channel. No container overhead worth counting
            # for a file this size, and a figure that is a hair small is better than one that is
            # invented.
            return samples * channels * 2
        return int(seconds * _mp3_bits_per_second() / 8)

    return {
        "files": [
            {
                "path": str(path),
                "name": path.name,
                "seconds": seconds,
                "secondsText": _clock(seconds),
                "samples": samples,
                "bytes": size_of(path),
            }
            for path in paths
        ],
        "taken": [path.suffix for path in paths if path.exists()],
    }


def _mp3_bits_per_second() -> int:
    """``MP3_BITRATE`` as a number, because the size estimate needs one.

    The constant is ffmpeg's own spelling (``"320k"``), and the size shown for the file is computed
    from it — so the parse is written out rather than done with ``rstrip("k")``, which would silently
    read a future ``"320K"``, ``"320 k"`` or ``"1M"`` as ``320``, ``320 `` and ``1``. A wrong number
    here would not break anything; it would just be wrong on screen, which is worse.
    """
    raw = normalizer.MP3_BITRATE.strip().lower()
    multiplier = 1
    for suffix, scale in (("k", 1000), ("m", 1_000_000)):
        if raw.endswith(suffix):
            raw, multiplier = raw[: -len(suffix)], scale
            break
    try:
        return int(float(raw) * multiplier)
    except ValueError:  # a constant ffmpeg would not accept either; the caller shows no size for it
        return 0


def _plan_json(plan: normalizer.NormalizePlan) -> dict[str, Any]:
    return {
        "source": _describe(plan.source),
        "output": str(plan.spec.output),
        "outputName": plan.spec.output.name,
        "container": plan.container,
        "strategy": plan.spec.strategy,
        "strategyNote": normalizer.STRATEGIES.get(plan.spec.strategy, ""),
        "targetDbfs": plan.spec.target_dbfs,
        "targetText": plan.target_text,
        "trimDb": plan.spec.trim_db,
        # The operator's two chain figures, and the sentence saying what they do to the dynamics. They
        # travel with the plan because they are the settings that can be turned the wrong way without
        # any symptom in the output's *level*: the fader after the chain puts the result on the target
        # whatever the chain did, so a ceiling that is too low costs dynamics and nothing else.
        "makeupDb": plan.spec.makeup_db,
        "ceilingDbfs": plan.spec.ceiling_dbfs,
        "chainNote": normalizer.dynamics_note(plan.spec.makeup_db, plan.spec.ceiling_dbfs),
        "chainHelp": dict(normalizer.CHAIN_HELP),
        "gainDb": plan.total_gain_db,
        "gainText": plan.gain_text,
        # False until the run has measured what the chain and the encoder do to *this* file. The window
        # shows the provisional figure with the note that it is provisional rather than hiding the row:
        # a plan that showed nothing where a gain belongs would read as a plan that had failed.
        "gainIsMeasured": plan.gain_is_measured,
        "expectedPeakDbfs": plan.expected_peak_dbfs,
        "sourceLevels": _levels_json(plan.source_peak_dbfs, plan.source_mean_dbfs, plan.duration),
        "duration": plan.duration,
        "durationText": _clock(plan.duration),
        "keepsPicture": plan.keeps_picture,
        "droppedStreams": list(plan.dropped_streams),
        "notes": list(plan.notes),
        "cautions": list(plan.cautions),
        "audio": _audio_json(plan),
        # The graph itself, so the window can show what will be done to the sound rather than
        # describing it in its own words. A filtergraph is a fact; a sentence about one is a summary.
        "filterGraph": normalizer.sound_graph(plan),
    }


def _check_json(result: verifier.VerifyResult) -> list[dict[str, Any]]:
    return [
        {"check": check.name, "status": check.status, "detail": check.detail}
        for check in result.checks
    ]


def _outcome_json(job: Job, output: normalizer.NormalizeResult) -> dict[str, Any]:
    plan = job.plan
    assert plan is not None
    return {
        "job": job.id,
        "index": job.index,
        "source": str(job.spec.source),
        "output": str(output.output),
        "plan": _plan_json(plan),
        "checks": [],
        "verified": None,
        "elapsed": job.elapsed,
        # What the *run* wrote, not what is on the disk at that path: a `.wav` that was already there
        # was skipped, and one that arrived while the run was working was not written over. Asking the
        # disk instead would answer "yes" to both and offer the operator a file from an earlier run as
        # though this one had made it.
        "audioWritten": [str(path) for path in output.audio],
    }


# ---------------------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------------------

def _run_batch(batch: Batch) -> None:
    """The worker. One file at a time, in order, and everything it says goes onto the hub."""
    global CURRENT
    publish = HUB.publish

    def make_log(job: Job):
        def log(text: str) -> None:
            for line in str(text).splitlines() or [""]:
                publish({"type": "log", "job": job.id, "level": _level(line), "text": line})
        return log

    def make_progress(job: Job):
        def progress(tick: Ticks) -> None:
            publish({**_ticks(tick), "job": job.id})
        return progress

    def make_stage(job: Job):
        def stage(label: str) -> None:
            job.state = "running"
            publish({"type": "stage", "job": job.id, "label": label})
        return stage

    publish({
        "type": "batch-started",
        "batch": batch.id,
        "jobs": [
            {"job": job.id, "index": job.index, "source": str(job.spec.source), "output": str(job.spec.output)}
            for job in batch.jobs
        ],
    })

    for job in batch.jobs:
        log = make_log(job)
        progress = make_progress(job)
        stage = make_stage(job)
        try:
            if batch.cancel.cancelled() or job.cancel.cancelled():
                raise Cancelled("the batch was cancelled before this file started")

            job.state = "measuring"
            publish({"type": "job-started", "job": job.id, "source": str(job.spec.source)})

            stage("reading the file")
            info = prober.probe(job.spec.source)
            publish({"type": "media", "job": job.id, "media": _describe(info)})

            stage("measuring what the sound peaks at")
            levels = prober.levels(info)
            publish({
                "type": "measured",
                "job": job.id,
                "levels": _levels_json(levels.max_dbfs, levels.mean_dbfs, info.duration),
            })

            stage("planning the gain")
            plan = normalizer.plan_normalize(job.spec, info, levels.max_dbfs, levels.mean_dbfs)
            job.plan = plan
            publish({"type": "plan", "job": job.id, "plan": _plan_json(plan)})
            for caution in plan.cautions:
                log(caution)

            output = normalizer.run_normalize(
                plan, cancel=job.cancel, log=log, progress=progress, stage=stage
            )

            job.state = "verifying"
            outcome = _outcome_json(job, output)

            stage("measuring the result")
            result = verifier.verify(plan, output.output)
            outcome["checks"] = _check_json(result)
            outcome["verified"] = result.ok
            outcome["outputLevels"] = _levels_json(
                result.output_peak_dbfs, result.output_mean_dbfs, result.sound_seconds
            )
            outcome["peakErrorDb"] = result.peak_error_db
            outcome["appliedGainDb"] = result.applied_gain_db
            for line in result.report().splitlines():
                log(line)

            job.outcome = outcome
            job.state = "done"
            publish({
                "type": "job-finished",
                "job": job.id,
                "ok": result.ok,
                "outcome": outcome,
            })
        except Cancelled:
            job.state = "cancelled"
            job.outcome = None
            publish({"type": "job-cancelled", "job": job.id})
        except normalizer.NormalizeError as refusal:
            # A refusal is the engine working. It has a stable tag so the window can put it beside the
            # file it belongs to rather than in a generic error bar.
            job.state = "failed"
            job.error = str(refusal)
            job.reason = refusal.reason
            logger.info("refused: %s", refusal)
            publish({
                "type": "job-failed",
                "job": job.id,
                "message": str(refusal),
                "reason": refusal.reason,
            })
        except FFmpegError as failure:
            job.state = "failed"
            job.error = str(failure)
            job.reason = "ffmpeg"
            logger.warning("ffmpeg: %s", failure)
            publish({"type": "job-failed", "job": job.id, "message": str(failure), "reason": "ffmpeg"})
        except Exception as failure:  # noqa: BLE001 - the worker must never die silently
            job.state = "failed"
            job.error = str(failure)
            job.reason = "internal"
            logger.exception("a job failed")
            publish({"type": "log", "job": job.id, "level": "error", "text": traceback.format_exc(limit=3)})
            publish({"type": "job-failed", "job": job.id, "message": str(failure), "reason": "internal"})

        if batch.cancel.cancelled():
            # Everything after a cancel is reported as cancelled rather than silently skipped: a row
            # that says "queued" for ever is worse than one that says the batch stopped.
            for waiting in batch.jobs[job.index + 1:]:
                if waiting.state == "queued":
                    waiting.state = "cancelled"
                    publish({"type": "job-cancelled", "job": waiting.id})
            break

    done = sum(1 for job in batch.jobs if job.state == "done")
    failed = sum(1 for job in batch.jobs if job.state == "failed")
    cancelled = sum(1 for job in batch.jobs if job.state == "cancelled")
    publish({
        "type": "batch-finished",
        "batch": batch.id,
        "done": done,
        "failed": failed,
        "cancelled": cancelled,
        "elapsed": batch.elapsed,
    })

    batch.done.set()
    with CURRENT_LOCK:
        if CURRENT is batch:
            CURRENT = None


# ---------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------

async def index(_request: web.Request) -> web.Response:
    return web.json_response({"product": PRODUCT, "version": VERSION, "routes": ROUTES})


async def health(_request: web.Request) -> web.Response:
    """Can this machine normalize, and with what.

    The answer is deliberately small. ``ffmpeg -encoders`` and ``-filters`` print tens of kilobytes of
    lists, and the only thing the window does with them is decide whether a lamp is green and whether a
    box can be ticked — so the decisions are made here and the lists are not sent.

    ``product`` is what the Electron shell checks to tell this engine from whatever else may be holding
    the port. It matters more here than in the siblings: there are now three of these applications, all
    of them answer ``/api/health`` and all of them have a planning route.

    Three capabilities and not one. ``libmp3lame`` decides whether the MP3 export can be written;
    ``aac`` decides whether the sound can be encoded at all, and an ffmpeg without it cannot normalize
    anything; ``loudnorm`` says whether this machine's ffmpeg has the EBU R128 filters, which this
    product does not use but a future loudness target would need. They are different answers to
    different questions, and collapsing them would leave an operator choosing a format in a window that
    had no way to know the encoder was missing until the last pass of a run.
    """
    answer: dict[str, Any] = {
        "product": PRODUCT,
        "version": VERSION,
        "ffmpeg": None,
        "aac": False,
        "libmp3lame": False,
        "loudnorm": False,
    }
    try:
        answer["ffmpeg"] = tool("ffmpeg")
        from normalizer.process import encoders, filters, version

        answer["versionLine"] = version("ffmpeg")
        encoders_list = encoders()
        filters_list = filters()
        answer["aac"] = " aac " in encoders_list or "\naac " in encoders_list
        answer["libmp3lame"] = "libmp3lame" in encoders_list
        answer["loudnorm"] = "loudnorm" in filters_list
    except Exception as failure:  # noqa: BLE001 - health reports, it does not throw
        answer["error"] = str(failure)
    return web.json_response(answer)


async def probe(request: web.Request) -> web.Response:
    """Measure one source: its streams, and what its sound peaks at.

    The level measurement decodes the whole sound, which on a long file is seconds rather than
    milliseconds — so this route is called when a file is *chosen* and the answer is kept, not on every
    keystroke. See ``normalization-plans``: it takes the level as a request field for exactly that
    reason.
    """
    path = Path(request.query.get("path", ""))
    if not path.is_file():
        return web.json_response({"error": f"{path} is not a file", "reason": "input"}, status=400)
    try:
        info = await asyncio.to_thread(prober.probe, path)
        levels = await asyncio.to_thread(prober.levels, info)
    except FFmpegError as failure:
        return web.json_response({"error": str(failure), "reason": "ffmpeg"}, status=400)

    # The container's extension, and not the source's: a `.wav` is written as an `.m4a`, so a suggestion
    # carrying the source's extension would name a file the run does not write. It is worked out from the
    # probed file rather than guessed, which is the same reason `plan_normalize` settles the destination
    # itself.
    _extension, _muxer, _label = normalizer.container_for(info)

    return web.json_response({
        "media": _describe(info),
        "levels": _levels_json(levels.max_dbfs, levels.mean_dbfs, info.duration),
        "suggestedOutput": str(normalizer.output_for(path, _extension)),
    })


async def _body(request: web.Request) -> Any:
    """The request's JSON, or a refusal saying what arrived instead.

    ``Request.json()`` lets a ``JSONDecodeError`` out for a body that is not JSON at all, which reaches
    the window as a 500 — and a 500 says the server broke, when what happened is the caller sent
    something that is not a request. Both routes that take a body parse it here, so neither can refuse a
    malformed one differently from the other.
    """
    try:
        return await request.json()
    except ValueError as malformed:
        raise normalizer.InputRefused(f"the request body is not JSON: {malformed}") from None


def _number(value: Any, field: str, default: float) -> float:
    """One request field that has to be a number.

    ``float("abc")`` is a ``ValueError`` and ``float(None)`` is a ``TypeError``. Both are a malformed
    request, and neither may reach the window as a 500. A numeric string is accepted because an
    input field is a string until it is parsed, and a JSON number is accepted because a script has no
    reason to write one as text.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise normalizer.InputRefused(f"the {field} field has to be a number, not {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise normalizer.InputRefused(f"the {field} field has to be a number, not {value!r}")


def _audio_field(value: Any) -> tuple[str, ...]:
    """The ``audio`` request field as the engine's own request type.

    A list of extensions rather than one boolean per format, because the two formats that exist are not
    independent: ``.mp3`` is encoded *from* the ``.wav`` the same run writes, so "which of these, in
    which order" is the whole of the request and a pair of flags would let a caller ask for the second
    without the first.

    The order is the engine's, not the caller's. ``AUDIO_FORMATS`` fixes it, and a window that sent them
    the other way round would be asking for the MP3 to be made out of a file that does not exist yet. A
    field that can express that is a field that can break a run, so it cannot express it.
    """
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise normalizer.InputRefused(
            f"the audio field has to be a list of extensions like ['.wav', '.mp3'], not "
            f"{type(value).__name__}"
        )
    wanted = {str(extension) for extension in value}
    unknown = sorted(wanted - set(normalizer.AUDIO_FORMATS))
    if unknown:
        raise normalizer.InputRefused(
            "the audio field names formats nothing here can write: " + ", ".join(unknown) + ". The "
            "ones it can write are " + ", ".join(normalizer.AUDIO_FORMATS) + "."
        )
    return tuple(extension for extension in normalizer.AUDIO_FORMATS if extension in wanted)


def _settings(body: dict[str, Any]) -> dict[str, Any]:
    """The fields that describe *how* to normalize, shared by the plan and the run.

    One reader, so a plan cannot be built with a different target from the run that follows it — which
    is the failure mode this whole arrangement exists to prevent: the window shows the operator a gain,
    the operator presses Start, and the run uses a different one.
    """
    return {
        "target": _number(body.get("targetDbfs"), "target", normalizer.DEFAULT_TARGET_DBFS),
        "strategy": str(body.get("strategy") or normalizer.DEFAULT_STRATEGY),
        # The two chain figures the operator owns. They travel with every request for the same reason
        # the target does: the plan states what they will do to the dynamics, and the run has to be the
        # run that was planned.
        "makeup": _number(body.get("makeupDb"), "makeup", normalizer.DEFAULT_MAKEUP_DB),
        "ceiling": _number(body.get("ceilingDbfs"), "ceiling", normalizer.DEFAULT_CEILING_DBFS),
        "trim": _number(body.get("trimDb"), "trim", 0.0),
        "audio": _audio_field(body.get("audio")),
        "bitrate": str(body.get("audioBitrate") or "192k"),
    }


def _spec_from(
    body: dict[str, Any], settings: dict[str, Any], source: Path, output: Path | None
) -> normalizer.NormalizeSpec:
    """One source as the engine's own request type."""
    return normalizer.NormalizeSpec(
        source=source,
        output=output if output is not None else normalizer.output_for(source),
        target_dbfs=settings["target"],
        strategy=settings["strategy"],
        makeup_db=settings["makeup"],
        ceiling_dbfs=settings["ceiling"],
        trim_db=settings["trim"],
        audio_exports=settings["audio"],
        audio_bitrate=settings["bitrate"],
    )


def _sources_from(body: dict[str, Any]) -> list[Path]:
    """The ``sources`` request field: one or more files, in the order given."""
    raw = body.get("sources")
    if raw is None:
        raw = [body.get("source")] if body.get("source") else []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise normalizer.InputRefused("the request is missing: sources")
    paths: list[Path] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise normalizer.InputRefused(f"a source has to be a path, not {entry!r}")
        path = Path(entry.strip())
        if not path.is_file():
            raise normalizer.InputRefused(f"this source is not a file: {path}")
        if path in paths:
            # A duplicate is a formatting mistake rather than a request for two runs, and the engine
            # would otherwise normalize the same file twice and write `2` beside `1` for no reason.
            continue
        paths.append(path)
    if not paths:
        raise normalizer.InputRefused("the request names no sources")
    return paths


def _refusal(failure: Exception, status: int = 400) -> web.Response:
    payload: dict[str, Any] = {"error": str(failure)}
    if isinstance(failure, normalizer.NormalizeError):
        payload["reason"] = failure.reason
    return web.json_response(payload, status=status)


def _status_for(failure: normalizer.NormalizeError) -> int:
    """Which HTTP status a refusal is.

    A malformed request is the client's fault (400); a file that is individually fine but cannot be
    normalized is a different thing and gets a different code (422), because the fix is a different
    file rather than a different request. An output that cannot be written is a conflict (409).
    """
    return {"input": 400, "shape": 422, "output": 409}.get(failure.reason, 400)


def _plan_from_body(body: dict[str, Any]) -> tuple[normalizer.NormalizePlan, dict[str, Any]]:
    """Plan one file from a request, measuring what the plan needs.

    The first thing it does is refuse a body that is not an object, because `_body` will happily return
    a list, a string or a number for a body that is valid JSON — and `body.get` on any of those is an
    `AttributeError`, which reaches the window as a `500` saying the server broke when what happened is
    the caller sent something that is not a request. Both routes that plan call this, so neither can
    refuse a malformed body differently from the other.

    ## Why the level is a *request* field and not something this route measures

    ``peakDbfs`` is the source's measured peak, and it is the one input to the plan that cannot be
    derived from the file's header. Measuring it costs a full decode of the sound — seconds on a long
    file — and this route is called as the operator moves a slider. So the window measures once, when
    the file is chosen (``GET /api/probe``), and sends the figure back.

    ## Why that is not a hole in the arithmetic

    A caller could send a peak that is not true, and the *plan* would then state a gain that the run
    would not produce. What it cannot do is make the run produce that gain: ``run_normalize`` builds its
    commands from the plan, and the plan's gain is arithmetic on the number it was given — so the run
    would apply the gain it was asked for, and the *verification*, which re-measures the finished file
    rather than trusting anything, would report the output's real level and fail the target check. The
    lie is therefore visible in the report, which is the only place it could matter. The alternative —
    measuring on every keystroke — is a decode per slider position.
    """
    if not isinstance(body, dict):
        raise normalizer.InputRefused(
            f"the request body has to be an object, not {type(body).__name__}"
        )
    settings = _settings(body)
    sources = _sources_from(body)
    source = sources[0]
    info = prober.probe(source)

    supplied = body.get("peakDbfs")
    if supplied is None:
        measured = prober.levels(info)
        peak, mean = measured.max_dbfs, measured.mean_dbfs
    else:
        peak = None if supplied == "silence" else _number(supplied, "peakDbfs", 0.0)
        mean = None if body.get("meanDbfs") in (None, "") else _number(body.get("meanDbfs"), "meanDbfs", 0.0)

    output = body.get("output")
    plan = normalizer.plan_normalize(
        _spec_from(body, settings, source, Path(str(output)) if output else None),
        info,
        peak,
        mean,
    )
    return plan, settings


async def normalization_plans(request: web.Request) -> web.Response:
    """What the run will do, without writing anything. Called as the fields settle.

    ``outputExists`` is the one field here that the plan did not decide: whether something is already at
    the path the run would write to. ``output_for`` walks the name until it finds a free one, so the
    answer is normally no — but the window may have been handed a path by its Save dialog, and this is
    how it finds out before the press rather than after it.
    """
    try:
        plan, _settings_used = await asyncio.to_thread(_plan_from_body, await _body(request))
    except normalizer.NormalizeError as refusal:
        return _refusal(refusal, _status_for(refusal))
    except FFmpegError as failure:
        return web.json_response({"error": str(failure), "reason": "ffmpeg"}, status=400)
    return web.json_response({
        "plan": _plan_json(plan),
        "outputExists": plan.spec.output.exists(),
        "audioTaken": [path.suffix for path in plan.audio_paths if path.exists()],
    })


async def start_batch(request: web.Request) -> web.Response:
    """Start a batch. Returns at once; the work is reported on ``/api/events``."""
    global CURRENT
    try:
        body = await _body(request)
        if not isinstance(body, dict):
            raise normalizer.InputRefused(
                f"the request body has to be an object, not {type(body).__name__}"
            )
        settings = _settings(body)
        sources = _sources_from(body)
        # Every path is decided *here*, before a job exists, so the answer to "where is the output"
        # is one the window was told rather than one it works out — and so a name is reserved in the
        # order the batch was asked for rather than in the order the files happen to finish.
        outputs = await asyncio.to_thread(_reserve_outputs, body, settings, sources)
    except normalizer.NormalizeError as refusal:
        return _refusal(refusal, _status_for(refusal))

    with CURRENT_LOCK:
        if CURRENT is not None:
            return web.json_response(
                {
                    "error": "a batch is already running; stop it before starting another",
                    "reason": "busy",
                },
                status=409,
            )
        HUB.reset()
        jobs = [
            Job(_spec_from(body, settings, source, output), index)
            for index, (source, output) in enumerate(zip(sources, outputs, strict=True))
        ]
        batch = Batch(jobs)
        CURRENT = batch

    batch.thread = threading.Thread(target=_run_batch, args=(batch,), daemon=True, name="normalizer")
    batch.thread.start()
    return web.json_response(
        {
            "batch": batch.id,
            "jobs": [
                {"job": job.id, "index": job.index, "source": str(job.spec.source), "output": str(job.spec.output)}
                for job in jobs
            ],
            "started": True,
        },
        status=201,
        headers={"Location": "/api/normalizations/current"},
    )


def _reserve_outputs(
    body: dict[str, Any], settings: dict[str, Any], sources: list[Path]
) -> list[Path]:
    """Where each file in the batch will be written.

    One name at a time and never the same name twice: two sources called `talk.m4a` from different
    folders are two files, and a batch that gave them both `talk - normalized.m4a` in the same output
    folder would have the second overwrite the first — which this product does not do. The reservation
    is a small set of the names already chosen in *this* request, checked as each one is picked.
    """
    asked = body.get("output")
    if asked:
        # A single explicit output and one source: that is the Save dialog's answer, and it is the
        # operator's path rather than a name this product chooses.
        if len(sources) > 1:
            raise normalizer.InputRefused(
                "a batch of several files cannot be given one output path. Leave the output field "
                "empty and each normalized file is written beside its source."
            )
        return [Path(str(asked))]

    taken: set[Path] = set()
    outputs: list[Path] = []
    for source in sources:
        candidate = normalizer.output_for(source)
        index = 2
        while candidate in taken:
            candidate = source.with_name(f"{source.stem}{normalizer.SUFFIX} {index}{candidate.suffix}")
            index += 1
        taken.add(candidate)
        outputs.append(candidate)
    return outputs


async def current_batch(_request: web.Request) -> web.Response:
    """The singleton batch: what is running, or nothing."""
    with CURRENT_LOCK:
        batch = CURRENT
    if batch is None:
        return web.Response(status=204)
    return web.json_response({
        "batch": batch.id,
        "elapsed": batch.elapsed,
        "jobs": [
            {
                "job": job.id,
                "index": job.index,
                "source": str(job.spec.source),
                "output": str(job.spec.output),
                "state": job.state,
            }
            for job in batch.jobs
        ],
    })


async def cancel_batch(_request: web.Request) -> web.Response:
    """Cancel the running batch. Idempotent: cancelling nothing is an answer, not an error.

    The cancel token is polled by the worker and by every ffmpeg it is watching, so the file being
    worked on stops where it is and the ones behind it are reported as cancelled rather than left
    saying "queued" for ever.
    """
    with CURRENT_LOCK:
        batch = CURRENT
    if batch is None:
        return web.json_response({"cancelled": False, "reason": "nothing is running"})
    batch.cancel.cancel()
    return web.json_response({"cancelled": True, "batch": batch.id})


async def events(request: web.Request) -> web.StreamResponse:
    """The stream: everything a run says, as it says it.

    ## A client that goes away is not an error

    Windows reports a closed loopback connection as ``ConnectionResetError`` — and on a socket being
    written to, ``ClientConnectionResetError`` — from ``prepare`` as well as from ``write``. None of
    that is a fault: the window was reloaded, or closed, or a subscriber simply finished. Letting it
    reach aiohttp's handler produces a full traceback per event, which buries whatever the engine was
    actually saying.
    """
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    channel: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4096)
    try:
        await response.prepare(request)
        channel = HUB.subscribe()
        while True:
            try:
                event = await asyncio.to_thread(channel.get, True, 0.5)
            except queue.Empty:
                # A comment frame, so a proxy or a sleeping window keeps the pipe open and the browser
                # does not decide the stream has died.
                await response.write(b": keep-alive\n\n")
                continue
            payload = json.dumps(event, default=str)
            await response.write(f"data: {payload}\n\n".encode())
    except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
        # Re-raised would be wrong here and swallowed would be worse: the subscriber is leaving, which
        # is what the `finally` is for.
        pass
    finally:
        HUB.unsubscribe(channel)
    return response


# ---------------------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------------------

#: Where the built interface lives, relative to this file: `<root>/frontend/dist`.
INTERFACE = Path(__file__).resolve().parent.parent / "frontend" / "dist"


async def interface(request: web.Request) -> web.Response:
    """Serve the built page, so the window and the engine are one origin.

    ## Why the engine serves the interface and not Electron

    It was `loadFile` first in the sibling product, and it cannot work. A Vite build is an ES module,
    and Chromium refuses a module script loaded from `file://` — the origin is opaque, so the fetch
    fails CORS before a line of the app runs. Even had it loaded, every call to the engine would have
    been `file://` -> `http://127.0.0.1:8767`, which is cross-origin too, so the page would have had to
    be given CORS headers to talk to its own backend.

    Serving the two from one origin deletes both problems rather than working around them, and it is one
    handler.
    """
    wanted = request.match_info.get("path", "")
    if wanted == "api" or wanted.startswith("api/"):
        raise web.HTTPNotFound()

    target = (INTERFACE / wanted).resolve() if wanted else INTERFACE / "index.html"
    # `..` in a request is not a path, it is an attempt. Refuse anything outside the build.
    if target != INTERFACE and INTERFACE not in target.parents:
        raise web.HTTPNotFound()
    if not target.is_file():
        target = INTERFACE / "index.html"
    if not target.is_file():
        return web.json_response(
            {"error": f"the interface has not been built yet ({INTERFACE})"}, status=503
        )
    response = web.FileResponse(target)
    # The bundle's files keep their names across builds, so a copy held in the window's cache is
    # indistinguishable from the one that was just built, and Chromium will reuse it on the strength of
    # a `Last-Modified` alone. That is a window showing an interface that no longer exists, which is
    # indistinguishable from a change that never took effect.
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


def build_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/api", index),
        web.get("/api/health", health),
        web.get("/api/probe", probe),
        web.post("/api/normalization-plans", normalization_plans),
        web.post("/api/normalizations", start_batch),
        web.get("/api/normalizations/current", current_batch),
        web.delete("/api/normalizations/current", cancel_batch),
        web.get("/api/events", events),
    ])
    # Last, and a catch-all on purpose: `/api/health` was registered first and wins, so the only
    # requests that reach this are ones for the page or for a file the page needs.
    app.add_routes([web.get("/", interface), web.get("/{path:.*}", interface)])
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stderr,
    )
    print(f"{PRODUCT} backend {VERSION} on http://127.0.0.1:{PORT}", flush=True)
    # No access log: every event-stream reconnect would be a line, and the console belongs to what the
    # engine is saying about the work, not to a list of requests from one window.
    web.run_app(build_app(), host="127.0.0.1", port=PORT, print=None, access_log=None)


if __name__ == "__main__":
    main()
