"""The media layer, the plan, and the commands it builds.

Every test here is arithmetic, argument lists, or a monkeypatched seam. Nothing launches ffmpeg and
nothing needs footage, which is what makes the suite run in about a second — and it is also its
weakness: `scripts/check_normalize.py` is where these argument lists are proved to be ones ffmpeg
accepts, on files it builds itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import a_plan, a_spec, a_video, an_audio
from normalizer import media as prober
from normalizer import normalize as normalizer
from normalizer import process as process_module
from normalizer.media import _parse_framemd5

#: Where the tests put the files they never create. A path is a value here, not a place.
WORK = Path("C:/work")

# ---------------------------------------------------------------------------------------
# What a file is
# ---------------------------------------------------------------------------------------


def test_an_audio_file_is_a_file_with_a_sound_and_no_picture():
    info = an_audio()
    assert info.kind == "audio"
    assert not info.has_picture
    assert info.audio is not None
    assert info.audio.sample_rate == 48000
    assert "48" not in info.summary() or "48000" in info.summary()


def test_a_video_file_is_a_file_with_both():
    info = a_video()
    assert info.kind == "video"
    assert info.has_picture
    assert info.frames == 150
    assert info.rate == 25


def test_silence_is_not_a_very_quiet_signal():
    """`volumedetect` prints `-inf` for digital silence, and this turns it into `None`.

    A sentinel like `-1000.0` would be a number somebody eventually does arithmetic on — an +85 dB gain
    applied to a file of nothing, and reported as normalization.
    """
    assert process_module._dbfs("-inf") is None
    assert process_module._dbfs("-91.0") is None  # 1 LSB in a 16-bit word, which is silence
    assert process_module._dbfs("-6.0") == -6.0
    assert process_module._dbfs("-0.1") == -0.1


def test_the_volume_pattern_reads_ffmpeg_s_own_spacing():
    """The figures arrive as `mean_volume: -30.1 dB`, with a space, from a `%6.1f`.

    A pattern without `\\s*` after the colon matches nothing on a real log and the failure it produces
    is this module's own sentence about ffmpeg not reporting a level — which reads as a broken ffmpeg.
    """
    log = """
    [Parsed_volumedetect_0 @ 0x1] n_samples: 576000
    [Parsed_volumedetect_0 @ 0x1] mean_volume: -30.1 dB
    [Parsed_volumedetect_0 @ 0x1] max_volume: -27.1 dB
    [Parsed_volumedetect_0 @ 0x1] histogram_27db: 168000
    """
    found = {f"{name}_volume": process_module._dbfs(value) for name, value in process_module._VOLUME.findall(log)}
    assert found == {"mean_volume": -30.1, "max_volume": -27.1}


def test_framemd5_rows_are_parsed_by_pts():
    """A data row is six fields, and the first version of this required seven.

    The effect was that every row was dropped and the frame check reported "no presentation time was
    common to both decodes" — a sentence that reads like a container problem and was a miscounted list.
    """
    text = (
        "#format: frame checksums\n"
        "#stream#, dts,        pts, duration,     size, hash\n"
        "0,          1,          1,        1,  1382400, 02bae04ba7383104d56d5686e5f20588\n"
        "0,          2,          2,        1,  1382400, 0e4f98ffd7ff7eec9adfaccba972ebff\n"
    )
    rows = _parse_framemd5(text)
    assert rows == [
        "0:1:02bae04ba7383104d56d5686e5f20588",
        "0:2:0e4f98ffd7ff7eec9adfaccba972ebff",
    ]
    assert rows[0].split(":")[1] == "1", "the second field is the pts, and it is an integer"




# ---------------------------------------------------------------------------------------
# What the plan refuses
# ---------------------------------------------------------------------------------------


def test_a_file_with_no_sound_is_refused():
    silent = prober.MediaInfo(
        path=Path("C:/media/mute.mp4"),
        container="mov,mp4",
        duration=10.0,
        size_bytes=1000,
        audio=None,
        video=a_video().video,
    )
    with pytest.raises(normalizer.NotNormalizable, match="nothing to normalize"):
        normalizer.plan_normalize(a_spec(silent.path), silent, None, None)


def test_a_file_with_no_duration_is_refused():
    from dataclasses import replace

    info = replace(an_audio(), duration=0.0)
    with pytest.raises(normalizer.NotNormalizable, match="does not declare a duration"):
        normalizer.plan_normalize(a_spec(), info, -6.0, -9.0)


def test_a_strategy_that_does_not_exist_is_refused_by_name():
    with pytest.raises(normalizer.InputRefused, match="not a normalizing strategy"):
        a_plan(strategy="loudnorm")


def test_a_target_outside_the_range_is_refused_with_the_range():
    with pytest.raises(normalizer.InputRefused, match="outside the range"):
        a_plan(target_dbfs=6.0)
    with pytest.raises(normalizer.InputRefused, match="outside the range"):
        a_plan(target_dbfs=-40.0)


def test_a_ceiling_outside_the_range_is_refused_with_the_range():
    with pytest.raises(normalizer.InputRefused, match="not a ceiling"):
        a_plan(ceiling_dbfs=3.0)
    with pytest.raises(normalizer.InputRefused, match="outside the range"):
        a_plan(ceiling_dbfs=-40.0)


def test_a_format_nothing_can_write_is_refused_by_name():
    with pytest.raises(normalizer.InputRefused, match=r"\.flac"):
        a_plan(audio_exports=(".flac",))


def test_the_mp3_alone_is_refused_because_it_is_encoded_from_the_wav():
    with pytest.raises(normalizer.InputRefused, match="encoded from"):
        a_plan(audio_exports=(".mp3",))


def test_the_mp3_with_the_wav_is_accepted():
    plan = a_plan(audio_exports=(".wav", ".mp3"))
    assert [path.suffix for path in plan.audio_paths] == [".wav", ".mp3"]


def test_the_plan_does_not_touch_the_disk(tmp_path):
    """`plan_normalize` is pure: it creates no file and starts no process.

    That is what makes it safe to call as the operator moves a slider, which is what lets the window
    state the gain and the warnings before anyone commits to a run.
    """
    source = tmp_path / "talk.wav"
    spec = a_spec(source, makeup_db=0.0)
    info = an_audio(source)
    plan = normalizer.plan_normalize(spec, info, -27.1, -30.1)
    assert plan.gain_db == pytest.approx(21.1, abs=0.01)
    assert list(tmp_path.iterdir()) == [], "planning wrote something"


# ---------------------------------------------------------------------------------------
# The gain
# ---------------------------------------------------------------------------------------


def test_the_default_strategy_reaches_any_target():
    """`gain` has no limiter in it, so its gain is the target's arithmetic and nothing else.

    The `chain` strategy adds the operator's drive on top of this, which is why it cannot reach every
    target and why that one is not the default. See the drive test below.
    """
    for peak in (-40.0, -27.1, -12.0, -3.0):
        plan = a_plan(peak=peak, target_dbfs=-6.0, makeup_db=0.0)
        assert plan.spec.strategy == "gain"
        assert plan.gain_db == pytest.approx(-6.0 - peak, abs=0.01), f"peak {peak}"


def test_gaining_a_silent_file_applies_nothing():
    plan = a_plan(peak=None, mean=None)
    assert plan.gain_db == 0.0
    assert any("digital silence" in note for note in plan.notes)


def test_the_chain_carries_the_operator_s_drive_in_front_of_it():
    """The drive is the fader, not the compressor's `makeup` — which cannot go below 1.0.

    A source that is too loud for the target has to come *down*, and the only element that can do that
    is a fader in front of the chain.
    """
    with_drive = a_plan(peak=-27.1, target_dbfs=-6.0, strategy="chain", makeup_db=12.0)
    without = a_plan(peak=-27.1, target_dbfs=-6.0, strategy="chain", makeup_db=0.0)
    assert with_drive.gain_db - without.gain_db == pytest.approx(12.0, abs=0.001)


def test_a_target_the_limiter_cannot_reach_is_a_caution_before_the_run():
    plan = a_plan(peak=-27.1, target_dbfs=-3.0, strategy="chain", ceiling_dbfs=-6.0)
    assert any("clamps material into it" in caution for caution in plan.cautions)
    assert plan.expected_peak_dbfs == -3.0, "nothing has been measured yet, so the target stands"


# ---------------------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------------------


def command_with(plan, needle: str) -> list[str]:
    for command in normalizer.normalize_commands(plan, Path("C:/work")):
        if needle in " ".join(command.args):
            return command.args
    raise AssertionError(f"no command mentions {needle!r}")


def test_the_picture_is_always_copied():
    """Every command that maps the source's picture carries `-c:v copy`, and there is exactly one."""
    plan = a_plan(a_video())
    commands = normalizer.normalize_commands(plan, Path("C:/work"))
    mapping_video = [c for c in commands if "0:v" in c.args]
    assert len(mapping_video) == 1, "one command writes the picture"
    assert "-c:v" in mapping_video[0].args
    assert mapping_video[0].args[mapping_video[0].args.index("-c:v") + 1] == "copy"


def test_the_sound_is_always_re_encoded():
    for strategy in ("gain", "chain", "ceiling"):
        plan = a_plan(a_video(), strategy=strategy)
        mux = normalizer.master_command(plan, Path("C:/work/master.wav"))
        assert "-c:a" in mux.args
        assert mux.args[mux.args.index("-c:a") + 1] == "aac", strategy


def test_the_muxer_is_named_and_not_inferred_from_the_extension():
    """ffmpeg picks its muxer from the extension and will accept a codec the container cannot frame.

    That is how a WAV full of ADTS AAC gets written and called a success — `format_name: wav`, one
    `aac` stream, the right duration, and a first decode that fails with `decode_band_types: Input buffer
    exhausted before END element found`.
    """
    plan = a_plan()
    mux = normalizer.master_command(plan, Path("C:/work/master.wav"))
    assert "-f" in mux.args
    assert mux.args[mux.args.index("-f") + 1] == plan.muxer
    assert plan.muxer == "ipod" and plan.container == ".m4a", "a .wav source is written as .m4a"


def test_a_webm_source_is_written_as_matroska():
    """A container that cannot hold AAC beside the streams it has is not a refusal — it is a new name."""
    from dataclasses import replace

    info = replace(a_video(), path=Path("C:/media/clip.webm"))
    plan = a_plan(info)
    assert plan.container == ".mkv"
    assert plan.muxer == "matroska"
    assert any("will not carry AAC" in caution for caution in plan.cautions)


def test_the_sound_is_fetched_from_the_fader_and_the_picture_from_the_source():
    plan = a_plan(a_video())
    mux = normalizer.master_command(plan, Path("C:/work/master.wav"))
    assert mux.args.count("-i") == 2
    assert str(plan.source.path) in mux.args
    assert "C:\\work\\master.wav" in mux.args or "C:/work/master.wav" in mux.args
    assert mux.args[mux.args.index("-map") + 1] == "0:v", "the picture comes from the source"
    assert "1:a:0" in mux.args, "the sound comes from the fader's file"


def test_the_chain_graph_holds_the_operator_s_chain_and_nothing_else():
    plan = a_plan(strategy="chain", ceiling_dbfs=-6.0)
    graph = normalizer.sound_graph(plan)
    assert "acompressor=threshold=-20dB:ratio=1:attack=1:release=50" in graph
    assert "alimiter=limit=0.501187:release=50:level=0" in graph
    for forbidden in ("dynaudnorm", "loudnorm", "aresample", "pan=", "equalizer"):
        assert forbidden not in graph, forbidden


def test_the_compressor_s_makeup_is_left_at_unity_and_the_drive_is_the_fader():
    """`makeup` is a MULTIPLIER and cannot go below 1.0, so the drive is a `volume` in front.

    Writing `makeup=12` asking for twelve decibels is the defect this pins: it is +21.6 dB, and the
    chain's limiter would then be what decided the file's level rather than the target.
    """
    plan = a_plan(strategy="chain", makeup_db=12.0, ceiling_dbfs=-6.0)
    graph = normalizer.sound_graph(plan)
    assert "makeup=1," in graph or graph.endswith("makeup=1,alimiter=limit=0.501187:release=50:level=0")
    assert graph.startswith("volume="), "the run's gain is in front of the chain"


def test_the_limiter_s_automatic_level_is_off():
    """`level=1` adds about +3 dB on top of the ceiling, by an amount that depends on the material."""
    plan = a_plan(strategy="chain")
    assert "level=0" in normalizer.sound_graph(plan)


def test_no_command_reaches_for_a_filter_this_product_did_not_promise():
    forbidden = ("dynaudnorm", "loudnorm", "ebur128", "aresample=async", "speechnorm")
    for strategy in ("gain", "chain", "ceiling"):
        plan = a_plan(strategy=strategy)
        for command in normalizer.normalize_commands(plan, Path("C:/work")):
            joined = " ".join(command.args)
            for name in forbidden:
                assert name not in joined, f"{strategy} reached for {name}"


def test_the_wav_export_is_uncompressed_16_bit_and_carries_no_picture():
    plan = a_plan(audio_exports=(".wav",))
    command = normalizer.wav_command(plan, Path("C:/work/x.part.m4a"), Path("C:/work/x.wav"))
    assert command.args[command.args.index("-c:a") + 1] == "pcm_s16le"
    assert "-vn" in command.args
    assert "-t" in command.args, "the export is cut to the plan's length, not the container's"


def test_the_mp3_is_320_kbps_and_is_encoded_from_the_wav():
    plan = a_plan(audio_exports=(".wav", ".mp3"))
    exports = normalizer.sound_plan(plan)
    assert [export.path.suffix for export in exports] == [".wav", ".mp3"]
    wav, mp3 = exports
    assert wav.command is not None and mp3.command is not None
    assert str(mp3.command.args[mp3.command.args.index("-i") + 1]) == str(wav.path)
    assert mp3.command.args[mp3.command.args.index("-b:a") + 1] == "320k"


def test_no_export_reads_one_of_the_sources():
    """The exports are the sound the run produced, and that is a property of what they are given.

    An export handed a source would be un-summed, un-gained, and in the case of a chain run not even
    processed — a perfectly valid WAV of the wrong thing.
    """
    plan = a_plan(audio_exports=(".wav", ".mp3"), strategy="chain")
    for export in normalizer.sound_plan(plan):
        assert export.command is not None
        given = str(export.command.args[export.command.args.index("-i") + 1])
        assert given != str(plan.source.path)


def test_an_occupied_sound_name_is_skipped_with_a_reason(tmp_path):
    source = tmp_path / "talk.wav"
    spec = a_spec(source, audio_exports=(".wav", ".mp3"))
    info = an_audio(source)
    plan = normalizer.plan_normalize(spec, info, -6.0, -9.0)
    plan.audio_paths[0].write_bytes(b"already here")
    exports = normalizer.sound_plan(plan)
    assert all(export.command is None for export in exports)
    assert all(export.skip is not None for export in exports)
    assert plan.audio_paths[0].name in exports[0].skip


# ---------------------------------------------------------------------------------------
# Where the file goes
# ---------------------------------------------------------------------------------------


def test_a_name_that_is_taken_steps_aside_rather_than_refusing(tmp_path):
    """A normalized copy is derived from a file that is still there, so its name walks rather than
    refusing: pressing the button twice costs nothing instead of demanding a cleanup first."""
    source = tmp_path / "talk.wav"
    source.write_bytes(b"x")
    first = normalizer.output_for(source)
    assert first.name == f"talk{normalizer.SUFFIX}.wav", "the source's own extension is kept"
    first.write_bytes(b"y")
    second = normalizer.output_for(source)
    assert second.name == f"talk{normalizer.SUFFIX} 2.wav"


def test_the_container_replaces_the_extension_and_never_the_stem(tmp_path):
    """`talk.en.wav` -> `talk.en - normalized.m4a`.

    `Path('talk.en - normalized').stem` is `'talk'`, not `'talk.en - normalized'` — the last dot is in
    the *name* rather than before an extension. Stripping the extension early is therefore a way to lose
    a word, which is why the suffix goes in before the source's extension and the container replaces it
    afterwards.
    """
    source = tmp_path / "talk.en.wav"
    plan = normalizer.plan_normalize(a_spec(source), an_audio(source), -6.0, -9.0)
    # The planner settles the destination itself, once the container is known, so both spellings agree
    # and the window cannot show one path while the run writes another.
    assert plan.spec.output.name == f"talk.en{normalizer.SUFFIX}.m4a"
    assert plan.output == plan.spec.output


def test_the_audio_exports_sit_on_the_master_s_own_stem():
    plan = a_plan(an_audio(Path("C:/media/talk.wav")), audio_exports=(".wav", ".mp3"))
    for path in plan.audio_paths:
        assert path.stem == plan.output.stem
        assert path.parent == plan.output.parent
