# Changelog

## [Unreleased]

### Added

- The whole product. TheNormalizer-V2 was cloned from **TheStitcher-v2** for its shell, its design tokens
  and its proven media-layer idioms, and the engine was written for this domain.
- `backend/normalizer/` — the engine, in four modules: `process.py` (the only module that spawns anything:
  `ffmpeg`, `ffprobe`, the `volumedetect` measurement and the argument-list discipline), `media.py` (what a
  file is: its streams, its level, and the checksums that prove a stream was copied), `normalize.py` (the
  request type, the refusals, the plan, the filtergraphs, the two-pass run and the sound files) and
  `verify.py` (measuring what was written).
- **Three strategies, and `gain` is the default.** `gain` is one fader and reaches any target; `chain` is
  the operator's Premiere Track Fx and reproduces what a stitched episode sounds like; `ceiling` is the
  fader with a limiter at the target, for lifting quiet material hard. The default is `gain` because a
  limiter is a ceiling and a strategy containing one cannot deliver an arbitrary target — which is a
  measurement, not a preference (`docs/DESIGN.md` §2.2).
- **The make-up gain and the limiter ceiling are the operator's to set**, in decibels and dBFS, and they
  reach ffmpeg in the units ffmpeg wants: the drive as a `volume` fader (because `acompressor`'s `makeup`
  is a multiplier with a floor of 1.0 and cannot attenuate) and the ceiling as `alimiter`'s `limit`,
  converted from dBFS to linear amplitude. Both are reported in the plan, and the window explains them.
- **The level is measured rather than modelled.** A stage pass encodes through the master's own codec, the
  result is decoded and measured, and the master's gain is corrected by what that measurement found. This
  exists because AAC overshoots a PCM master by about 0.6 dB on decode and no limiter upstream can prevent
  it — the ringing happens after the samples. See `docs/DESIGN.md` §2.3 and §2.4 for the two designs that
  got this wrong and the arithmetic that settled it.
- **The picture is stream-copied, always.** Every command that maps it carries `-c:v copy`, exactly one
  command maps it, and `verify` checksums sampled frames against the file they came from. Frames are matched
  by presentation time rather than by position, because an input seek lands at different offsets in two
  files with different keyframe layouts — which is the defect that made the check report a bit-exact copy
  as broken (4/11 frames "differing" on the reference clip).
- **The muxer is named to ffmpeg rather than inferred from the extension**, and the finished file's
  `format_name` is checked against it. This is the WAV-full-of-AAC defect: `-c:a aac` into a `.wav` produces
  a valid WAV header with ADTS frames in the data chunk, which `ffprobe` reports happily and which fails to
  decode. A source whose container cannot hold AAC is written into one that can — `.wav` → `.m4a`,
  `.webm` → `.mkv` — and the plan says so before the run.
- **Video and audio, from one window.** The file that decides what happens is the file: a source with a
  picture keeps it (copied) and one without becomes a sound file, and the container follows from that.
- **A queue of files, run one batch at a time.** `POST /api/normalizations` takes every source and returns
  **201** with a job per file and where each will go; `GET /api/normalizations/current` is the singleton
  (**204** when idle); `DELETE` on it is the cancel. A second batch is **409** with `reason: "busy"`.
- **Progress streamed per file.** Every stage, every exact command line, every position ffmpeg reports and
  every measurement, on `/api/events`, tagged with the job it belongs to. A subscriber that stops reading
  cannot stall the work: each has a bounded queue and every publish is non-blocking.
- **A normalized copy is written beside its source and a name that is taken steps aside** —
  `talk - normalized.m4a`, then `2`, then `3`. Nothing this product did not choose is ever written over,
  and the check is taken twice: when the run starts and again at the rename that publishes the result.
  The original is never touched.
- **A WAV and a 320 kbps MP3 beside each master**, off by default. They are decoded out of the file the run
  has just written — the MP3 from the WAV, not from the episode again — so they are the sound the master
  plays rather than a second build of it. An occupied `.wav` or `.mp3` is skipped with a sentence rather
  than refusing the run, and the outcome reports the files **the run wrote** rather than the files at those
  paths.
- **`GET /api/health` says what this machine can do**: whether its ffmpeg has an AAC encoder at all, whether
  it has `libmp3lame` (which decides whether the MP3 box is offered) and whether it has the EBU R128 filters
  (reported, not used). The answer names the product, because three applications in this family answer
  `/api/health` and the port is the only thing that tells them apart.
- **The window**: a queue with each file's measured level and where it has got to, the settings that apply
  to all of them, the selected file's report — its facts, the filtergraph that will be applied, every
  caution and then every check — and a console carrying the run's own log. `F5` starts, `Esc` stops, `Del`
  removes a row, `Ctrl+O` adds files.
- `scripts/check_normalize.py` — builds its own fixtures with ffmpeg (a tone, a quiet tone, a loud tone,
  digital silence and a 720p video with a quiet soundtrack), runs the real engine over each, and prints
  every measurement. It exists because `backend/tests` launches no ffmpeg: **five defects in this engine
  were found only by running this**, and all five are in `docs/DESIGN.md` §2.
- `scripts/check_window.py` — starts Chromium with a debugging port, drives the built page over the DevTools
  protocol, and checks that React mounts on the engine's own origin, that the footer gets its health answer,
  that the chain's two figures enable at the operator's own values, that a target above the limiter's working
  level is flagged before the run, and that nothing scrolls or clips at the declared minimum. It captures a
  screenshot, which is the only way to check a layout.
- `scripts/bootstrap.ps1` — finds or installs Python, Node and ffmpeg, builds the environment and the
  interface, and reports every prerequisite with `-Doctor`. Inherited from the sibling with the PyAV
  dependency, the port and the product name corrected.
- Docs: `docs/SPEC.md` (28 numbered requirements, each with the check that decides it),
  `docs/DESIGN.md` (the decisions and the five measurements behind them), `docs/TRUTH.md` (every claim
  paired with its check, and an explicit list of what is **not** checked), and this file's sibling
  `docs/SKILLS-APPLIED.md`.

### Changed from the sibling this was cloned from

- **The port is 8767**, not 8765 or 8766. All three products' APIs have `/api/health` and a planning route,
  so a warm port would be mistaken for a slow start. The shell requires the health body to name this product
  and reports a stranger as a stranger.
- **The window minimum is 1280 × 820**, re-derived with the shell's `minHeight`: a queue, a settings column
  with five zones, a report and a log band is more content than the sibling had, and the number is checked
  by rendering the page at that size rather than by counting rows.
- **The tagline is the product's own sentence and never the ffmpeg build string.** An ffmpeg version is
  forty characters of a bar whose job is to say what this application is, and it changes with the machine.
  The footer carries the health.
- The two `.field-row__note` controls whose `<label htmlFor>` pointed at inputs with no `id` were connected:
  the number fields now carry `id="target"`, `id="makeup"` and `id="ceiling"`, so clicking a label focuses
  its field and a screen reader can name it.

### Fixed

Defects this build found in itself. All five were found by running the real engine, not by reading code —
which is why `scripts/check_normalize.py` exists.

- **`volumedetect`'s figures were never parsed.** The pattern required the number immediately after the
  colon; ffmpeg prints `mean_volume: -30.1 dB`, with a space, from a `%6.1f` — while the `n_samples:` line
  above it has none. Every file looked like it had no level, and the failure arrived as this engine's own
  sentence about ffmpeg not reporting one, which reads as a broken ffmpeg. Two bugs were stacked here: the
  capture group is `max`, not `max_volume`, so even a matching pattern looked the key up wrongly.
- **A `.wav` source was written into a `.wav` container, with AAC in it.** The first version of the
  container table mapped a `.wav` to a `.wav` on the reasoning that WAV holds anything. It does not: ffmpeg
  writes a valid header, `ffprobe` reports it correctly, and the first decode fails. The extension and the
  muxer are now one decision returned together, and `verify` asks the finished file what it is.
- **The frame checksum parser required seven fields where `framemd5` has six.** Every row was dropped and
  the frame check reported *"no presentation time was common to both decodes"* — a sentence that reads like
  a container problem and was a miscounted list.
- **Frames were compared by position rather than by presentation time.** An input seek lands at different
  offsets in two files with different keyframe layouts, so a bit-exact copy was reported as 4/11 frames
  differing. Matching on the presentation time makes the check exact.
- **`output_for` stripped the extension before the container was known**, and `Path('talk.en - normalized').stem`
  is `'talk'` — the last dot is in the *name*. A source whose own name had a dot lost a word. The suffix is
  now inserted before the source's extension and the container replaces it afterwards.

### Known gaps

`docs/TRUTH.md` §7 is the list, and it is not a formality: no test in `backend/tests` runs a real ffmpeg; the
target has been measured on an 11-second clip rather than on a 17-minute episode; `chain` on real material
has not been listened to; the screenshot shows the empty window; and the level is a **peak**, not a
loudness — two files at the same peak can be six decibels apart in what a listener calls loudness.
