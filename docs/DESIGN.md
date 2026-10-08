# Design

The decisions, and the measurements that produced them. Where a decision rejects an alternative, the
alternative is named, because a decision whose alternative has been forgotten gets re-litigated.

This engine's shape was settled by **measurements it was built to make**, not by reasoning about what
ffmpeg ought to do. Five of them changed the design; §2 is all five, and it is the section to read first.

---

## 1. One pass per file, and why the picture is copied

```
source ──▶ fader ──▶ [ the chain ]  ──▶ aac ──▶ the master container
   │                                                  ▲
   └────────── -c:v copy ─────────────────────────────┘
```

The sound is filtered and encoded; the picture is copied. For a video that is the whole reason the product
is fast: a 17-minute 1080p file costs one audio encode rather than a video re-encode, and the picture in
the output is the source's picture byte for byte — which `verify` proves by checksumming sampled frames
against the file they came from.

**Rejected:** re-encoding the picture. Minutes instead of seconds and a generation of quality, for a change
that lives entirely in the sound.

**Rejected:** copying both streams through one pass with a `-filter_complex`. This was never possible: the
sound has to be *processed*, and a stream copy cannot process anything. The two are separated for a reason
that is a fact about the operation rather than a preference about the code.

---

## 2. The five measurements that shaped the engine

### 2.1 `acompressor`'s `makeup` is a multiplier with a floor of 1.0

`makeup=3.9812` is +12 dB, and `3.9812` is `10 ** (12 / 20)`. An operator writing `makeup=12` asking for
twelve decibels gets **+21.6 dB** and a file that clips. And the option's range starts at 1.0, so it
**cannot attenuate at all** — a source that is too loud for the target cannot be brought down through it.

**Decision:** the drive is a `volume` filter, in the operator's own decibels, and the chain's own `makeup`
is left at unity. `makeup_multiplier` does the conversion in one place, so a control that says `+12 dB` and
a command that says `makeup=12` cannot both exist.

### 2.2 A limiter is a ceiling, and it is not where it says

The chain was characterised against a tone at every drive level, and the result is the single most
consequential measurement in this build:

| drive into the operator's `limit=0.5012` | what comes out |
|---|---|
| +0 to +18 dB | in + 12 dB — transparent, just the make-up |
| +24 dB | **−3.1 dBFS** |
| +36 dB and above | **−9.0 dBFS** |

`alimiter` engages about **three decibels below** the linear value it is handed. Two consequences follow,
and both are load-bearing:

* **A chain containing a limiter cannot deliver an arbitrary target.** Everything driven into it comes out
  at about the same level, so the file peaks where the limiter puts it rather than where the target says.
  Measured: a source at −40.5 dBFS, a −9.0 target, and the chain delivers **−6.1 dBFS** — and no gain after
  the limiter changes that, because the limiter is a ceiling.
* **A fader placed after the chain clips.** With the chain first and the fader second, a −27.1 dBFS source
  asking for −6.0 came out at **0.0 dBFS**: the chain's own make-up had already used the headroom, and the
  fader then drove a signal that was already at full scale.

**Decision:** the strategies are named for what they are, and the honest one is the default. `gain` has no
limiter in it, so its fader is the target's arithmetic and it reaches any target. `chain` is the operator's
own sound, at a target its limiter can reach — and the plan says which of the two happened before the run
rather than after it.

### 2.3 `aac` overshoots on decode, by about 0.6 dB

A tone written into PCM at exactly **−6.00 dBFS** comes back out of an AAC decode at **−5.40 dBFS**. The
mean is unchanged, so it is not a gain: it is the codec's reconstruction ringing past the sample values it
was given. FLAC, with no such ringing, reproduces −6.00 exactly.

Nothing upstream can prevent it. A limiter at −6, −5, −4, −3, −2, −1 or 0 dBFS all produced the same
−5.40, because the limit is applied to samples and the overshoot happens after them.

**Decision:** the stage pass encodes **through the master's own codec**, decodes the result and measures
it, and the master's gain is corrected by what that found. The correction converges rather than solving
exactly — the second error is half the first, because the overshoot at one level is not quite the overshoot
at another — and `PEAK_TOLERANCE_DB` is 1.0 dB for that reason and not because 0.3 was inconvenient.

### 2.4 A stage pass has to run at the master's operating point

The first two designs both failed here, in opposite directions, and the reason is worth writing down
because it is not obvious from either of them.

* **Design one** put `target − source peak` in front of the chain. It delivered 0.0 dBFS, clipped.
* **Design two** ran the stage **dry** and derived the correction from the difference between the chain's
  transparent output and an encode of it *through the fader*. That difference is mostly the fader, not the
  codec: it measured **+9.7 dB** where the true overshoot is 0.6, and the correction built from it drove the
  master 9.7 dB too quiet.

**Decision:** both passes run the same graph at the same gain. The stage carries the plan's own arithmetic;
the master carries that plus `target − what the stage measured`. Written out, with `A` the source's peak,
`V` the nominal gain, `C(x)` the whole encode-and-ring path and `o` its overshoot:

```
stage:   decoded(A) = C(A + V)  = A + V + o
master:  decoded(B) = C(A + G)  = A + G + o
want:    decoded(B) = t
⇒        G          = t − A − o = V + (t − decoded(A))
```

The correction is therefore a measurement of *this material through this codec*, not a model of a codec.

### 2.5 ffmpeg's WAV muxer accepts a codec the container cannot frame

`-c:a aac` writing to a `.wav` produces a **valid WAV header** with ADTS frames in the data chunk.
`ffprobe` reports it happily — `format_name: wav`, one `aac` stream, the right duration — the muxer exits
0, and the first decode fails with `decode_band_types: Input buffer exhausted before END element found`. A
run that called that file a success would have reported a number and delivered something no player can
open. It is the defect that produced R7 and R14.

**Decision:** the muxer is named explicitly in every command rather than inferred from the extension, the
container extension is chosen from a table of what each muxer can actually carry, and `verify` asks the
finished file what it is. All three, because the failure is silent in two of the three places.

---

## 3. The two-pass run, in order

```
1. stage   the strategy + the plan's gain → aac → a temporary file in the master's container
2. measure the temporary file's samples, then decode it and measure what the decoder hands back
3. master  the strategy + the corrected gain → PCM
4. mux     the source's picture (-c:v copy) + that PCM → aac → <name>.part.<ext>
5. exports optional WAV, then the MP3 encoded from that WAV
6. rename  <name>.part.<ext> → <name> - normalized.<ext>
```

Everything is written inside a temporary directory beside the output, and the rename at the end is what
makes a cancelled or failed run leave the destination exactly as it was. The directory is beside the output
rather than in the system temp so the rename is a move within one volume.

**The cost** is one extra encode and two extra decodes of the audio per file. On a 17-minute episode that is
seconds; the picture is untouched in every pass. **The return** is that the level is measured rather than
assumed, which is the product's whole claim.

---

## 4. Why the container is a table and not a rule

AAC belongs in the MP4 family and is accepted in Matroska. It is not accepted beside a video in `.webm`,
which is VP8/VP9/AV1 with Vorbis or Opus. A source whose container cannot hold it is written into one that
can — `talk.webm` becomes `talk - normalized.mkv`, and `talk.wav` becomes `talk - normalized.m4a` — rather
than being refused, because telling an operator that a file they have in front of them cannot be normalized
because of the box it came in is the tool failing at its own job.

The **extension and the muxer are one decision**, returned together by `container_for`, because §2.5 is what
happens when they are two.

**Rejected:** keeping the source's extension and hoping. That is the WAV-of-AAC defect, and it is a file
that probes correctly and plays as nothing.

---

## 5. Frame comparison is by presentation time, not by position

An input seek (`-ss` before `-i`) lands on the keyframe at or before the requested time and discards
forward — so two files that share a picture but not a keyframe layout decode to *the same frames starting at
different offsets*. Measured on the reference clip: seeking both files to 2.000 s gave the source frames at
pts 1 and 2 and the output frames at pts 0 and 1, and comparing the lists by position reported that the
copy had failed when the frame at pts 1 was bit-identical in both.

`framemd5` carries each frame's presentation time, so the comparison matches on that. A frame's presentation
time is what it *is*; its position in a seeked decode is an accident of where the decoder landed.

A second defect lived in the same function and is worth naming because it is the same mistake in a different
costume: the parser required **seven** comma-separated fields where a `framemd5` data row has **six**, so
every row was dropped and the check reported *"no presentation time was common to both decodes"* — a
sentence that reads like a container problem and was a miscounted list.

---

## 6. Silence is a value

16-bit PCM cannot represent exact zero for a negative sample: the range is −32768..32767, so a file of
nothing but zeros has a tiny DC offset, and a generated silence is usually one LSB of dither as well.
Measured: `anullsrc` written as 16-bit stereo reads `max_volume: -91.0 dB`.

Without a threshold, "digital silence" is a case the product never sees, and a silent file is instead a file
**85 dB below the target** — so the run computes an +85 dB gain, applies it to the noise floor, and reports
that as normalization. `SILENCE_DBFS` is −90, which is below the noise floor of every microphone, converter
and codec this product will ever be handed.

---

## 7. The window

- **The port is 8767, and answering is not enough.** The siblings bind 8765 and 8766, all three APIs have
  `/api/health` and all three have a planning route, so a warm port would be mistaken for a slow start. The
  health check requires the body to name this product, and a stranger on the port is reported as a stranger.
- **One origin.** The engine serves the built page, because a Vite build is an ES module and Chromium
  refuses a module script from `file://` — and because every engine call would otherwise be cross-origin.
- **`sandbox: true`.** Inherited as `false`; the page reads JSON over loopback and draws it, so it has no
  use for a node handle.
- **Shutdown walks the process tree.** `child.kill()` terminates Python and nothing else, so an orphaned
  ffmpeg survives holding the output file open. `taskkill /T /F` is what walks it.
- **`backend.on("error")` exists.** A spawn that cannot find an interpreter emits `error`, and an unhandled
  `error` on a `ChildProcess` is an uncaught exception that takes the application down before it can draw
  the page that would have explained it.
- **`reveal` cannot launch anything.** A file is *shown in its folder*; only a directory is handed to the
  shell to open.
- **The tagline is the product's sentence and never the ffmpeg build string.** An ffmpeg version is forty
  characters of a bar whose job is to say what this application is, and it changes with the machine rather
  than with the product. The footer carries the health.
- **The window's minimum is 1280 × 820**, and it is checked rather than declared: a real browser renders the
  page at that size and asserts that the document does not overflow and that no control runs past the right
  edge (`scripts/check_window.py`). The frame never scrolls; the queue, the log and the report scroll inside
  themselves.

---

## 8. What was rejected

| Rejected | Why |
|---|---|
| Re-encoding the picture | minutes instead of seconds, and a generation of quality, for a number that lives in the sound |
| Inferring the muxer from the extension | the WAV-full-of-AAC defect: a file that probes correctly and plays as nothing |
| A fader after the chain | it cannot lift the result above the limiter's ceiling, and it clips outright when the material already reaches it (measured: 0.0 dBFS) |
| A fader before the chain | the chain's own make-up then drives a signal already at full scale (measured: 0.0 dBFS, clipped) |
| Modelling the codec instead of measuring it | AAC's overshoot is 0.6 dB on a tone and not a constant, and the model would have been wrong in the direction that looks fine |
| A stage pass that runs dry | its "overshoot" is mostly the fader — 9.7 dB where the true figure is 0.6 |
| `acompressor`'s `makeup` as the drive | a multiplier from 1.0 up: it cannot attenuate, and `makeup=12` is +21.6 dB |
| `loudnorm` as the measure | it is not in every ffmpeg (this machine's `essentials` build has neither it nor `ebur128`), and the level this product delivers is the level its sibling delivers |
| Refusing a name that is taken | a normalized copy is derived from a file that is still there, so the second press of a button is not a mistake worth a red sentence |
| Asking whether to replace the destination | the answer would have to be a request field that *permits* it, and that field is the promise gone. The product steps aside, or refuses, instead |
| `asyncio.Queue` for the event hub | the producers are a worker thread and ffmpeg's stderr reader; `asyncio.Queue` is not thread-safe |
| `pytest-asyncio` | the suite's one async need is a real HTTP client against a real socket, and `asyncio.run` in a helper supplies a loop with no new dependency |
| Pydantic for the request models | a dependency added to hold five paths and three numbers, when one function that names a missing field is the whole requirement |
