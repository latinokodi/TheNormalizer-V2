"""The HTTP surface, driven the way the window drives it.

Everything here goes through a real server on a real loopback port, because the things worth checking at
this layer are the ones only a socket can show: that a refusal carries its tag, that a body which is not
a request is a `400` rather than a `500`, and that the plan answers with the path the run will really
write to.

## Why a thread, and not `aiohttp`'s `TestClient`

`TestClient` borrows the *running* event loop, and there is no running loop between tests. The sibling
products faced the same thing and answered it with `asyncio.run` in a helper, which works for a request
but not for a server whose loop has to outlive the test that started it. So the server runs on its own
loop in a daemon thread and each test drives it from its own `asyncio.run` — two loops, one socket, and
no `pytest-asyncio`, which is a dependency this suite does not otherwise need.

## What is replaced, and what is not

`media.probe`, `process.measure_levels`, `normalize.run` and `normalize._measure_decoded_aac` are the
four things that need a file or a process. The route table, the request parsing, the refusals, the job
bookkeeping and the event hub are all real.
"""

from __future__ import annotations

import asyncio
import threading
import time
from fractions import Fraction
from pathlib import Path

import pytest
from aiohttp import ClientSession, web

import server
from normalizer import media as prober
from normalizer import normalize as normalizer
from normalizer import process as process_module
from normalizer import verify as verifier

STARTUP_TIMEOUT = 10.0


class Engine:
    """A running backend, and the address it answers on."""

    def __init__(self) -> None:
        self.root = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        def serve() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)

            async def up() -> None:
                self._runner = web.AppRunner(server.build_app())
                await self._runner.setup()
                site = web.TCPSite(self._runner, "127.0.0.1", 0)
                await site.start()
                self.root = f"http://127.0.0.1:{self._runner.addresses[0][1]}"
                self._ready.set()
                while not loop.is_closed():
                    await asyncio.sleep(0.05)

            try:
                loop.run_until_complete(up())
            except (asyncio.CancelledError, RuntimeError):
                # The loop was closed under us, which is how this thread stops.
                pass

        self._thread = threading.Thread(target=serve, daemon=True, name="test-engine")
        self._thread.start()
        if not self._ready.wait(STARTUP_TIMEOUT):
            raise RuntimeError("the test engine never came up")

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=STARTUP_TIMEOUT)

    def get(self, path: str):
        return self._one("GET", path)

    def post(self, path: str, **kwargs):
        return self._one("POST", path, **kwargs)

    def delete(self, path: str):
        return self._one("DELETE", path)

    def _one(self, method: str, path: str, **kwargs):
        """One request, on its own loop. The answer is a `(status, payload)` pair.

        The body is read *inside* the coroutine because the session closes with it: a response handed out
        of a closed loop is an object whose `.json()` no longer works, which is the shape of bug this
        wrapper exists to prevent rather than to have.
        """

        async def ask():
            async with ClientSession(self.root) as session:
                async with session.request(method, path, **kwargs) as answer:
                    try:
                        body = await answer.json(content_type=None)
                    except ValueError:
                        body = None
                    return answer.status, body

        return asyncio.run(ask())


@pytest.fixture
def engine(monkeypatch):
    """The real app, with the four process-shaped seams replaced by values."""
    quiet = process_module.Levels(mean_dbfs=-30.1, max_dbfs=-27.1, seconds=6.0)

    def fake_probe(path):
        return prober.MediaInfo(
            path=Path(path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=6.0,
            size_bytes=4_000_000,
            audio=prober.AudioStream(codec="aac", sample_rate=48000, channels=2),
            video=prober.VideoStream(
                codec="h264",
                profile="High",
                level=41,
                width=1920,
                height=1080,
                pix_fmt="yuv420p",
                rate=Fraction(25, 1),
                frames=150,
                timebase=Fraction(1, 12800),
                has_b_frames=True,
            ),
            streams=(("video", "h264"), ("audio", "aac")),
        )

    def fake_run(args, **kwargs):
        """A pass that writes its output, so the run's own bookkeeping has something to find."""
        target = Path(args[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"written by the fake")
        return None

    monkeypatch.setattr(server.prober, "probe", fake_probe)
    monkeypatch.setattr(server.prober, "levels", lambda info, timeout=None: quiet)
    monkeypatch.setattr(process_module, "run", fake_run)
    monkeypatch.setattr(normalizer, "run", fake_run, raising=False)
    monkeypatch.setattr(
        normalizer,
        "measure_levels",
        lambda path, timeout=None: process_module.Levels(mean_dbfs=-9.0, max_dbfs=-9.0, seconds=6.0),
    )
    monkeypatch.setattr(normalizer, "capture", lambda *a, **k: (0, "", ""))
    # The run measures the codec by decoding its own stage file, and then *verifies* what it wrote. Both
    # are real processes pointed at a file the fake run filled with four bytes, so both are seams: what
    # these tests are about is the transport, the refusals and the job bookkeeping, not ffmpeg's opinion
    # of a placeholder.
    monkeypatch.setattr(
        normalizer,
        "_measure_decoded_aac",
        lambda stage_file, plan: process_module.Levels(mean_dbfs=-9.0, max_dbfs=-8.4, seconds=6.0),
    )
    monkeypatch.setattr(
        verifier,
        "measure_levels",
        lambda path, timeout=None: process_module.Levels(mean_dbfs=-9.0, max_dbfs=-8.4, seconds=6.0),
    )
    monkeypatch.setattr(verifier.media_module, "probe", fake_probe)
    monkeypatch.setattr(
        verifier,
        "probe_json",
        lambda args, timeout=None: {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}},
    )
    monkeypatch.setattr(verifier.media_module, "stream_duration", lambda path, stream="a:0": 6.0)
    monkeypatch.setattr(verifier.media_module, "packet_times", lambda path: [6.0])

    running = Engine()
    running.start()
    yield running
    running.stop()


def a_file(tmp_path, name="talk.mp4"):
    where = tmp_path / name
    where.write_bytes(b"not really a video, but it is a file")
    return str(where)


def a_request(tmp_path, **overrides):
    payload = {
        "sources": [a_file(tmp_path)],
        "targetDbfs": -6.0,
        "strategy": "gain",
        "makeupDb": 12.0,
        "ceilingDbfs": -6.0,
    }
    payload.update(overrides)
    return payload


def settle(timeout: float = 15.0) -> None:
    """Wait for the worker thread to finish. The engine is synchronous and off the loop."""
    deadline = time.monotonic() + timeout
    while server.CURRENT is not None and time.monotonic() < deadline:
        time.sleep(0.02)


# ---------------------------------------------------------------------------------------
# The small answers
# ---------------------------------------------------------------------------------------


def test_the_index_lists_the_routes_the_app_registers(engine):
    status, payload = engine.get("/api")
    assert status == 200
    assert payload["product"] == "TheNormalizer"
    assert payload["routes"] == server.ROUTES, "the index and the route table are one list"


def test_health_names_this_product(engine):
    """Three of these applications answer `/api/health`, so a warm port must be told apart."""
    status, payload = engine.get("/api/health")
    assert status == 200
    assert payload["product"] == "TheNormalizer"
    for capability in ("aac", "libmp3lame", "loudnorm"):
        assert capability in payload


def test_a_file_that_does_not_exist_is_a_refusal_and_not_a_crash(engine):
    status, payload = engine.get("/api/probe?path=C:/nowhere/absent.mp4")
    assert status == 400
    assert payload["reason"] == "input"
    assert "is not a file" in payload["error"]


def test_probe_says_what_it_measured_and_where_the_output_would_go(engine, tmp_path):
    status, payload = engine.get(f"/api/probe?path={a_file(tmp_path)}")
    assert status == 200
    assert payload["media"]["kind"] == "video"
    assert payload["levels"]["peakText"] == "-27.10 dBFS"
    # The container's extension and not the source's: a `.wav` becomes an `.m4a`, and a suggestion
    # carrying the source's extension would name a file the run does not write.
    assert payload["suggestedOutput"].endswith(f"talk{normalizer.SUFFIX}.mp4")


# ---------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------


def test_the_plan_names_the_output_the_container_and_the_gain(engine, tmp_path):
    status, payload = engine.post("/api/normalization-plans", json=a_request(tmp_path))
    assert status == 200
    plan = payload["plan"]
    assert plan["output"].endswith(f"talk{normalizer.SUFFIX}.mp4")
    assert plan["container"] == ".mp4"
    # The target's own arithmetic and nothing else, because `gain` has no chain to drive: −6.0 from a
    # source at −27.1 is +21.1 dB, and the make-up field does not touch a run with no chain in it.
    assert plan["gainDb"] == 21.1
    assert plan["gainIsMeasured"] is False, "nothing has run, so the gain is the plan's arithmetic"
    assert payload["outputExists"] is False


def test_the_plan_states_the_chain_and_the_two_figures_the_operator_owns(engine, tmp_path):
    status, payload = engine.post(
        "/api/normalization-plans",
        json=a_request(tmp_path, strategy="chain", makeupDb=12.0, ceilingDbfs=-6.0),
    )
    assert status == 200
    plan = payload["plan"]
    assert plan["makeupDb"] == 12.0
    assert plan["ceilingDbfs"] == -6.0
    assert "+12.0 dB" in plan["chainNote"]
    assert "makeup=1" in plan["filterGraph"], "the drive is the fader, not the compressor's option"


def test_a_request_with_no_source_is_refused_by_name(engine):
    status, payload = engine.post("/api/normalization-plans", json={"targetDbfs": -6})
    assert status == 400
    assert "sources" in payload["error"]


def test_a_source_that_is_not_a_file_is_refused_by_name(engine):
    status, payload = engine.post(
        "/api/normalization-plans", json={"sources": ["C:/nowhere/x.mp4"]}
    )
    assert status == 400
    assert "is not a file" in payload["error"]


@pytest.mark.parametrize(
    "malformed",
    [b"", b"[1,2,3]", b'"a string"', b"null", b"12", b"{not json}", b'{"sources":'],
)
def test_a_body_that_is_not_a_request_is_a_400_and_not_a_500(engine, malformed):
    """A `500` says the server broke, when what happened is the caller sent something that is not a
    request."""
    status, _ = engine.post("/api/normalization-plans", data=malformed)
    assert status == 400, malformed


def test_an_unknown_strategy_is_refused_with_the_ones_there_are(engine, tmp_path):
    status, payload = engine.post(
        "/api/normalization-plans", json=a_request(tmp_path, strategy="loudnorm")
    )
    assert status == 400
    assert "gain" in payload["error"]


def test_a_format_nothing_can_write_is_refused_by_name(engine, tmp_path):
    status, payload = engine.post(
        "/api/normalization-plans", json=a_request(tmp_path, audio=[".flac"])
    )
    assert status == 400
    assert ".flac" in payload["error"]


def test_the_mp3_without_the_wav_is_refused_before_anything_is_written(engine, tmp_path):
    status, payload = engine.post(
        "/api/normalization-plans", json=a_request(tmp_path, audio=[".mp3"])
    )
    assert status == 400
    assert "encoded from" in payload["error"]


def test_the_sound_files_the_plan_names_are_the_ones_a_run_would_write(engine, tmp_path):
    status, payload = engine.post(
        "/api/normalization-plans", json=a_request(tmp_path, audio=[".wav", ".mp3"])
    )
    assert status == 200
    files = payload["plan"]["audio"]["files"]
    assert [entry["name"] for entry in files] == [
        f"talk{normalizer.SUFFIX}.wav",
        f"talk{normalizer.SUFFIX}.mp3",
    ]
    assert all(entry["bytes"] > 0 for entry in files)


# ---------------------------------------------------------------------------------------
# The batch
# ---------------------------------------------------------------------------------------


def test_nothing_is_running_when_nothing_has_been_started(engine):
    assert engine.get("/api/normalizations/current")[0] == 204


def test_cancelling_nothing_is_an_answer(engine):
    status, payload = engine.delete("/api/normalizations/current")
    assert status == 200
    assert payload["cancelled"] is False


def test_a_batch_is_started_and_answers_with_a_job_per_source(engine, tmp_path):
    first, second = a_file(tmp_path, "a.mp4"), a_file(tmp_path, "b.mp4")
    status, payload = engine.post(
        "/api/normalizations", json=a_request(tmp_path, sources=[first, second])
    )
    assert status == 201
    assert payload["started"] is True
    assert [job["source"] for job in payload["jobs"]] == [first, second]
    outputs = [job["output"] for job in payload["jobs"]]
    assert len(set(outputs)) == 2, "two sources get two names"
    settle()


def test_a_second_batch_while_one_runs_is_refused_with_busy(engine, tmp_path):
    """One batch at a time: two ffmpeg processes on one disk are not twice as fast, and the second
    one's progress would make the first one's bar a lie."""
    engine.post("/api/normalizations", json=a_request(tmp_path))
    status, payload = engine.post(
        "/api/normalizations", json=a_request(tmp_path, sources=[a_file(tmp_path, "c.mp4")])
    )
    assert status in (201, 409)
    if status == 409:
        assert payload["reason"] == "busy"
    settle()


def test_the_batch_reports_a_job_per_file_when_it_finishes(engine, tmp_path):
    engine.post("/api/normalizations", json=a_request(tmp_path))
    settle()
    kinds = [event["type"] for event in server.HUB._history]
    for expected in ("batch-started", "job-started", "job-finished", "batch-finished"):
        assert expected in kinds, expected
    finished = next(e for e in server.HUB._history if e["type"] == "batch-finished")
    assert finished["done"] == 1 and finished["failed"] == 0


def test_a_batch_of_several_files_given_one_output_path_is_refused(engine, tmp_path):
    """The Save dialog's answer is one path for one file. Several files cannot share it, and saying so
    is better than silently writing them all beside their sources."""
    status, payload = engine.post(
        "/api/normalizations",
        json=a_request(
            tmp_path,
            sources=[a_file(tmp_path, "a.mp4"), a_file(tmp_path, "b.mp4")],
            output=str(tmp_path / "one.mp4"),
        ),
    )
    assert status == 400
    assert "one output path" in payload["error"]


# ---------------------------------------------------------------------------------------
# The two passes, which is where this engine's shape lives
# ---------------------------------------------------------------------------------------


def test_the_stage_and_master_passes_are_one_operating_point():
    """The master runs the chain at the drive the stage measured it at, and corrects *after* the limiter.

    That is the whole arrangement, and both halves of it were learned the hard way. A stage that ran *dry*
    measured a gain rather than an overshoot — its `chain_peak` was the chain's transparent output while
    its decoded peak came from an encode through the fader — and the correction built from the difference
    drove the master to the wrong level. And a chain run whose correction was folded into the *front* gain
    delivered a decibel low on every file, because a limiter clamps whatever reaches it: a correction in
    front of a limiter is not a correction, it is a change of drive.

    So a chain run's master front gain is the stage's own, unchanged, and the correction is the **out
    fader** — the one element this product places after a limiter, safely, because the limiter has already
    bounded what reaches it.
    """
    from conftest import a_plan, a_video

    plan = a_plan(a_video(), strategy="chain", makeup_db=12.0, ceiling_dbfs=-6.0)
    assert normalizer.sound_graph(plan, "stage") == normalizer.sound_graph(plan, "master")
    assert plan.chain_out_gain_db == 0.0, "nothing has been measured, so there is nothing to correct"

    measured = plan.with_measurements(-15.1, -14.5)
    assert measured.overshoot_db == -0.6, "the decoder overshot the samples by 0.6 dB"
    assert measured.master_gain_db == measured.stage_gain_db, "the drive is the one that was measured"
    assert measured.chain_out_gain_db == -6.0 - -14.5, "the out fader is the gap to the target"
    assert measured.total_gain_db == measured.stage_gain_db
    # And the master's graph is the stage's with the out fader filled in, whatever that figure is.
    assert "volume=0.000000dB" in normalizer.sound_graph(measured, "stage")
    assert f"volume={measured.chain_out_gain_db:.6f}dB" in normalizer.sound_graph(measured, "master")
    assert measured.gain_is_measured is True
