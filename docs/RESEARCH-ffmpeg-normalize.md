# ffmpeg-normalize: what it is, and whether it would improve this project

**Researched** against the project at commit `65565d1`, and every claim below that could be measured on this
machine was measured rather than taken from documentation. The verdict is at the end; the short version is
**no, do not adopt it — but two of its findings are worth taking, and one of them is not about it at all.**

Sources: [the repository](https://github.com/slhck/ffmpeg-normalize), [the normalization
options guide](https://slhck.info/ffmpeg-normalize/usage/normalization-options/), [the
FAQ](https://slhck.info/ffmpeg-normalize/advanced/faq/), [PyPI](https://pypi.org/project/ffmpeg-normalize/).

---

## 1. What it is

A well-regarded Python utility by Werner Robitza for batch-normalizing audio with ffmpeg. It is **not a
library that processing is written against** so much as a CLI with a Python API bolted on, and its features
are worth listing because several of them this project already has:

| Its feature | This project |
|---|---|
| EBU R128 loudness normalization, two-pass by default | ✗ — this one is peak-based |
| RMS-based normalization | ✗ |
| Peak normalization to a target | **✓ — this is what it does** |
| Selective audio-stream normalization | ✗ |
| Skip files already at target (`--threshold`) | ✗ |
| Batch/album normalization preserving relative loudness | ✗ |
| Video file support, picture preserved | ✓ |
| Self-documenting output (`ENCODER_SETTINGS` tag) | ✓ in a different form — the report and the log |
| Automatic output codec per container | ✓ — the `CONTAINERS` table |
| Presets | ✗ — this project has two controls instead |

## 2. Where it is genuinely ahead of this project

**Two things, and only two.**

**2.1 Loudness targets instead of peak targets.** `loudnorm` measures ITU-R BS.1770 loudness and can be told
to hit a LUFS target. This project hits a peak. The difference matters and `docs/TRUTH.md` §7 already admits
it: two files at the same peak can be six decibels apart in perceived loudness, and for a folder of interviews
that is the actual complaint. Measured on the file that prompted this research:

| | sample peak | true peak | integrated loudness | loudness range |
|---|---|---|---|---|
| source | −1.10 dBFS | −1.11 dBFS | **−23.4 LUFS** | **12.8 LU** |
| this project's output | −6.20 dBFS | −6.23 dBFS | **−19.2 LUFS** | **9.9 LU** |

The peak was delivered exactly as promised and the *loudness* still moved 4.2 LU, because peak and loudness
are different quantities. If the operator's requirement is "these files sound the same as each other", a
loudness target is the right instrument and a peak target is not.

**2.2 True peak as a measured figure.** ffmpeg's `loudnorm` reports `input_tp` — an oversampled estimate of
the inter-sample peak — in its first pass, at no extra cost beyond a decode this project already performs.
**Measured on this material the gap is 0.01–0.03 dB**, so it is not worth the change *for these files*; it
would matter for material that has been through a lossy codec, where inter-sample peaks of 0.5–1 dB are
ordinary. This is a "know the number is available, and here is what it was here" finding, not a defect.

## 3. Where it would not help — and this is the important part

**3.1 It cannot even out a file either, in its default and recommended mode.** The FAQ is explicit that
linear mode "applies a constant gain adjustment across the entire audio" and is "generally preferred because
it preserves the original dynamic range". That is the same thing this project's `gain` strategy does. Adopting
ffmpeg-normalize would not have fixed the report that prompted this research — *"the quieter parts are still
in a lower volume"* — because a constant gain is a constant gain in any tool.

**3.2 The mode that *would* even a file out has a caveat that lands exactly on this material.** `loudnorm`
falls back from linear to **dynamic** mode when the input's loudness range exceeds the target LRA. The
documentation is explicit: *"linear normalization alone cannot reduce the loudness range without dynamic
processing"*. The source above has **12.8 LU**, against loudnorm's default LRA target of 7. So for this file
and every file like it, ffmpeg-normalize would either fall back to dynamic mode — which is a compressor with a
look-ahead limiter, i.e. the thing the operator was asking for — or apply the `--keep-loudness-range-target`
mitigation and deliver a constant gain and be back where it started.

That is worth stating plainly because it is the crux: **the operator's request is a dynamics request, not a
normalization-algorithm request, and no choice of normalizer changes that.**

**3.3 Its architecture is this project's architecture.** Two passes: one to measure, one to apply the measured
values. `docs/DESIGN.md` §2.3 and §2.4 arrived at the same shape from measurement — a stage pass through the
master's codec, measured, and the master corrected by what was found. There is nothing to import from that
overlap, and its measured values are loudness where this project's are peaks.

**3.4 As a dependency it would cost more than it gave.** It shells out to ffmpeg with its own argument
construction, its own temp-file handling (temporary files were only dropped in 1.38.0) and its own
container/codec table — all three of which this project has already measured its way through, with five
defects recorded in `DESIGN.md` §2 that would have to be re-verified through somebody else's construction.
It would also put a hard dependency on `loudnorm` being present, which this project currently *reports* rather
than requires (`/api/health` has a `loudnorm` field).

## 4. What it *did* contribute, indirectly, and what was tested because of it

Reading it is what prompted the tests behind two things this project now knows for certain.

**4.1 The limiter is sound.** The FAQ's discussion of dynamic fallback raised the question of whether this
project's own limiter actually holds its ceiling. Tested three ways, on a controlled source, in PCM, with no
encoder in the path:

| drive into a −6.0 dBFS limiter | PCM out |
|---|---|
| 0 … 20 dB (input below the ceiling) | passes through, untouched |
| 26, 32, 40 dB (input far above it) | **−6.00 dBFS exactly** |

And through the engine's own generated chain at 0, +12, +24 and +36 of drive: **−6.00 dBFS at every one**.
The limiter is doing its job; earlier probe readings of `0.00` were an artifact of measuring a *decoded AAC*
file rather than the PCM, which is exactly how this project's own `PEAK_TOLERANCE_DB` note describes the
encoder behaving. **No defect.**

**4.2 The real answer to the operator's report is about the chain, not the normalizer.** The operator's Track
Fx contains `acompressor=ratio=1`, which is transparent by design (`DESIGN.md` §2.2), so the only dynamics the
`chain` strategy changes are the ones its limiter clamps. Measured on the same 180-second excerpt, all ending
in the same limiter and out fader so the figures are comparable:

| what runs before the limiter | peak | evened out by |
|---|---|---|
| today: the operator's chain, +12 | −6.0 | **4.6 dB** |
| `speechnorm=e=12` (ffmpeg's speech normalizer) | −6.0 | **11.7 dB** |
| `dynaudnorm=f=250:g=15` | −6.0 | **10.6 dB** |
| `acompressor` at 6:1, threshold −30 | −6.0 | **9.3 dB** |

Every one of those reaches the target exactly and every one evens the file out **two to three times as much**
as the operator's chain does. That is the change that answers the report.

## 5. Verdict

**Do not adopt ffmpeg-normalize as a dependency.** It is a good tool solving a neighbouring problem — loudness
targets by EBU R128 — and its two-pass shape is already this project's shape. It would not have fixed the
report that prompted the question, its recommended mode is a constant gain (which the project already offers),
and the mode that would fix it is one this project can add in a filtergraph without inheriting a CLI, a
temporary-file policy and a codec table that were each measured into place here.

**Take two things from it:**

1. **A leveler in the chain, offered as a choice.** This is answer 4.2 and it is the real fix. `speechnorm` is
   ffmpeg's own speech normalizer, designed for one pass, and it is the closest thing to what an interview
   needs.
2. **Report loudness next to peak.** `loudnorm`'s first pass can supply integrated LUFS and LRA for free during
   a decode this project already performs. It would let the report say *"this file is 20 LU wide"* — which is
   the number the operator actually needs to understand why the quiet parts are quiet — and it would make the
   product honest about being a peak normalizer rather than a loudness one.

Neither requires the dependency. Neither is a rewrite.
