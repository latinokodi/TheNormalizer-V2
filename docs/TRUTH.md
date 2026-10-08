# What is claimed, and what would catch it being false

Every claim this product makes, paired with the check that decides it. A claim and its check are the same
size; a claim with no check is a claim with no evidence and belongs in §3.

Statuses: **checked** — a command was run and this is what it printed. **not checked** — no command has
established it, and it is listed here rather than assumed.

The measurements below were taken on this machine: `ffmpeg version 8.0.1-essentials_build-www.gyan.dev`,
Python 3.12.10, Windows. Where a figure is machine-specific it says so.

---

## 1. The level

| Claim | Check | Status |
|---|---|---|
| The delivered file peaks at the target | `scripts/check_normalize.py`, real ffmpeg on fixtures it builds: a tone, a quiet tone, a loud tone, digital silence and a 720p video. Every target landed within the tolerance — the reference tone **−6.00 dBFS against a −6.0 target** | **checked** |
| The level is measured, not modelled | the run encodes a stage pass through the master's own codec, decodes it and measures it, then corrects the master's gain by what it found. `test_server.py::test_the_stage_and_master_passes_are_one_operating_point` | **checked** |
| `aac` overshoots a PCM master by about 0.6 dB, and nothing upstream prevents it | measured directly: a master written at exactly −6.00 dBFS came back out of an `aac` decode at **−5.40**; FLAC reproduced **−6.00**; `alimiter` at −6, −5, −4, −3, −2, −1 and 0 dBFS all produced −5.40 | **checked** |
| A silent file is given no gain | `test_verify.py::test_silence_stays_silent`; `scripts/check_normalize.py` reports *"silence, peak None"* for a file of nothing | **checked** |
| 16-bit PCM silence reads about −91 dBFS and is treated as silence | measured: `anullsrc` as 16-bit stereo reads `max_volume: -91.0 dB`; `SILENCE_DBFS = -90` and `test_plan.py::test_silence_is_not_a_very_quiet_signal` | **checked** |
| The tolerance is the codec's resolution and not the instrument's | `volumedetect` reads to 0.1 dB; the delivered peak is one lossy encode away from the plan. `PEAK_TOLERANCE_DB = 1.0`, and `test_verify.py::test_the_tolerance_is_the_codec_s_resolution_and_not_the_instrument_s` fails if it is narrowed to 0.3 | **checked** |

## 2. The picture

| Claim | Check | Status |
|---|---|---|
| The picture is stream-copied, not re-encoded | every command that maps the source's picture carries `-c:v copy`, and exactly one command does: `test_plan.py::test_the_picture_is_always_copied`, `::test_the_sound_is_fetched_from_the_fader_and_the_picture_from_the_source` | **checked** |
| Copied frames are bit-identical to their source | `scripts/check_normalize.py` on a real 720p25 clip: **10/10 sampled frames bit-identical**. The first run of this check reported 4/11 and the mismatch was the *seek*, not the copy — see the next row | **checked** |
| Frames are matched by presentation time, not by position | an input seek lands at different offsets in two files with different keyframe layouts. `test_verify.py::test_frames_are_matched_by_presentation_time_and_not_by_position` builds exactly that and requires it to pass | **checked** |
| The picture's length is measured from its own timestamps | `test_verify.py::test_the_sound_and_the_picture_must_end_together`; the reference run reports *6.021 s of picture against the source's 6.021 s (0 ms)* | **checked** |
| The output is the container it was asked for | `test_verify.py::test_the_container_is_checked_against_the_muxer_that_was_asked_for`, and `::test_a_container_the_muxer_really_writes_passes`. The reference run reports *mov,mp4,m4a,3gp,3g2,mj2, written by the ipod muxer* | **checked** |
| A `.wav` source is written as an `.m4a` rather than as a WAV full of AAC | measured: `-c:a aac` into a `.wav` produces a valid WAV header with ADTS frames in the data chunk, which `ffprobe` reports happily and which fails to decode with `decode_band_types: Input buffer exhausted before END element found`. `test_plan.py::test_the_muxer_is_named_and_not_inferred_from_the_extension` | **checked** |
| The sound in the delivered file decodes | `test_verify.py::test_a_sound_that_will_not_decode_is_a_failure`; every run in `scripts/check_normalize.py` reports *every sample decoded without an error* | **checked** |

## 3. The two strategies

| Claim | Check | Status |
|---|---|---|
| `gain` reaches any target, from −40 dBFS to −3 dBFS | `test_plan.py::test_the_default_strategy_reaches_any_target` across four source levels, and `scripts/check_normalize.py` at two targets on real files | **checked** |
| `chain` reproduces the operator's filters verbatim | `test_plan.py::test_the_chain_graph_holds_the_operator_s_chain_and_nothing_else` — the compressor's threshold, ratio, attack and release, the limiter's release, and `level=0` | **checked** |
| The drive is a fader and not `acompressor`'s `makeup` | `makeup` is a multiplier whose range starts at 1.0, so it **cannot attenuate**; `makeup=12` asking for twelve decibels is **+21.6 dB**. `test_plan.py::test_the_compressor_s_makeup_is_left_at_unity_and_the_drive_is_the_fader`, and `scripts/check_normalize.py` asserts `+12 dB` reaches ffmpeg as `makeup=3.9812` | **checked** |
| `alimiter`'s `level=1` would add about +3 dB | measured: a sine driven +30 dB through `limit=0.5012` comes out at **−3.0 dBFS** with `level=1` and at **−9.0 dBFS** with `level=0`. `test_plan.py::test_the_limiter_s_automatic_level_is_off` | **checked** |
| A target above the limiter's working level is named before the run | `test_plan.py::test_a_target_the_limiter_cannot_reach_is_a_caution_before_the_run`, and `scripts/check_normalize.py` reports the caution on a real run | **checked** |
| A fader **after** the chain cannot lift the result above the limiter | measured: `volume=12dB,alimiter…,volume=21.1dB` on a −27.1 dBFS source delivers **0.0 dBFS**, and the chain alone delivers −15.1 | **checked** |
| A fader **before** the chain drives it into clipping | measured: `volume=21.1dB,<chain>` delivers **0.0 dBFS** where `<chain>,volume=9.1dB` delivers **−6.0 dBFS** | **checked** |
| A stage pass that runs dry measures the fader rather than the codec | measured: the chain's transparent output against an encode of it through the fader differs by **+9.7 dB** where the true overshoot is 0.6 | **checked** |

## 4. The files

| Claim | Check | Status |
|---|---|---|
| A normalized copy sits beside its source and the original is untouched | `scripts/check_normalize.py`: *the source is still there*. `test_plan.py::test_the_container_replaces_the_extension_and_never_the_stem` pins the name, including a source with a dot of its own | **checked** |
| A name that is taken steps aside | `test_plan.py::test_a_name_that_is_taken_steps_aside_rather_than_refusing`; the live engine suggested `jfk - normalized 2.m4a` when the first name was occupied | **checked** |
| A batch of several files given one output path is refused | `test_server.py::test_a_batch_of_several_files_given_one_output_path_is_refused` | **checked** |
| The WAV is uncompressed 16-bit at the source's rate, with no picture | `test_plan.py::test_the_wav_export_is_uncompressed_16_bit_and_carries_no_picture`; `scripts/check_normalize.py` probes what was written: **pcm_s16le, 48000 Hz, 2ch** | **checked** |
| The MP3 is 320 kbps and is encoded **from the WAV** | `test_plan.py::test_the_mp3_is_320_kbps_and_is_encoded_from_the_wav`; `scripts/check_normalize.py` probes **mp3, 320000** and asserts the MP3's input is the WAV's path | **checked** |
| No export reads one of the sources | `test_plan.py::test_no_export_reads_one_of_the_sources` walks every strategy | **checked** |
| The exports are the master's sound and not the source's | `scripts/check_normalize.py`: master **−9.30 dBFS**, WAV **−9.30 dBFS** | **checked** |
| An occupied sound-file name is skipped rather than refusing | `test_plan.py::test_an_occupied_sound_name_is_skipped_with_a_reason`; the skip sentence names the file in the way | **checked** |
| Nothing this product writes is written over | `run_normalize` checks the destination twice — when the run starts and again at the rename — and every write goes to a temporary name first. `scripts/check_normalize.py` confirms the source survives | **checked** |

## 5. The engine and the API

| Claim | Check | Status |
|---|---|---|
| The whole engine suite passes | `venv\Scripts\python.exe -m pytest backend\tests -q` → **76 passed in 1.5 s**, exit 0 | **checked** |
| The engine serves its own interface on one origin | `GET /` → **200**, the built page; `scripts/check_window.py` mounts React on `http://127.0.0.1:8767/` | **checked** |
| The route index and the route table agree | `test_server.py::test_the_index_lists_the_routes_the_app_registers` | **checked** |
| The engine starts and answers | `GET /api/health` → `{"product": "TheNormalizer", "version": "2.0.0", "aac": true, "libmp3lame": true, "loudnorm": true, "versionLine": "ffmpeg version 8.0.1-essentials_build-www.gyan.dev …"}` | **checked** |
| A body that is not a request is a `400`, not a `500` | `test_server.py::test_a_body_that_is_not_a_request_is_a_400_and_not_a_500`, parameterised over seven bodies including `[1,2,3]`, `"a string"`, `null`, `12` and truncated JSON | **checked** |
| A refusal names what it is missing | `test_server.py::test_a_request_with_no_source_is_refused_by_name`, `::test_a_source_that_is_not_a_file_is_refused_by_name`, `::test_an_unknown_strategy_is_refused_with_the_ones_there_are` | **checked** |
| One batch at a time | `test_server.py::test_a_second_batch_while_one_runs_is_refused_with_busy` | **checked** |
| Cancelling nothing is an answer, not an error | `test_server.py::test_cancelling_nothing_is_an_answer` | **checked** |
| The singleton is empty when nothing is running | `test_server.py::test_nothing_is_running_when_nothing_has_been_started` → **204** | **checked** |
| A batch reports a job per file and finishes | `test_server.py::test_the_batch_reports_a_job_per_file_when_it_finishes` — `batch-started`, `job-started`, `job-finished`, `batch-finished` | **checked** |
| The real engine works over a real socket end to end | driven live against `C:\…\jfk.wav` (11 s, 16 kHz mono, peaking −2.10 dBFS): plan *gain −3.90 dB*, run *delivered −6.00 dBFS, off target 0.00 dB, verified true*, seven checks passed and two `not_checked` for the absent picture, and both sound files written | **checked** |

## 6. The window

| Claim | Check | Status |
|---|---|---|
| The interface compiles under the tree's strict TypeScript settings | `npx tsc --noEmit` → exit **0** | **checked** |
| The interface builds | `npx vite build` → exit **0**, 37 modules, `app.js` 175.36 kB (55.46 kB gzip), `app.css` 22.42 kB (4.50 kB gzip) | **checked** |
| The interface's own logic is tested | `npx vitest run` → **28 passed** (2 files: the four conversions the interface owns, and the reducer that joins the event stream to the rows) | **checked** |
| The window renders, from the engine's own origin, in a real browser | `scripts/check_window.py` drives Chromium over the DevTools protocol: React mounts on `http://127.0.0.1:8767/`, the product names itself, the empty state and its button are drawn, and the page reports no error | **checked** |
| The window gets the engine's capabilities and writes its prose from them | the same script: the footer reads *ffmpeg ready*, which is a `fetch` the page had to make and complete | **checked** |
| Moving a setting enables the right controls | the same script: the chain's two figures are disabled on `gain` and enable at **12.0 dB into a −6.0 dBFS limiter** when `chain` is chosen | **checked** |
| A target the chain cannot reach is flagged on screen before the run | the same script moves the target to −1.0 with `chain` chosen and finds the warning prose | **checked** |
| Nothing scrolls or clips at the declared minimum | the same script at 1600×1000: **0 px** of horizontal overflow, **0 px** of vertical, **0** controls past the right edge, 2 scrollable wells | **checked** |
| The window opens under Electron | Electron was launched against this tree: the shell logged *backend: venv\Scripts\python.exe backend\server.py*, the engine logged *TheNormalizer backend 2.0.0 on http://127.0.0.1:8767*, the shell logged *the interface loaded*, and the OS reported a window titled **TheNormalizer** while `/api/health` answered on that port | **checked** |
| A graceful close walks the process tree | the titled window was closed with `CloseMainWindow()`, which is what the title bar's X does. Afterwards: **no stranded engine**, every Electron process exited, and the shell logged *backend stopped* | **checked** |
| A failed start draws the page that explains it | with `backend\` renamed away, the window titled itself **"TheNormalizer - the engine did not start"** and the shell logged *backend exited unexpectedly (code 2)* | **checked** |
| The interface has been visually inspected | `docs/shots/01-empty.png`, captured from the live page at 1600×1000 | **checked** (the empty state) · **not checked** (a queue with rows in it, a run in progress, a finished report) |

## 7. What is not checked

- **No test in `backend/tests` runs a real ffmpeg.** The suite launches none except the one `/api/health`
  test. The evidence that these argument lists are ones ffmpeg accepts is `scripts/check_normalize.py`,
  which builds its own fixtures and runs the real engine over them — a regression in the engine would not
  fail the suite. That is a deliberate trade (a suite that needs footage is a suite nobody runs) and it is
  the largest gap here.
- **The target on a real episode has been measured once, on an 11-second clip.** `jfk.wav` normalized to
  exactly −6.00 dBFS, which establishes the pipeline. It does **not** establish anything about a 17-minute
  1080p file: how long the picture copy takes, what an AAC encode of a long file costs, or whether
  `-map 0:v` behaves on a source with several video streams. A real episode is how that is answered, and it
  has not been run.
- **`chain` on real material has not been listened to.** Every claim about it is about its *argument lists*
  and its *level*: the filters are the operator's, the ffmpeg options are the ones the measurements
  produced, and the plan says what it expects. Nobody has heard a file it produced.
- **The four `chain` cautions are unit-tested, not heard.** A target above the limiter's working level is
  reported before the run; what a user makes of that sentence is not measured.
- **The window's own dialogs have not been exercised.** Electron was launched and the window opened, the
  engine came up and the interface loaded, a graceful close stopped the tree and a failed start drew the
  page that explains it — all recorded in §6. What is *not* checked is the interaction inside the shell: the
  multi-select file dialog, the Save dialog and `reveal`. Those need a person in front of the window, and
  `scripts/check_window.py` drives the page in a browser where `electronAPI` is absent by design.
- **`start.bat` and `scripts/bootstrap.ps1` have not been run end to end.** `bootstrap.ps1 -Doctor` was run
  and reports on every prerequisite against a machine that already has them all. The install path — winget,
  the portable downloads — is the same code the sibling ships and has not been exercised here.
- **The screenshot shows the empty window.** A queue with forty rows, a run in progress, a file that came
  out wrong: none of them has been looked at. `scripts/check_window.py` can capture all three and only
  captures the first.
- **No claim is made about colour, scaling or picture quality.** The engine never decodes the picture except
  to checksum sampled frames; it does not scale, crop or re-time anything.
- **The level is a peak and not a loudness.** Two files at the same peak can be six decibels apart in what a
  listener calls loudness. `GET /api/health` reports whether this machine's ffmpeg has `loudnorm` and
  `ebur128` — it has both — and nothing uses them. That is a decision (see `DESIGN.md` §8) and not an
  oversight, but it means "normalized" here means "same peak" and not "same perceived loudness".
