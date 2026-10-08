# TheNormalizer-V2 — specification

What the product is required to do. Nothing here says how it is done; that is `DESIGN.md`.

Every requirement carries the check that decides whether it holds. A requirement nothing can check is
either a gap or a defect, and both are worth seeing.

---

## 1. What the product is

An operator has a folder of finished media — videos and sound files — that have to be delivered at a
consistent **peak level**. Each file arrives at whatever level it arrived at, and the product takes each
one to a level the operator names.

**The picture is not re-encoded.** That is the point of the product for the video half of its work: a
re-encode of a 17-minute 1080p segment costs minutes and one generation of quality, and the level is a
property of the *sound* — so the picture is stream-copied and only the audio is encoded. The sound files
this product also writes are the finished sound, decoded from the file it has just written.

The product takes **both kinds of file**, and the difference is a fact about the source rather than a mode
in the window: a file with a picture keeps it, a file without one becomes a sound file, and the container
each is written into follows from that.

---

## 2. Definitions

- **Peak** — the largest absolute sample in a stream, in dBFS. Measured by decoding every sample, never
  read from a header, because no container carries one.
- **Target** — the level the finished file should peak at, in dBFS.
- **dBFS** — decibels relative to full scale. Negative; 0 is the largest number a sample can hold.
- **Silence** — a sound whose peak is at or below −90 dBFS. Not "very quiet": 16-bit PCM cannot hold
  exact zero, and a file of nothing reads about −91 dBFS.
- **The chain** — the operator's Premiere Track Fx, reproduced: a compressor that performs no gain
  reduction and the limiter that does the work. See R8.
- **Drive** — how hard the material is pushed into the chain's limiter, in decibels.
- **Ceiling** — the level the chain's limiter will not let a sample past.
- **Stage pass** — the encode the run makes in order to *measure* what the chain and the codec do to this
  file. It is written to a temporary file and never delivered.
- **Master** — the file the run delivers. `<name> - normalized<ext>`, beside the source.

---

## 3. The level, stated

With `P` the source's measured peak, `T` the target, and `G` the gain the run applies:

```
gain    = T − P + drive          the front fader (drive is 0 for a strategy with no chain)
chain   = the operator's chain, or nothing
output  = (source × gain) through the chain
```

and the run does not *assume* what comes out. It applies the above to a temporary file, **encodes it
through the master's own codec**, decodes it back, and measures it; the master's fader is then

```
master fader = stage fader + (T − what the stage measurement said)
```

Three measured facts force that second step, and each is worth a decibel or more on ordinary material:
the chain's own limiter is a ceiling; `aac` overshoots on decode by about 0.6 dB; and a fader **after** the
limiter cannot lift the result above it. `DESIGN.md` §2 has the arithmetic and the measurements.

**R1 — The default target is −6.0 dBFS.**
Because that is where a stitched episode sits, so a file normalized here sits beside one. *Check:*
`test_plan.py::test_the_default_strategy_reaches_any_target`.

**R2 — A run reports the level it measured, not the level it was asked for.**
`outcome.peakErrorDb` is the signed difference between what the finished file peaks at and the target, and
`outcome.outputLevels` is the engine's own reading of it. *Check:* `test_verify.py::test_a_level_on_the_target_passes`
and `::test_a_level_off_the_target_fails_and_says_by_how_much`.

---

## 4. Requirements

### The media layer

**R3 — Every source is probed and its level measured before a plan exists about it.**
The probe reports the container, the duration, the size, the picture (codec, profile, level, size, pixel
format, frame rate as a rational, frame count, timescale, B-frame depth) and the sound (codec, sample
rate, channel count, bit rate). The level is measured by decoding the whole sound. *Check:*
`test_plan.py::test_a_video_file_is_a_file_with_both`, `test_server.py::test_probe_says_what_it_measured_and_where_the_output_would_go`.

**R4 — Silence is a value and not a very quiet signal.**
`volumedetect` prints `-inf` for digital silence, and 16-bit PCM silence reads about −91 dBFS. Both are
silence, both become `null`, and a silent file is given **no gain** — there is no gain that makes silence
audible, and inventing one would be the product making up a number. *Check:*
`test_plan.py::test_silence_is_not_a_very_quiet_signal`, `test_verify.py::test_silence_stays_silent`,
`test_plan.py::test_gaining_a_silent_file_applies_nothing`.

**R5 — A request is refused, with a reason, by whichever layer can see the problem.**
A missing field or a path that is not a file is `server._sources_from` — that is about the *request*. A
file with no sound, or no duration, is `plan_normalize` — that is about the *file*. A strategy that does
not exist, a level outside the range a level works in, and the `.mp3`-without-`.wav` dependency are all
`InputRefused`, and each refusal names what it needs. *Check:* `test_plan.py::test_a_file_with_no_sound_is_refused`,
`::test_a_strategy_that_does_not_exist_is_refused_by_name`, `::test_the_mp3_alone_is_refused_because_it_is_encoded_from_the_wav`,
`test_server.py::test_a_body_that_is_not_a_request_is_a_400_and_not_a_500`.

### The picture

**R6 — The picture is stream-copied, always, and proved to be.**
Every command that maps the source's picture carries `-c:v copy`, and exactly one command does. After the
run, frames are sampled from the output and from the source and their checksums compared — which is a
thing a stream copy guarantees and a re-encode cannot fake. A file with no picture reports the check as
`not_checked` rather than as a pass. *Check:* `test_plan.py::test_the_picture_is_always_copied`,
`::test_the_sound_is_fetched_from_the_fader_and_the_picture_from_the_source`,
`test_verify.py::test_a_picture_that_was_copied_passes_and_one_that_was_not_fails`,
`scripts/check_normalize.py` (*10/10 sampled frames bit-identical to the source*).

**R7 — The container is named to ffmpeg rather than inferred, and the finished file is checked against it.**
A source whose container cannot hold an AAC sound is written into one that can — `.wav` and `.webm` become
`.m4a` and `.mkv` — and the plan says so before the run. After it, the file's own `format_name` is compared
against the muxer that was asked for. *Check:* `test_plan.py::test_the_muxer_is_named_and_not_inferred_from_the_extension`,
`::test_a_webm_source_is_written_as_matroska`,
`test_verify.py::test_the_container_is_checked_against_the_muxer_that_was_asked_for`.

### The sound

**R8 — Three strategies exist, the operator picks one, and only one of them reaches every target.**

| Strategy | What happens to the sound | What it can reach |
|---|---|---|
| `gain` (**default**) | one fader, and nothing else | any target |
| `chain` | the operator's Premiere Track Fx, plus the out fader that puts its output on the target | any target |
| `ceiling` | the fader, then a limiter at the target | any target, without clipping on the way |

**Every strategy reaches every target**, and it did not used to: a limiter clamps material driven into it to
a level of its own, below the ceiling it names and dependent on the drive, so a chain run could only deliver
what its limiter happened to land on. The run now measures the chain and corrects it with a fader *after* the
limiter — see `docs/DESIGN.md` §2.5 for the three measurements and the two designs that got it wrong.
*Check:* `test_plan.py::test_the_default_strategy_reaches_any_target`,
`test_server.py::test_the_stage_and_master_passes_are_one_operating_point`,
`scripts/check_normalize.py`.

**R9 — The chain's two figures are the operator's, and they reach ffmpeg as the units ffmpeg wants.**
The drive is the fader **in front of** the chain, not `acompressor`'s `makeup` — which is a multiplier with
a floor of 1.0 and therefore cannot attenuate, and which a caller writing `makeup=12` asking for twelve
decibels would get +21.6 dB from. The ceiling is `alimiter`'s `limit`, converted from dBFS to the linear
amplitude the option wants. Both are reported in the plan, in the operator's own units. *Check:*
`test_plan.py::test_the_compressor_s_makeup_is_left_at_unity_and_the_drive_is_the_fader`,
`::test_the_limiter_s_automatic_level_is_off`, `scripts/check_normalize.py` (*+12 dB is `makeup=3.9812`*).

**R10 — A target that needs more headroom than the ceiling allows is named before the run.**
A limiter clamps material driven into it to about three decibels under the nominal ceiling. The out fader
closes the gap *downwards* — it can only lift what the limiter delivered, never past full scale — so a target
more than three decibels above the ceiling is one the chain cannot reach, and the plan says so. With the
level and the limiter as one number (R27) that cannot arise from the window; it is a caution for a caller
that sets them apart. *Check:*
`test_plan.py::test_a_target_the_limiter_cannot_reach_is_a_caution_before_the_run`.

**R11 — The sound's rate and channel count are the source's.**
Nothing resamples and nothing remixes. A rate that is unusual for the output codec is a caution rather
than a conversion. *Check:* `test_verify.py::test_the_sound_keeps_the_source_s_rate_and_channels`,
`scripts/check_normalize.py` (*a 16 kHz mono file stays 16 kHz mono*).

### The measurement

**R12 — The master's level is a measurement of the codec, not a model of it.**
The stage pass encodes through the same muxer the master uses, the result is decoded and measured, and the
master's gain is corrected by what that measurement found. *Check:*
`test_server.py::test_the_stage_and_master_passes_are_one_operating_point`, and
`scripts/check_normalize.py`, whose reference run reports the pipeline at −6.00 dBFS against a −6.0 target.

**R13 — The output is measured, never assumed, and a wrong measurement is a failure.**
After a run the product reports the finished peak, its own mean, the difference from the target, the gain
the level actually moved by, the sound's length, the picture's length and whether the sound decodes — each
as `passed`, `failed` or `not_checked`, and the third is not a pass. *Check:*
`test_verify.py::test_the_tolerance_is_the_codec_s_resolution_and_not_the_instrument_s`,
`::test_a_level_off_the_target_fails_and_says_by_how_much`, `::test_the_three_verdicts` throughout.

**R14 — A sound that will not decode is a failure, whatever the container says.**
ffmpeg's WAV muxer accepts AAC, writes a valid WAV header and stores ADTS frames in the data chunk;
`ffprobe` reports it happily and the first decode fails. The output is decoded as part of the check, so a
file that probes correctly and plays as nothing is reported as a failure. *Check:*
`test_verify.py::test_a_sound_that_will_not_decode_is_a_failure`.

**R15 — The picture's length is measured from the picture.**
From the presentation timestamps of the output's own frames, never from the container's `duration` field,
which is written from the last packet in *decode* order and is short by the B-frame reorder depth.
*Check:* `test_verify.py::test_the_sound_and_the_picture_must_end_together`, and the same defect recorded
in the sibling product's `DESIGN.md`.

**R16 — Frames are compared by presentation time, not by position.**
An input seek lands in a different place in two files that share a picture but not a keyframe layout, so
comparing two decodes position by position reports a copy as broken. *Check:*
`test_verify.py::test_frames_are_matched_by_presentation_time_and_not_by_position`.

### The files

**R17 — A normalized copy is written beside its source, and the original is never touched.**
`<name> - normalized.<ext>`, in the source's own folder. A batch writes each file beside its own source; a
single file may be given a destination by the operator, and only a single file may. *Check:*
`test_server.py::test_a_batch_of_several_files_given_one_output_path_is_refused`,
`test_plan.py::test_the_container_replaces_the_extension_and_never_the_stem`.

**R18 — A name that is taken steps aside rather than refusing.**
`talk - normalized.m4a`, then `2`, then `3`. A normalized copy is derived from a file that is still sitting
right there, so the common case of pressing the button twice is not a mistake worth a red sentence.
*Check:* `test_plan.py::test_a_name_that_is_taken_steps_aside_rather_than_refusing`.

**R19 — A file the run did not choose is never written over, and the check is taken twice.**
The destination is settled when the plan is built and asked again at the rename that publishes the result,
because the first answer is minutes old by then. A run that fails or is cancelled leaves the destination
exactly as it was. *Check:* `test_plan.py::test_an_occupied_sound_name_is_skipped_with_a_reason`,
`scripts/check_normalize.py` (*the source is still there*).

**R20 — The sound files are the sound the episode plays, and an occupied name is skipped rather than
refused.**
A `.wav` and a 320 kbps `.mp3` beside each master, read out of the file the run has just written and — for
the MP3 — encoded from the WAV rather than from the episode again. An export is never handed one of the
sources and applies no filter of its own. An existing `.wav` or `.mp3` does not stop the run: the exports
are skipped together with a sentence, and the master still goes out. The run reports the files **it wrote**
rather than the files at those paths. *Check:*
`test_plan.py::test_the_wav_export_is_uncompressed_16_bit_and_carries_no_picture`,
`::test_the_mp3_is_320_kbps_and_is_encoded_from_the_wav`, `::test_no_export_reads_one_of_the_sources`,
`scripts/check_normalize.py` (*the WAV is the master's sound, not the source's: −9.30 / −9.30 dBFS*).

### Operation

**R21 — One batch at a time.**
A second batch while one is in flight is refused with `409` and `reason: "busy"`. Two ffmpeg processes on
one disk are not twice as fast, and the second one's progress would make the first one's bar a lie.
*Check:* `test_server.py::test_a_second_batch_while_one_runs_is_refused_with_busy`.

**R22 — A batch runs its files in order and one failure does not stop it.**
A file that cannot be read is that **job's** failure: the remaining files are still the operator's work,
and stopping at the first unreadable file would be the tool deciding they were done. *Check:*
`test_server.py::test_the_batch_reports_a_job_per_file_when_it_finishes`.

**R23 — Progress is streamed, per file, and a subscriber that stops reading cannot block the work.**
Each subscriber has a bounded queue and every publish is non-blocking. *Check:* the `Hub` in `server.py`
and its `put_nowait`, exercised by every batch test.

**R24 — Failure carries the command.**
A failed ffmpeg invocation is reported with its argument list and its stderr, because that is what makes it
fixable. *Check:* `process.FFmpegError`, and `test_server.py::test_the_batch_reports_a_job_per_file_when_it_finishes`.

### Interface

**R25 — Every number the window shows is one the engine measured.**
No figure is computed in the interface that the engine could have measured: no gain, no duration, no level,
no frame count. What the interface computes is presentation — a byte count as a magnitude, a clock for the
*window's own* elapsed time, and the width of a bar. *Check:* review of `api.ts` against `server.py`; the
interface's own tests (`format.test.ts`, `queue.test.ts`) assert that the four conversions it owns are
conversions and nothing more.

**R26 — The window is two controls, and each one says what it does in words that do not have to be looked
up.**
**Level** is the peak every finished file will have; **Even out** is how far the quiet parts are lifted
toward the loud ones; **Drive** is how hard the sound is pushed into the limiter. Everything else the engine accepts is either fixed at the value this product is for — the strategy,
the trim — or is not a decision the operator is making — a named destination, a bitrate. *Check:*
`scripts/check_window.py` asserts that exactly three of the level, even-out, drive, strategy, target,
make-up and ceiling fields exist on the page, and that each carries its prose. *Rationale:* a field whose label has to
be looked up is a field that gets left alone (feedback from the first use of the window).

**R26b — The product reduces dynamic range, because that is what it is for.**
A `chain` run places the operator's filters, and those filters contain **no gain reduction** — their
compressor is `ratio=1`, transparent by design — so a run used to change only the dynamics its limiter
clamped. Measured on a 13-minute interview, the quietest fifth moved +3.7 dB against the loudest fifth's
+6.3: a 21.8 LU spread barely touched. A leveler (`speechnorm`) now runs before the fader, because it decides
*relative* level while everything after it decides absolute level. *Check:*
`test_plan.py::test_the_leveler_is_first_and_can_be_turned_off`, `scripts/loudness_profile.py` — the same
interview's gap is closed by **14.2 dB** with it and 2.6 dB without.

**R27 — The level and the limiter are one number.**
They were two fields for one decision. They travel as `targetDbfs` and `ceilingDbfs` from a single
`levelDbfs`, so the request cannot be built with them out of step. *Check:*
`queue.test.ts::sends one number as both the level and the limiter`.

**R28 — The output folder is the source folder.**
A normalized copy is written beside its source, and a batch of any length is written that way: there is no
field, no dialog and no reset for it, because there was one and nobody was making that decision. The
report still states the path the engine will write. *Check:* `test_server.py::test_a_batch_of_several_files_given_one_output_path_is_refused`,
`test_plan.py::test_the_container_replaces_the_extension_and_never_the_stem`.

**R29 — The window is the size of its content, and it does not fill the screen.**
It opens at 1180x880 and can be dragged down to 1100x760; it is never maximized. Its minimum is *checked*
rather than declared: `scripts/check_window.py --width 1100 --height 760` renders the page at that size and
asserts that the document does not overflow, that no control runs past an edge, and that the settings column
scrolls rather than clipping its own Normalize button. The two numbers are `WINDOW_MINIMUM` in
`electron/main.cjs` and `--frame-min-width`/`--frame-min-height` in `styles/tokens.css`. *Rationale:*
maximized on a 2560-wide display, a form and a list left half the screen as substrate.

**R30 — There is one way to add files, and it is in the queue's header.**
There were two buttons that both said "Add files" and opened the same dialog — one in the title bar and one
in the panel listing what had been added. Two controls for one action is a question the operator cannot
answer. *Check:* `scripts/check_window.py` counts the buttons whose label starts with "Add files" and
asserts there is one, in a `.zone`.

**R31 — The settings are a form, not an essay.**
Each control is a label, a value with its unit, and **one** line of help, and there are no zone headers over
them. It was a zone header plus a field label plus a note plus a paragraph per setting, which said the same
thing three times and measured 655 px of column for two fields — and that height is what decides how small
the window can be. *Check:* `scripts/check_window.py` asserts that every setting carries a unit and exactly
one hint, and that the settings plus the action bar come to under 380 px.

**R32 — The settings are on the left, and the source order is the reading order.**
The column that holds the decision comes first on the screen and first in the DOM, so what a screen reader
and the Tab key follow is what the layout shows. *Check:* `scripts/check_window.py` measures both columns'
positions and asserts the DOM lists them in the same order.

**R30 — A checkbox asks for what this machine can actually write, and a disabled control is not a choice
that travels.**
`GET /api/health` reports whether this machine's ffmpeg has an MP3 encoder; without it the box is disabled.
A health answer that has not arrived claims nothing: the box lives until the machine says otherwise.
*Check:* `test_server.py::test_health_names_this_product`,
`scripts/check_window.py::the sound-file note is written from what this machine can do`.

**R31 — The window offers only the sound files the run wrote.**
The reveal buttons are built from `outcome.audioWritten`, which is the engine's list of files *this run*
wrote — not from the plan's paths, which is what would offer an older file when the exports were skipped.
*Check:* `test_plan.py::test_an_occupied_sound_name_is_skipped_with_a_reason`.

---

## 5. Traceability

| Requirement | Check |
|---|---|
| R1 | `test_plan.py::test_the_default_strategy_reaches_any_target` |
| R2 | `test_verify.py::test_a_level_on_the_target_passes`, `::test_a_level_off_the_target_fails_and_says_by_how_much` |
| R3 | `test_server.py::test_probe_says_what_it_measured_and_where_the_output_would_go` |
| R4 | `test_plan.py::test_silence_is_not_a_very_quiet_signal`, `test_verify.py::test_silence_stays_silent` |
| R5 | `test_plan.py::test_a_file_with_no_sound_is_refused`, `test_server.py::test_a_body_that_is_not_a_request_is_a_400_and_not_a_500` |
| R6 | `test_plan.py::test_the_picture_is_always_copied`, `test_verify.py::test_a_picture_that_was_copied_passes_and_one_that_was_not_fails`, `scripts/check_normalize.py` |
| R7 | `test_plan.py::test_the_muxer_is_named_and_not_inferred_from_the_extension`, `test_verify.py::test_the_container_is_checked_against_the_muxer_that_was_asked_for` |
| R8 | `test_plan.py::test_the_default_strategy_reaches_any_target`, `test_server.py::test_the_plan_names_the_output_the_container_and_the_gain` |
| R9 | `test_plan.py::test_the_compressor_s_makeup_is_left_at_unity_and_the_drive_is_the_fader`, `::test_the_limiter_s_automatic_level_is_off` |
| R10 | `test_plan.py::test_a_target_the_limiter_cannot_reach_is_a_caution_before_the_run` |
| R11 | `test_verify.py::test_the_sound_keeps_the_source_s_rate_and_channels`, `scripts/check_normalize.py` |
| R12 | `test_server.py::test_the_stage_and_master_passes_are_one_operating_point`, `scripts/check_normalize.py` |
| R13 | `test_verify.py::*` |
| R14 | `test_verify.py::test_a_sound_that_will_not_decode_is_a_failure` |
| R15 | `test_verify.py::test_the_sound_and_the_picture_must_end_together` |
| R16 | `test_verify.py::test_frames_are_matched_by_presentation_time_and_not_by_position` |
| R17 | `test_server.py::test_a_batch_of_several_files_given_one_output_path_is_refused`, `test_plan.py::test_the_container_replaces_the_extension_and_never_the_stem` |
| R18 | `test_plan.py::test_a_name_that_is_taken_steps_aside_rather_than_refusing` |
| R19 | `test_plan.py::test_an_occupied_sound_name_is_skipped_with_a_reason`, `scripts/check_normalize.py` |
| R20 | `test_plan.py::test_the_wav_export_is_uncompressed_16_bit_and_carries_no_picture`, `::test_the_mp3_is_320_kbps_and_is_encoded_from_the_wav`, `scripts/check_normalize.py` |
| R21 | `test_server.py::test_a_second_batch_while_one_runs_is_refused_with_busy` |
| R22 | `test_server.py::test_the_batch_reports_a_job_per_file_when_it_finishes` |
| R23 | `server.Hub`, exercised by every batch test |
| R24 | `test_server.py::test_the_batch_reports_a_job_per_file_when_it_finishes` |
| R25 | review of `api.ts` against `server.py`; `format.test.ts`, `queue.test.ts` |
| R26 | `scripts/check_window.py` |
| R27 | `queue.test.ts::sends one number as both the level and the limiter` |
| R28 | `test_server.py::test_a_batch_of_several_files_given_one_output_path_is_refused` |
| R29 | `scripts/check_window.py` at both sizes |
| R30 | `scripts/check_window.py` (the button count) |
| R31 | `scripts/check_window.py` (the stack height) |
| R32 | `scripts/check_window.py` (both columns' positions, and the DOM order) |
| R30 | `test_server.py::test_health_names_this_product`, `scripts/check_window.py` |
| R31 | `test_plan.py::test_an_occupied_sound_name_is_skipped_with_a_reason` |

---

## 6. What this product is not

- It does not re-encode the picture. Not to scale, not to colour, not to change its codec. A file whose
  container cannot hold the new sound is written to a different container rather than re-encoding the
  picture into the old one.
- It does **not** measure loudness. There is no LUFS measurement and no loudness target: the level is a
  *peak*, which is what the product's sibling produces and what an edit bay asks for. `GET /api/health`
  reports whether this machine's ffmpeg has the EBU R128 filters, and nothing uses them.
- It does not decide what the target should be. −6.0 dBFS is a default, not a recommendation, and the
  field is the operator's.
- It does not resample, remix or re-time. The sound keeps its own rate and channel count, and every frame
  in the output is a frame that was already in the source.
- It does not touch the sources. They are opened read-only and never written.
- It does not replace a file it did not choose. An occupied *sound-file* name is skipped with a sentence; a
  destination that appears mid-run is not replaced either.
- It does not run two batches at once, and it has no queue inside the engine: a batch is one request, and
  changing your mind is a Stop.
