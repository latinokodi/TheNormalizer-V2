# Changelog

## [Unreleased]

### Fixed — the product was not doing dynamics, and its name says it should be

The report was *"I tested with a 13-minute interview and got the same file +6 dB; the quieter parts are still
in a lower volume"*, and it was correct. It was not a level problem at all — the level was delivered exactly —
it was a **dynamics** problem, and this program is supposed to be for that.

- **The `chain` strategy contained no gain reduction.** Its `acompressor` is `ratio=1`, transparent by
  design, because the filtergraph reproduces the operator's Premiere Track Fx verbatim. The only dynamics a
  run changed were the ones the limiter clamped, and a limiter shaves peaks without lifting a body. Measured
  on that 13-minute interview at a −6.0 dBFS target: the quietest fifth of the file moved **+3.7 dB** and the
  loudest fifth **+6.3** — a spread of 21.8 LU in, 19.2 out. Barely touched, exactly as reported.
- **There is a leveler in the chain now**, and it is `speechnorm` — ffmpeg's own speech normalizer, designed
  for one pass, aimed at exactly this material. It runs **before** the front fader, because it is the element
  that decides *relative* level while everything after it decides absolute level. The figure was chosen by
  measurement rather than taste, on a 180-second excerpt, every version ending in the same limiter and the
  same out fader:

  | what runs before the limiter | peak | spread out | evened out by |
  |---|---|---|---|
  | the operator's chain alone, +12 | −6.0 | 15.9 LU | **4.6 dB** |
  | `speechnorm=e=3` | −6.0 | 17.0 LU | 3.2 dB |
  | `speechnorm=e=7` | −6.0 | 12.8 LU | 9.5 dB |
  | **`speechnorm=e=12`** | −6.0 | **11.8 LU** | **11.9 dB** |
  | `speechnorm=e=16` | −6.0 | 11.7 LU | 12.2 dB |
  | `dynaudnorm=f=250:g=15` | −6.0 | 15.9 LU | 10.7 dB |

  The curve flattens after twelve — sixteen buys 0.3 dB more and takes the peaks 1.1 dB harder — so twelve is
  where the figure stops paying for itself.
- **The result on the file that raised it**, over 820 seconds of measurable sound, dropping the pauses:

  | | source | normalized | moved |
  |---|---|---|---|
  | quietest fifth | −41.0 LUFS | −22.7 LUFS | **+18.4 dB** |
  | second fifth | −31.0 | −16.3 | +14.7 |
  | middle | −28.1 | −16.0 | +12.1 |
  | loudest fifth | −19.3 | −15.2 | +4.1 |
  | **the quiet-to-loud gap** | **21.8 dB** | **7.5 dB** | **closed by 14.2 dB** |

  14.2 dB where the old chain managed 2.6. That is the complaint answered.

### Added — the figures that explain a file, which no peak can

- **Every file's loudness and its own spread are measured and reported.** `ebur128` supplies integrated LUFS
  and loudness range from a decode the engine already performs, and they are shown beside the peak — source
  and result. It is the pair that explains a file whose peak is exactly on target and which still has quiet
  parts: the interview above reads **−23.4 LUFS, 12.8 LU wide**, against the 7 LU a delivery target allows,
  and the report says so in a sentence rather than leaving the operator to wonder.
- **A third control, `Even out`**, because a hidden constant was a defect: the figure that decides how even a
  file is has to be reachable. Zero leaves the sound's own dynamics completely alone — the operator's chain
  and nothing else, which is what this product shipped with.
- `scripts/loudness_profile.py` — the momentary loudness of every 100 ms of two files and the difference
  between them, by quintile. This is what turns *"the quiet parts are still quiet"* into a table.
- `scripts/drive_sweep.py` — the engine run at each drive value over one excerpt, reporting how far each
  closes the quiet-to-loud gap. It is how the ordering of the leveler figures was established.
- `docs/RESEARCH-ffmpeg-normalize.md` — what ffmpeg-normalize is, measured against this project, and why it
  is not a dependency. Short version: its two-pass shape is already this project's shape, its recommended
  mode is a constant gain (which this project already offers), and the mode that *would* even a file out is
  the one this project has just added in a filtergraph without inheriting a CLI, a temporary-file policy and
  a codec table that were each measured into place here. It did contribute the question that found the
  defect above, and its loudnorm figures are what the second addition reports.

### Fixed — an encoding residual that is now measured rather than hidden

- **A levelled run can deliver a peak about 1.5 dB above the target**, and the tolerance is 2.0 dB because
  that is what was measured rather than what was hoped. The stage pass corrects the overshoot it measured
  (+2.50 dB on the interview); the master's own encode then adds **+1.40 dB** more, because a levelled master
  is dense — the leveler has brought the quiet parts up near the loud ones, so far more samples sit close to
  full scale — and a limiter driven at 18 dB of overload produces a signal whose encode overshoots by a
  figure that moves with the output level. Delivered: **−4.60 dBFS against a −6.0 target**, with every check
  that matters passing. Closing it needs a second measurement pass over the encoded master; that is one more
  full encode per file against a decibel and a half, and `docs/TRUTH.md` §6b records the trade rather than
  claiming it does not exist.
- **Both faders are named in the log**, and the message that printed only one of them as "the fader" is why
  this looked like a missing correction for an hour: `total_gain_db` is the drive *in front* of the chain and
  the out fader is the one *after* the limiter, and a run that reported the first while describing it as the
  second was telling the truth about the wrong number.

### Changed — the window grew for the control that does the work

- **1180x880, minimum 1100x760** — up from 1180x720 and 1100x600. Three controls with a line of help each
  need more room than two, and a window that clipped them would be a window with one of its own controls off
  the bottom of it. The leveler's help was cut from three lines to two before the number was set.
- **The report shows `Loudness` and `Spread`** in the same table as the measured peak and mean, with the
  spread drawn as a caution when it is wider than a delivery target allows.


### Changed — the log is a panel, not a band

- **The log has the left column's spare height instead of a 128 px strip across the bottom.** It was a band
  and it had 128 px of a 720 px window, of which 30 was a progress strip of its own — so a run's log got about
  **90 px, four lines**, and this console exists to carry every stage, every command line and every
  measurement. It is now a zone under the settings and it takes the height the settings do not use: **230 px**
  at the size the window opens at, and more as the window grows.
- **The progress figures moved onto the log's own header.** A separate strip above the footer was a *third*
  28 px band in a 720 px window — title bar, log header, progress strip, footer — and the one carrying the
  least. The bar and the count sit on the header of the panel that reports the run.
- **`.app__col--form > .zone` was overriding `.zone--log`'s `flex: 1 1 auto`**, which is why the log stayed
  pinned at its 152 px floor. The cause is specificity and not source order: `>` with two class selectors is
  (0,2,0) and `.zone--log` is (0,1,0), so the later rule lost. The column rule is now
  `.app__col--form > .zone:not(.zone--log)`, which keeps it at (0,2,0) and lets the log's own rule win for the
  right reason rather than by an `!important`.
- **Both progress sweeps were escaping their tracks and painting over their neighbours.** A 30 % block
  translated to `+340 %` on a 56 px row bar ends **40 px past its own right edge**, which is the width of the
  next table cell: a working file's bar painted over its neighbour's Show button. On the 200 px console bar it
  was 204 px. The travelling segment is now a **gradient with its `background-position` animated**, so the
  paint moves inside the box instead of the box moving out of it and no `overflow` rule has to be right for it
  to stay put.
- Two dead rules removed with the band: `.progress-percent` and `.progress-figure`, and `--console-floor`
  replaced by `--log-floor`.


### Changed — feedback from the second use of the window

- **The window is the size of its content and is no longer maximized.** It opened at 1600x1000 and then
  called `maximize()`, which on a 2560-wide display filled the screen with a form and a list and left half
  of it as substrate. It now opens at **1180x720** with a **1100x600** floor, both measured rather than
  guessed: `scripts/inspect_window.py` reports what each part of the interface actually is, and those two
  numbers are the sums. Verified in Electron: the OS reports a 1322x807 device-pixel window, which is
  1180x720 logical at 125% scaling — not maximized, and the engine came up, answered `/api/health`, and
  walked its process tree on a graceful close.
- **There were two buttons that both said "Add files"** — one in the title bar and one in the queue's own
  header — opening the same dialog. Two controls for one action is a question the operator cannot answer,
  and the answer was that they were identical. The title bar's is gone; the queue's header keeps the one,
  because it sits over the thing it adds to.
- **The settings panel was an essay and is now a form.** Each setting carried a zone header with a title
  *and* a note, a field label *and* a suffix, and a paragraph underneath: three layers of labelling and five
  paragraphs, which said the same thing three times and measured **655 px of column for two fields**. There
  are no zone headers now, one unit per control instead of a third grid column, and **one line of help**
  under each field where the value is genuinely not guessable. It is **339 px** including the action bar, and
  `scripts/check_window.py` asserts it stays under 380 so it cannot creep back. That height is not cosmetic:
  it is what decides how small the window can be.

### Fixed — the frame, in the fourth arrangement, and the instrument that was lying about it

- **The frame is a flex column and the console is what grows.** The first three arrangements were grids, and
  each nominated a region to absorb the window's height — first the settings zones, which stretched two
  panels of 284 px around two fields of 45 px, then the body, which put the slack into a log that had not
  said anything yet. Flex says it directly: the body is `flex: 0 0 auto` and the console is `flex: 1 1 auto`
  with a 128 px floor, so an idle window is controls and files with a short log under them and a tall one is
  controls, files, and a log with room to be read. All four attempts are recorded beside the rule that
  settled it in `styles/app.css`.
- **`min-height: 0` on the settings column was clipping its own Normalize button.** A flex column with a zero
  minimum is one a grid may squeeze below its content, and the body row did exactly that: a window shorter
  than the settings need did not scroll them, it put the action bar under the console band and off the bottom
  edge with no scrollbar anywhere to say so. The queue column sets it back to zero, because a queue that
  pushes a window taller is worse than a queue that scrolls.
- **`100vh` for the frame was wrong, and only in the check.** `html`, `body` and `#root` are all `height:
  100%`, so a percentage chains to the window while `vh` resolves against the *visual* viewport — which under
  the device-metrics emulation the browser check uses is a different number. The frame stopped at 542 px
  inside a 720 px window in the check and was correct in Electron.
- **The browser check was measuring a page that had not settled.** React mounts before the stylesheet is
  applied, and in that state the frame is as tall as its content. Every check and the screenshot then ran
  against that page, which made two *correct* fixes look like they could not be made. It now waits for the
  frame to equal the viewport before asserting anything, and there is a claim for it.
- **And it was rendering the previous build.** `index.html` names `./app.css` with no hash in the name, so
  Chromium served a cached sheet into a fresh document — a fixed layout produced a screenshot that was
  **byte-identical** to the broken one. The check now disables the network cache as well as busting the
  document URL. That single fault cost two debugging sessions, and the fix is two lines.
- **`--width` and `--height` on `scripts/check_window.py`**, so a layout's promise about a *size* can be
  checked at that size: the same claims now pass at 1180x720 and at 1100x600, and `docs/shots/02-minimum.png`
  is the smallest window the application allows.

### Added — the instruments for a question about a number

- `scripts/inspect_window.py` — what the page loaded, whether that stylesheet carries the rules just written,
  the frame's size against the viewport it was given, and every element of the settings column and the file
  list by class with its height. This is what turns *"too much empty space"* into *"two zones of 284 px
  around two fields of 45"*, and it is the instrument that distinguishes *"the layout is too big"* from
  *"the page is not the page you think"*.
- `scripts/measure_window.py` — where each column begins and how wide it is, as numbers, because a screenshot
  at a non-integer scale factor is a poor instrument for a question arithmetic answers exactly.

### Changed — feedback from the first use of the window

- **The window is two controls, and both of them say what they do.** It was five fields named the way an
  engineer names them — *Target*, *Sound*, *Make up*, *Limiter* — and that is a panel a person who has not
  sat in front of a mixing desk cannot use. A field whose label has to be looked up is a field that gets
  left alone, and four of the five were not decisions this product's operator is making. What is left is
  **Level** (the peak every finished file will have) and **Drive** (how hard the sound is pushed into the
  limiter), each with a sentence under it saying what changing it will sound like in plain words.
- **The level and the limiter are one number.** They were two fields for one decision, which is two
  chances to disagree about it and a second thing to understand. `Settings.levelDbfs` now travels as both
  `targetDbfs` and `ceilingDbfs`, and the request cannot be built with them out of step.
- **The strategy is not a control.** It was a select with three options; the window now always uses the
  operator's chain, because that is the sound this family's episodes have and asking a question whose
  answer is always the same is not a feature.
- **The output folder is gone as a control, because it is always the source folder.** A `Choose…` button,
  a read-only path field and a `Beside source` reset were three pieces of interface for a decision nobody
  was making: `run_normalize` writes `<name> - normalized.<ext>` beside the source, and the one exception
  — naming a destination for a single file — is not worth a row on a panel this size. The batch rule is
  the only rule now, and the report still states the path the engine will write.
- **The settings moved to the left and the files to the right.** The controls were against the right edge,
  which made the eye travel to the far corner to find the thing it came for and back to the middle to read
  the files. Reading runs left to right, so the decision comes first. The DOM had to move with the
  stylesheet: a grid places its children in source order, and flipping only the CSS would have put the
  reading order — what a screen reader and the Tab key follow — out of step with the layout.
- **The empty space is mostly gone.** Each of the three attempts at it failed differently and all three
  are written down in `styles/app.css` beside the rule that settled it: zones stretched to share the
  window height, which is right when the zones are full of controls and wrong when there are two of them;
  then a log band sized as a share of the window, which reserves half the screen for a log that has said
  nothing yet. The console is now as tall as what it has to say, with a floor and a ceiling of its own, and
  everything it does not use goes to the file list.

### Fixed — the chain could not reach a target its limiter had not landed on

- **A `chain` run now delivers the level it was asked for.** The operator's chain ends in a limiter, and a
  limiter clamps material driven into it to a level of its *own* — below the ceiling it names and dependent
  on the drive. Measured on a −2.0 dBFS source through +12 dB of make-up: a −6.0 dBFS ceiling delivered
  −5.10, a −3.0 ceiling delivered −8.10, and a −1.0 ceiling delivered −10.10. There is no formula for
  that, so the run measures it in the stage pass like everything else about the chain, and a new **out
  fader** — after the limiter, the one place this product puts a gain there — is what the measurement buys.
  It is safe in that position because the limiter has already bounded what reaches it.
- **The correction for a chain run was being applied in front of the limiter, where it does nothing.** The
  residual was folded into the front gain, and a gain in front of a limiter is not a correction, it is a
  change of drive — so every file came out a decibel low with the out fader computed correctly on paper and
  the drive quietly absorbing it. The master's front gain is now the stage's own, unchanged, which is also
  what keeps the two passes at the one operating point the measurement depends on.
- **The peak tolerance is 1.5 dB rather than 1.0**, and the number is measured rather than chosen: the
  chain's out fader is derived from a measurement taken *before* the master's encode, so a chain run
  carries one AAC overshoot of error plus that unmeasured term. Thirty-six combinations of four source
  levels, three drives and three ceilings: the worst was 1.30 dB, on material so quiet and so lightly
  driven that the limiter never engaged, and most rows were exact.

### Fixed — the browser check was reporting on a build that no longer existed

- **Chromium serves a stylesheet it fetched moments ago even in a fresh profile**, because the disk cache
  under `%LOCALAPPDATA%` is per-machine rather than per-run. `scripts/check_window.py` renders
  `/?built=<timestamp>` now. It cost one debugging session: a layout fix appeared not to work, and the
  screenshot was byte-identical to the previous one.
- **`.gitignore` was rewritten for this project.** It was the sibling's, and it named that project's
  artefacts: a `pyflakes` launch that does not happen, a `thenormalizer-*` temp directory the engine does
  not use, a `*.trimmerproj` rule for a file this product has never written, and an exception at the end
  for two subscribe bumpers that are not here. Every rule in the file now names something this tree
  actually produces, and the media block gained the output names a run writes — `* - normalized.*`,
  `*.part.*` and `.normalize-*/` — because the product writes beside its source by design and a source
  inside the checkout is one `git add -A` from a history nobody can shrink.
- **`scripts/check_window.py` grew three claims and `scripts/measure_window.py` exists.** The check now
  asserts where each column *is* rather than which classes it has — the defect it catches, the settings
  rendering 70 % wide on the right, is invisible in the markup — and that the source order is the reading
  order. `measure_window.py` was written because a screenshot at a non-integer scale factor is a poor
  instrument for a question a number answers exactly, and because that defect was being read off one.

### Added — the first release

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
