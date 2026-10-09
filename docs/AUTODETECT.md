# Autodetect: choosing the settings from the file

**Status:** implemented. `backend/normalizer/detect.py`, `GET /api/suggestions`, and the **Suggest** button in
the settings panel.

The objective of this product is that the voices come out even — see `docs/TRUTH.md` §6b2. Autodetect is the
feature that decides *how much evening* a particular file needs, instead of making the operator guess a number.

---

## 1. What it reads

Nothing new is measured. The suggestion is a pure function of figures the engine already takes for every file
it is asked about, which is what makes it testable without ffmpeg and cheap to offer:

| figure | where it comes from | what it says |
|---|---|---|
| `sample_peak_dbfs` | `volumedetect` | the largest sample, which is what the target is applied to |
| `integrated_lufs` | `ebur128` | how loud the file sounds overall |
| `range_lu` | `ebur128` | `LRA`: the metered spread between the loud and quiet parts |
| `loudest_lufs` | `ebur128`, momentary | the loudest moment, for a sanity note |
| **`true_peak_dbfs`** | **`ebur128=peak=true`** | the largest inter-sample peak, which is what a file loses headroom to after a lossy encode |
| **`spread_lu`** | **derived: quietest fifth against loudest fifth** | the figure the detector actually decides on |

**`spread_lu` is derived rather than taken from `LRA`, and that is a measurement and not a preference.** On a
13-minute interview, `LRA` reported 12.8 LU while the fifth-to-fifth spread of the same file was 21.8 LU. On
the calibration sources of §4 — four files whose plateaus are *exactly* 6, 12, 18 and 24 LU apart — `LRA`
reported 1.8, 2.6, 2.9 and 3.0 LU while the fifth-to-fifth spread reported 6.0, 12.0, 18.0 and 24.0. `LRA`
gates in 400 ms blocks and takes a range over its own percentiles; the spread over fifths answers the question
the operator is asking, which is *how far apart are the quiet parts and the loud ones*.

A derived figure is still a measurement, and it is taken with the same code the calibration and the reports
use, so the number the detector decides on is the number the operator can see.

## 2. What it decides

Three figures, and each one is derived from a named input by a rule that is stated rather than tuned:

### 2.1 `Level` — the target, in dBFS

**It is not detected.** It is the delivery requirement, the same for every file in a batch, and a program that
changed it per file would defeat its own purpose: two files at different levels are two files that do not
match, which is the thing the operator is here to avoid. The suggestion carries the operator's current level
back unchanged and says so.

### 2.2 `Even out` — from the spread

```
even_out = clamp(round(spread_lu - DELIVERY_SPREAD_LU), 0, MAX_EVEN_OUT)
```

with `DELIVERY_SPREAD_LU = 7.0` and `MAX_EVEN_OUT = 18.0`.

* **`DELIVERY_SPREAD_LU = 7.0`** is `loudnorm`'s own default loudness-range target, so it is a figure from the
  standard rather than one invented here, and it is the same threshold `docs/TRUTH.md` §6b uses to call a file
  *wide*.
* **The subtraction** says: shut the leveler off for a file that is already inside the target, and set it to
  the amount by which the file exceeds it otherwise. A file 7 LU wide needs nothing; one 22 LU wide gets 15.
* **`MAX_EVEN_OUT = 18.0`** against `speechnorm`'s own ceiling of 30. At the measured point, 12 was enough for
  a 21.8 LU file; 18 is that figure plus room for material worse than anything measured, and it stops short of
  the regime where the filter is squashing tone-like material into a flat line (§4).

### 2.3 The true peak — read, and reported rather than decided on

`ebur128` is asked for `peak=true`, which makes it oversample and estimate the **true peak**: the largest level
the waveform reaches *between* its samples. `volumedetect` cannot see that at all, and it matters because an
encoder's decoder rings past the samples it was given.

It is measured because the objective names it, and it is **not** used to choose a figure. What it does is
produce a **note** when it sits a decibel or more above the sample peak, because that is a file which will lose
that much headroom the moment it is encoded — and a normalizer's whole promise is a peak at the target.

Measured on the calibration material: a source at −18.1 dBFS sample peak had a true peak of **−18.1 dBFS**, no
gap at all, so on ordinary material the two agree. On material that has already been through a lossy codec the
gap is ordinary and is worth saying out loud.

### 2.4 `Make up` — left where the operator has it

`DEFAULT_MAKEUP_DB`, unchanged. Make up decides how hard the limiter is hit and therefore how squashed the
peaks come out — a question about character — and the leveler has already decided how even the file is. The
suggestion reports the figure so the plan and the panel agree, and it does not pretend to have detected it.

## 3. What it says, and why it says it

Every suggestion carries **reasons**: one line per figure, in the operator's words, naming the measurement it
came from. A recommendation without its reasoning is a number the operator has to take on trust, and this
program's position on trust is in `docs/TRUTH.md`.

A file whose figures could not be read — no loudness, no level — produces **no suggestion at all** rather than
a default one, because a suggestion made from nothing is indistinguishable from a suggestion made from
something and is acted on the same way.

## 4. Calibration: what was measured, and what could not be

`scripts/make_calibration_sources.py` builds sources whose spread is exact and verified. `scripts/calibrate_leveling.py`
runs the engine over them at a range of `Even out` values and reports the closure.

**The result is that tones cannot calibrate this filter, and that is worth recording.** On sources whose
plateaus are exactly 6, 12, 18 and 24 LU apart, `speechnorm` closed **99–100 % of the spread at every setting
including 4** — a 24 LU source came out 0.1 LU wide. A window normalizer flattens gated tones almost perfectly,
which is why the curve is flat, and a flat curve cannot say where a setting stops being enough. Six
filter-syntax failures were spent getting those sources built (`scripts/make_calibration_sources.py` lists all
five of the *other* causes), and the honest conclusion is that the fixture was the wrong instrument.

**The figure the detector uses comes from real speech, measured once:**

| on a 13-minute interview | source | normalized | |
|---|---|---|---|
| the quietest fifth | −41.0 LUFS | −22.7 LUFS | lifted **+18.4 dB** |
| the loudest fifth | −19.3 LUFS | −15.2 LUFS | lifted +4.1 dB |
| **the spread** | **21.8 LU** | **7.5 LU** | closed by **14.2 dB** |

which was measured at `Even out 12`. Twelve was enough to bring a 21.8 LU file to 7.5 — inside the target — so
the rule in §2.2 sets 15 where that file would have needed 12, and the two agree to within the tolerance the
rest of this program works at.

**What is not known, and is recorded rather than papered over:** the relationship between the figure and the
closure for spreads other than 21.8, on material other than speech, has not been measured. The rule is
monotonic in the right direction and calibrated at the one point that has a measurement behind it. When the
right material is available, `scripts/calibrate_leveling.py` is the instrument and §2.2's two constants are
the only things that would change.

## 5. What is refused

| the input | the answer |
|---|---|
| no loudness measured | no suggestion, and the reason is the missing measurement |
| no level measured (digital silence) | no suggestion: a file with no sound has no spread to even |
| a spread inside the target | an `Even out` of **0**, with the reason saying the file is already even |
| `Even out` above the maximum | capped, with the cap in the reason rather than silent |
| a true peak a decibel or more above the sample peak | the figures are unchanged, and a **note** says the file will lose that much headroom when it is encoded |
| a request for a level outside the engine's range | refused by the same rules the plan uses, not a second set |

## 6. How it is verified

Following the three disciplines this project uses:

* **SDD** — this document. The rules in §2 are the requirements; the tests below assert them.
* **TDD** — `backend/tests/test_detect.py`: the detector is a pure function over five numbers, so every rule
  has a unit test that was written to fail first. No ffmpeg, no files.
* **BDD** — `specs/features/autodetect.feature`, executed by `backend/tests/test_detect_bdd.py` through
  pytest-bdd, in the operator's language rather than the implementation's.
* **Real media** — `scripts/check_normalize.py` and `scripts/voice_evenness.py` for the end-to-end behaviour,
  and `scripts/calibrate_leveling.py` for the relationship in §4.
