# TheNormalizer

Take video and audio files to a peak level you choose — **without re-encoding the picture**. Windows
desktop, offline, no account.

```
source ──▶ gain ──▶ [ the chain ]  ──▶ aac ──▶ the master container
   │                                              ▲
   └───────────── -c:v copy ──────────────────────┘
```

---

## Quick start

Double-click **`start.bat`**. That is the whole of it.

It works that way on a Windows PC with **nothing installed on it**. `start.bat` finds or installs everything
the application needs, and then opens the window:

| It needs | What happens |
|---|---|
| **Python 3.10+** | used if the machine has a suitable one, otherwise winget installs it, otherwise the official installer runs quietly for the current user |
| **Node.js 18+** | used if present, otherwise winget, otherwise the official archive is unpacked into `.tools\node` |
| **ffmpeg and ffprobe** | used from `PATH` if present, otherwise a portable build is unpacked into `.tools\ffmpeg\bin` |
| **aiohttp** | installed into the project's `venv` from `backend/requirements.txt` |
| **npm's trees, and Electron** | installed into `node_modules` and `frontend/node_modules` |
| **the interface** | built, whenever a source file is newer than the bundle |

No administrator is needed at any point, and nothing is written outside this folder and `%LOCALAPPDATA%`.

**If the window does not open**, it says why on the page it shows instead: the interpreter it tried, the
port, and the last thing the engine printed. Running
`powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -NoLaunch` re-does the provisioning and
prints the whole story, and `-Doctor` reports every prerequisite without changing anything.

---

## What it does

Add media — several files at once, video or audio — choose the level, press **Normalize**.

| Control | What it decides |
|---|---|
| **Level** | the peak every finished file will have, in dBFS. −6.0 by default, which is where a stitched episode sits |
| **Even out** | how far the quiet passages are lifted toward the loud ones. 12 is where this was tuned; **0 leaves the sound's own dynamics completely alone** |
| **Also write** | an uncompressed WAV, a 320 kbps MP3, or both, beside each master |
| **Drive** | how hard the sound is pushed into the chain's limiter, which decides how hard the peaks are squashed on the way to the level. +12 dB is the edit bay's setting |

Three figures, and only one of them is about loudness. **Level** decides how loud the file is. **Even out**
decides how far apart its own quiet and loud parts are — the control that makes an interview sound like one
recording rather than two. **Drive** decides how hard the peaks are pushed down to get there, which is a
question about character rather than about level, because the level is reached whatever it is set to.

Measured on a 13-minute interview, 820 seconds of sound: the quietest fifth of the file moved **+18.4 dB**
against the loudest fifth's **+4.1**, taking a **21.8 dB** spread down to **7.5 dB**. With `Even out` at 0 —
the operator's filters and nothing else, which is what this product did before — the same file closed 2.6 dB
of that gap. If a file comes out flat or squashed, lower it; if the quiet parts are still quiet, raise it.

Each finished file is written **beside its source**, named `<name> - normalized.<ext>`. **Nothing is ever
written over** — a name that is taken steps aside to `2`, then `3` — and your originals are never touched.

### The picture is copied, not re-encoded

That is the whole reason a video costs seconds rather than minutes. Only the sound is encoded, and the
frames in the output are the source's frames byte for byte — which the product proves rather than asserts:
after every run it checksums sampled frames from the output against the file they came from and reports how
many matched. The reference run reports **10/10 bit-identical**.

A stream copy is only legal if the new container can hold what is being copied, so a source whose container
cannot carry an AAC soundtrack is written into one that can: `talk.webm` becomes
`talk - normalized.mkv`, and `talk.wav` becomes `talk - normalized.m4a`. The plan says so before the run.

### The level is measured, not assumed

A normalizer that applies an arithmetic gain and hopes has no way to tell you what it delivered. This one
encodes a **stage pass** through the same codec the master will use, decodes it, measures what came back, and
sets the master's gain from that measurement — so the level you asked for is the level the file has, and the
report says which one that was:

```
delivered peak   -6.00 dBFS
off target by    +0.00 dB
level moved      -2.10 → -6.00 dBFS (-3.90 dB)
```

Then it measures the file it wrote: the peak, the mean, the difference from the target, whether the sound
decodes, whether the sound kept the source's rate and channel count, whether the sound and the picture end
together, whether the container is the one that was asked for, and whether the picture really is the
source's. Each is `passed`, `failed` or **`not checked`** — and the third is not a quiet pass. A file with no
picture says so rather than reporting a check it could not run as one it passed.

### Three ways to get there

| Sound | What it does | What it reaches |
|---|---|---|
| **One gain** | a single fader. Nothing is compressed and nothing is limited | any target, from any source |
| **The operator's chain** | the Premiere Track Fx this family's edit bay uses: +12 dB of drive into a limiter | the limiter's own level, at most |
| **Gain, then a limiter** | the fader to the target, then a limiter at the target | any target, without clipping on the way |

The default is **One gain**, and that is a measurement rather than a taste: a limiter is a ceiling, so
everything driven into it comes out at about the same level and a chain containing one cannot deliver an
arbitrary target. The window says so when you pick that one and ask for a level above what the limiter can
reach.

### The sound files are the sound the episode plays

Tick **WAV**, **MP3** or both and the run also writes the finished sound beside the master, with its own
name. They are decoded out of the file the run has just written — and the MP3 is encoded *from that WAV*,
not from the episode again — so they are the sound the master plays rather than a second build of it that
could differ in a rounding nobody would look for.

An existing `.wav` or `.mp3` never costs you the master: the exports are skipped together with a sentence
naming the file in the way, and the normalized file still goes out.

---

## How it is built

| Piece | What it is |
|---|---|
| `backend/normalizer/` | the engine: `process.py` (the only module that spawns anything), `media.py` (what a file is), `normalize.py` (the plan, the graphs, the run, the sound files), `verify.py` (measuring what was written) |
| `backend/server.py` | the loopback HTTP and event-stream API the window talks to. Transport only — no normalizing logic |
| `frontend/` | React + Vite + TypeScript, plain CSS over a design-token file. No CSS framework |
| `electron/` | the window: starts the engine, proves it is *this* engine, opens a page on it, and opens at 1180x880 — the size of its content — rather than maximized |
| `scripts/bootstrap.ps1` | everything `start.bat` deliberately does not do |

The window and the engine share one origin: the engine serves the built page. That is not a preference — a
Vite build is an ES module and Chromium refuses a module script loaded from `file://`, so `loadFile` cannot
work, and every engine call would be cross-origin as well.

Configuration:

| Variable | Description | Default |
|---|---|---|
| `PORT` | the loopback port the engine binds, and the one the shell and the CSP expect | `8767` |
| `THE_NORMALIZER_DEV` | a Vite dev-server URL to load instead of the built page | unset |
| `THE_NORMALIZER_FFMPEG` | an explicit `ffmpeg.exe`, taking priority over `PATH` | unset |
| `THE_NORMALIZER_FFPROBE` | an explicit `ffprobe.exe`, taking priority over `PATH` | unset |
| `THE_NORMALIZER_SKIP_TESTS` | `1` skips the engine's checks during provisioning | unset |

---

## Tests

```bat
venv\Scripts\python.exe -m pytest backend\tests -q     :: 76 passed
npm --prefix frontend run typecheck                    :: exit 0
npm --prefix frontend test                             :: 28 passed
```

The engine suite runs in about a second and launches no ffmpeg, so it needs no footage. That is also its
weakness: **no test in it performs a real run.** Three scripts are the evidence, and each is run by hand:

```bat
venv\Scripts\python.exe scripts\check_normalize.py    :: the real engine on files it builds itself
venv\Scripts\python.exe backend\server.py             :: then, in another window:
venv\Scripts\python.exe scripts\check_window.py       :: the built page in a real browser
```

`check_normalize.py` builds a tone, a quiet tone, a loud tone, digital silence and a 720p video, runs the
whole engine over each, and prints every measurement — the level it planned, the level it applied, the level
it delivered, and every check. **Five defects in this engine were found only by running it**, and they are
recorded in `docs/DESIGN.md` §2.

`check_window.py` drives Chromium over the DevTools protocol against the running engine: React mounting on
the engine's own origin, the footer getting its health answer, the chain's figures enabling at the
operator's own values, a target the limiter cannot reach being flagged before the run, and nothing scrolling
or clipping at the declared minimum. It writes `docs/shots/01-empty.png`.

---

## Documentation

| File | What is in it |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | what the product is required to do, as 28 numbered requirements each with the check that decides it |
| [`docs/DESIGN.md`](docs/DESIGN.md) | the decisions and the five measurements behind them — read §2 first |
| [`docs/TRUTH.md`](docs/TRUTH.md) | every claim paired with its check, and an explicit list of what is **not** checked |
| [`docs/SKILLS-APPLIED.md`](docs/SKILLS-APPLIED.md) | the practices this build was held to, and what each one changed |
| [`CHANGELOG.md`](CHANGELOG.md) | what was built, what changed from the sibling, and the five defects this build found in itself |

## Licence

The bundled typefaces are **SIL Open Font License 1.1** — see `frontend/src/fonts/LICENCE.md`. `ffmpeg` is
called as a separate process and is not redistributed here; `start.bat` uses what the machine has or
unpacks a portable build into `.tools`.
