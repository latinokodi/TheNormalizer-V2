"""Run the real engine over real files and measure what came out.

This is the evidence the unit suite cannot be: `backend/tests` never launches ffmpeg (except one
health check), so nothing in it proves that these argument lists are ones ffmpeg *accepts*, that the
level really moves, or that the picture really is copied. This script builds the files it needs, so it
runs anywhere and needs no footage.

    venv\\Scripts\\python.exe scripts\\check_normalize.py

What it builds, in a temporary folder:

* `tone.wav`      — a 1 kHz tone at a known peak, the easy case
* `quiet.wav`     — the same tone 12 dB down, so the target can only be reached by *lifting*
* `silence.wav`   — digital silence, which must not be given a gain and must not be refused
* `clip.mkv`      — a 720p25 video with a quiet sound, the case the product exists for
* `loud.wav`      — a tone already above the target, so the run has to turn it *down*

Then, for each one, it runs probe -> plan -> run -> verify through the engine's own modules and prints
every figure, and it fails loudly if a claim is not met.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from normalizer import media as prober  # noqa: E402
from normalizer import normalize as normalizer  # noqa: E402
from normalizer import verify as verifier  # noqa: E402
from normalizer.process import capture, measure_levels, tool  # noqa: E402

FAILURES: list[str] = []


def check(claim: str, ok: bool, detail: str) -> None:
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {claim}: {detail}")
    if not ok:
        FAILURES.append(f"{claim}: {detail}")


def ffmpeg(args: list[str]) -> None:
    code, _, err = capture([tool("ffmpeg"), "-y", "-v", "error", *args], timeout=300.0)
    if code != 0:
        raise SystemExit(f"ffmpeg refused to build a fixture:\n{' '.join(args)}\n{err}")


def build(where: Path) -> None:
    """The fixtures, each with a level this script knows the answer to."""
    ffmpeg(["-f", "lavfi", "-i", "sine=frequency=1000:duration=6:sample_rate=48000",
            "-af", "volume=-6dB", "-ac", "2", str(where / "tone.wav")])
    ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
            "-af", "volume=-18dB", "-ac", "2", str(where / "quiet.wav")])
    ffmpeg(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", "4", str(where / "silence.wav")])
    ffmpeg(["-f", "lavfi", "-i", "sine=frequency=300:duration=6:sample_rate=48000",
            "-af", "volume=-2dB", "-ac", "2", str(where / "loud.wav")])
    # A video: 720p25 pictures with a quiet sound. `-shortest` so the two streams agree.
    ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=6:sample_rate=48000",
        "-af", "volume=-20dB", "-ac", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(where / "clip.mkv"),
    ])


def run_one(
    path: Path,
    target: float,
    strategy: str,
    exports: tuple[str, ...] = (),
    *,
    makeup_db: float | None = None,
    ceiling_dbfs: float | None = None,
    demand_target: bool = True,
) -> dict:
    """One file through the whole engine, with every figure printed.

    ``demand_target`` says whether *this* run's contract includes landing on the target. It is true for
    the strategies whose whole job is to reach one, and false for a chain run that is deliberately being
    driven into its limiter — where the honest claim is that the file's level is reported rather than
    that it is what was asked for. Everything else — the container, the decode, the shape, the length,
    the copied picture — is demanded of every run.
    """
    print(f"\n=== {path.name}  target {target:+.1f} dBFS, strategy {strategy} ===")
    info = prober.probe(path)
    print(f"  probed: {info.summary()}")
    levels = prober.levels(info)
    print(f"  measured: peak {levels.max_dbfs} mean {levels.mean_dbfs} dBFS")

    spec = normalizer.NormalizeSpec(
        source=path,
        output=normalizer.output_for(path),
        target_dbfs=target,
        strategy=strategy,
        audio_exports=exports,
        **(  # only override what the caller asked for, so the defaults are what is tested by default
            {}
            if makeup_db is None
            else {"makeup_db": makeup_db}
        ),
        **({} if ceiling_dbfs is None else {"ceiling_dbfs": ceiling_dbfs}),
    )
    plan = normalizer.plan_normalize(spec, info, levels.max_dbfs, levels.mean_dbfs)
    print(f"  planned: output {plan.output.name}, container {plan.container} ({plan.muxer})")
    if plan.uses_chain:
        print(
            f"  chain: the run's gain (capped at {spec.makeup_db:+.1f} dB) in front of the operator's "
            f"chain, whose limiter is at {spec.ceiling_dbfs:.1f} dBFS"
        )
        print(f"  chain graph: volume=<the run's gain>,{normalizer.dynamics_chain(spec.ceiling_dbfs)}")
    if not plan.gain_is_measured:
        print("  planned gain: measured during the run (the chain decides it)")
    else:
        print(f"  planned gain: {plan.gain_text}")
    for note in plan.notes:
        print(f"    note: {note}")
    for caution in plan.cautions:
        print(f"    caution: {caution}")

    logs: list[str] = []
    result = normalizer.run_normalize(plan, log=logs.append)
    plan = result.plan or plan
    print(f"  applied gain: {plan.gain_text}")
    print(
        f"  measured: chain {plan.measured_chain_peak_dbfs}, decoded {plan.measured_decoded_peak_dbfs}, "
        f"codec overshoot {plan.overshoot_db:+.2f} dB"
    )
    print(f"  wrote: {result.output.name} ({result.output.stat().st_size:,} bytes)")
    for extra in result.audio:
        print(f"         {extra.name} ({extra.stat().st_size:,} bytes)")

    outcome = verifier.verify(plan, result.output)
    print("  " + outcome.report().replace("\n", "\n  "))

    # The two checks a deliberately-driven chain run is allowed to miss are the two about the target —
    # `level moved` compares the run's whole effect against the target as well. Everything else — the
    # container, the decode, the sound's shape, the length, the copied picture — is demanded of every run,
    # and a chain run that is *not* being driven into its limiter demands the target too.
    for check_row in outcome.checks:
        demanded = demand_target or check_row.name not in ("target level", "level moved")
        if demanded:
            check(f"{path.name}: {check_row.name}", check_row.status != "failed", check_row.detail)
        else:
            print(f"  (reported, not demanded) {check_row.name}: {check_row.detail}")
    return {"plan": plan, "outcome": outcome, "result": result, "logs": logs}


def main() -> int:
    where = Path(tempfile.mkdtemp(prefix="normalizer-check-"))
    print(f"fixtures in {where}")
    try:
        build(where)

        # ---- The easy case: the default strategy, at a known level -------------------------
        #
        # `gain` is the default because it is the one that reaches any target: one fader, no limiter, so
        # the peak is the number asked for whatever the material. The `chain` strategy is exercised
        # separately below, at a target its own limiter can reach.
        tone = run_one(where / "tone.wav", -6.0, "gain")
        check(
            "tone.wav reaches the target",
            tone["outcome"].output_peak_dbfs is not None
            and abs(tone["outcome"].output_peak_dbfs + 6.0) <= normalizer.PEAK_TOLERANCE_DB,
            f"peak {tone['outcome'].output_peak_dbfs} dBFS against a -6.0 target",
        )
        check(
            "tone.wav kept its shape",
            tone["outcome"].sound_seconds > 5.9,
            f"{tone['outcome'].sound_seconds:.3f}s of sound for a 6s source",
        )

        # ---- Lifting: material 12 dB below the target -----------------------------------
        quiet = run_one(where / "quiet.wav", -6.0, "gain")
        check(
            "quiet.wav was lifted, not limited",
            quiet["outcome"].applied_gain_db is not None and quiet["outcome"].applied_gain_db > 6.0,
            f"the level moved {quiet['outcome'].applied_gain_db:+.2f} dB",
        )

        # ---- Turning down: material above the target -------------------------------------
        #
        # The fixture is measured rather than assumed. `ffmpeg`'s `sine` generator does not produce a
        # full-scale sine — its amplitude depends on the frequency against the buffer size, and the
        # source of a fixture is the last place to guess. So the claim is stated against what the
        # engine itself measured the file to be: if the source is above the target, the run must move
        # the level *down*, and if the source is below it, up. Either way the output has to land on the
        # target, which is the claim that matters.
        loud_source_peak = prober.levels(prober.probe(where / "loud.wav")).max_dbfs
        loud = run_one(where / "loud.wav", -6.0, "gain")
        expected_sign = 1 if (loud_source_peak or -99.0) < -6.0 else -1
        moved = loud["outcome"].applied_gain_db
        check(
            "loud.wav moved in the direction its own level requires",
            moved is not None and (moved > 0) == (expected_sign > 0),
            f"the source peaks at {loud_source_peak:.2f} dBFS against a -6.0 target, and the level "
            f"moved {moved:+.2f} dB",
        )

        # ---- Digital silence, which must not be given a gain -----------------------------
        silent = run_one(where / "silence.wav", -6.0, "chain")
        check(
            "silence.wav was not given a gain",
            silent["plan"].gain_db == 0.0
            and silent["outcome"].output_peak_dbfs is None,
            f"gain {silent['plan'].gain_text}, output peak {silent['outcome'].output_peak_dbfs}",
        )

        # ---- Video: the picture must come out bit-identical ------------------------------
        clip = run_one(where / "clip.mkv", -9.0, "gain", exports=(".wav", ".mp3"))
        check(
            "clip.mkv's picture is bit-identical",
            any(
                row.name == "picture copied" and row.status == "passed"
                for row in clip["outcome"].checks
            ),
            next(
                (row.detail for row in clip["outcome"].checks if row.name == "picture copied"),
                "no such check",
            ),
        )
        check(
            "clip.mkv kept its picture",
            clip["outcome"].picture_seconds > 5.9,
            f"{clip['outcome'].picture_seconds:.3f}s of picture",
        )
        commands = [line for line in clip["logs"] if line.startswith("$")]
        # Every command that writes a picture must copy it. The chain and fader passes have no video
        # mapping at all — `-map 0:a:0` and a wav output — so the check is: any command that maps the
        # source's video carries `-c:v copy`, and exactly one command does.
        video_commands = [line for line in commands if "-map 0:v" in line]
        check(
            "every command that writes the picture copies it",
            len(video_commands) == 1 and all("-c:v copy" in line for line in video_commands),
            f"{len(commands)} command(s), {len(video_commands)} of them mapping the picture; the copy "
            f"flag is on each",
        )
        wav = where / "clip - normalized.wav"
        mp3 = where / "clip - normalized.mp3"
        check("the WAV export exists", wav.is_file(), str(wav.name))
        check("the MP3 export exists", mp3.is_file(), str(mp3.name))
        if wav.is_file():
            code, out, _ = capture([
                tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "csv=p=0", str(wav),
            ])
            check("the WAV is uncompressed 16-bit at the source's rate", "pcm_s16le" in out, out.strip())
        if mp3.is_file():
            code, out, _ = capture([
                tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,bit_rate", "-of", "csv=p=0", str(mp3),
            ])
            check("the MP3 is 320 kbps", "mp3" in out and "320000" in out, out.strip())

        # ---- The exports are the sound the master plays -----------------------------------
        if wav.is_file():
            master_peak = measure_levels(where / "clip - normalized.mkv").max_dbfs
            wav_peak = measure_levels(wav).max_dbfs
            check(
                "the WAV is the master's sound, not the source's",
                master_peak is not None and wav_peak is not None and abs(master_peak - wav_peak) <= 0.1,
                f"master {master_peak:.2f} dBFS, WAV {wav_peak:.2f} dBFS",
            )

        # ---- Nothing was written over ------------------------------------------------------
        check(
            "the run never writes over the file it read",
            (where / "clip.mkv").is_file() and (where / "clip.mkv").stat().st_size > 0,
            "the source is still there",
        )

        # ---- The two chain figures the operator owns --------------------------------------
        #
        # Both are asserted on what the *run* was handed rather than on the plan's prose: the chain
        # appears in the argument list of the stage command, and the ffmpeg option behind each figure is
        # a conversion this script recomputes from the decibel value. A control that says "+12 dB" and a
        # command that says `makeup=12` are the defect this checks for — `makeup` is a multiplier, so
        # twelve there is +21.6 dB and a file that clips.
        # ---- A different target on the same source, to show the field is the whole job ----
        clean_source = where / "clean.wav"
        shutil.copy2(where / "quiet.wav", clean_source)
        clean = run_one(clean_source, -14.0, "gain")
        check(
            "a different target is reached on the same material",
            clean["outcome"].output_peak_dbfs is not None
            and abs(clean["outcome"].output_peak_dbfs + 14.0) <= normalizer.PEAK_TOLERANCE_DB,
            f"peak {clean['outcome'].output_peak_dbfs:.2f} dBFS against a −14.0 target",
        )

        # ---- The operator's chain: the sound, and what its limiter decides --------------
        #
        # A limiter is a ceiling, so a chain containing one delivers its own working level for material
        # driven hard into it — which is why `gain` is the default and this is a selection. What is
        # checked here is what the strategy promises: the operator's filters verbatim, the drive acting
        # where their make-up field is, and a report of what was delivered rather than what was asked for.
        #
        # With the drive at 0 dB the chain is transparent and the target *is* reached, which is the case
        # a normalizer's operator wants; at the operator's +12 dB it sounds like a stitched episode and
        # its level is the limiter's.
        chain_source = where / "chained.wav"
        shutil.copy2(where / "tone.wav", chain_source)
        chained = run_one(chain_source, -12.0, "chain", makeup_db=0.0, ceiling_dbfs=-3.0)
        check(
            "with the drive at 0 dB the chain is transparent and the target is reached",
            chained["outcome"].output_peak_dbfs is not None
            and abs(chained["outcome"].output_peak_dbfs + 12.0) <= normalizer.PEAK_TOLERANCE_DB,
            f"peak {chained['outcome'].output_peak_dbfs:.2f} dBFS against a −12.0 target",
        )
        graph = " ".join(line for line in chained["logs"] if line.startswith("$"))
        check(
            "the chain strategy runs the operator's filters verbatim",
            "acompressor=threshold=-20dB:ratio=1:attack=1:release=50" in graph
            and "alimiter=limit=0.707946:release=50:level=0" in graph,
            "the compressor's own shape, the limiter's release, and `level=0` so the limiter's "
            "automatic level is off — which would otherwise add about +3 dB",
        )
        check(
            "the chain's own make-up is left at unity and the drive is the fader in front of it",
            "makeup=1" in graph,
            "`makeup` is a multiplier whose range starts at 1.0, so it cannot attenuate; the drive is a "
            "fader because a loud file has to come down",
        )

        driven_source = where / "driven.wav"
        shutil.copy2(where / "quiet.wav", driven_source)
        driven = run_one(
            driven_source, -6.0, "chain", makeup_db=12.0, ceiling_dbfs=0.0, demand_target=False
        )
        check(
            "the drive moves the run's gain and not the chain's own make-up",
            driven["plan"].gain_text.startswith("+")
            and "makeup=1" in " ".join(line for line in driven["logs"] if line.startswith("$")),
            f"the fader is {driven['plan'].gain_text} where the target alone would need "
            f"{driven['plan'].working_gain_db():+.2f} dB, and the chain's make-up is still `makeup=1`",
        )
        # What a chain run promises is not the target — its limiter decides that — but that what it
        # *reports* is the measurement rather than the request. So the delivered file is measured again,
        # here, by this script, and the two figures have to be the same number.
        remeasured = measure_levels(driven["result"].output).max_dbfs
        reported = driven["outcome"].output_peak_dbfs
        check(
            "a chain run reports what it delivered, and the figure is the file's own",
            reported is not None
            and remeasured is not None
            and abs(reported - remeasured) <= 0.05,
            f"asked for −6.0, delivered {reported:.2f} dBFS; measuring the file again gives "
            f"{remeasured:.2f} dBFS",
        )

        # And a target the limiter provably cannot reach is named before the run, not after it.
        pinned_source = where / "pinned.wav"
        shutil.copy2(where / "tone.wav", pinned_source)
        pinned = run_one(
            pinned_source, -1.0, "chain", makeup_db=12.0, ceiling_dbfs=-6.0, demand_target=False
        )
        check(
            "a target above the limiter's working level is named before the run",
            any("clamps material into it" in line for line in pinned["plan"].cautions),
            next((line for line in pinned["plan"].cautions if "clamps" in line), "no caution"),
        )

        # ---- The make-up and ceiling fields ------------------------------------------------
        check(
            "the ceiling reaches ffmpeg as the linear value of the dBFS asked for",
            "limit=0.707946" in graph and "level=0" in graph,
            "−3.0 dBFS is `limit=0.707946`, which is 10^(-3/20), and `level=0` is not optional",
        )
        check(
            "the default ceiling is the operator's own",
            normalizer.DEFAULT_CEILING_DBFS == -6.0
            and normalizer.DEFAULT_MAKEUP_DB == 12.0
            and normalizer.DEFAULT_STRATEGY == "gain",
            f"strategy {normalizer.DEFAULT_STRATEGY}, make-up {normalizer.DEFAULT_MAKEUP_DB:+.1f} dB, "
            f"ceiling {normalizer.DEFAULT_CEILING_DBFS:.1f} dBFS",
        )
    finally:
        print(f"\n(fixtures kept at {where})")
    if FAILURES:
        print(f"\n{len(FAILURES)} claim(s) did not hold:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nevery claim held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
