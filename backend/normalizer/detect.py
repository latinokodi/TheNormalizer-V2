"""Choosing the settings for a file from what was measured about it.

## What this is

The window measures every file when it is added: its peak, its integrated loudness and its loudness range. This
module reads those figures and proposes the three settings for *that* file, so an operator with a quiet guest
and a loud host does not have to find the amount of evening by trial.

It is a **pure function over numbers.** Nothing here opens a file, spawns anything or knows about a request, so
every rule in `docs/AUTODETECT.md` is decidable in a unit test and the whole feature costs no measurement the
engine was not already taking.

## What it decides, and what it deliberately does not

| figure | decided by | why |
|---|---|---|
| **Level** | the operator | a delivery requirement is the same for a batch; two files at different levels are two files that do not match, which is the thing the operator is here to avoid |
| **Even out** | the file's own spread | this is the one that answers *how much evening does this file need* |
| **Make up** | the operator | it decides how hard the limiter is hit — a question about character — and the leveler has already decided how even the file is |

`docs/AUTODETECT.md` has the reasoning, the calibration, and the honest statement of what has **not** been
measured. In short: the one real-material measurement behind `DELIVERY_SPREAD_LU` is a 13-minute interview
whose spread went from 21.8 LU to 7.5 LU at an `Even out` of 12, and a tone cannot calibrate this filter at all
because `speechnorm` flattens gated tones completely at any setting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The spread a delivery target allows, in LU.
#:
#: **Seven**, because that is `loudnorm`'s own default loudness-range target and therefore a figure from the
#: standard rather than one chosen here. It is also the threshold `docs/TRUTH.md` §6b uses to call a file
#: *wide*, so the report and the detector agree about which files need evening.
DELIVERY_SPREAD_LU = 7.0

#: The gap between a file's true peak and its sample peak at which it is worth saying something, in dB.
#:
#: **One decibel**, and it is a reporting threshold rather than a control. A normalizer's promise is a peak at
#: the target, and an encoder's decoder rings past the samples it was given — so a file whose true peak sits a
#: decibel or more above its sample peak will lose that much of it back the moment it is encoded. Below a
#: decibel the two agree as closely as the measurement's own resolution, and a note on every file is a note
#: nobody reads.
#:
#: Measured on the calibration material: a source at −18.1 dBFS sample peak had a true peak of **−18.1 dBFS**,
#: no gap at all. On a file that has already been through a lossy codec the gap is ordinary.
TRUE_PEAK_NOTE_DB = 1.0

#: The most evening the detector will ask for, against `speechnorm`'s own ceiling of 30.
#:
#: At the one measured point, 12 was enough for a 21.8 LU file — it came out 7.5 LU wide, inside the target.
#: Eighteen is that figure with room for material worse than anything measured, and it stops well short of the
#: regime where the filter flattens tone-like material into a line.
MAX_EVEN_OUT = 18.0

#: The ranges a suggestion may propose in, taken from the engine so a suggestion cannot be unrunnable.
#:
#: Imported lazily inside `suggest` rather than at the top of the module: this file is a pure function of
#: numbers and importing `normalize` — which imports the module that spawns processes — would make it
#: impossible to unit-test without dragging the engine in behind it.
LEVEL_RANGE = (-24.0, 0.0)
MAKEUP_RANGE = (-12.0, 36.0)


@dataclass(frozen=True)
class Measurements:
    """What the engine knows about a file before anything is done to it.

    Every field is `None`-able because `ebur128` and `volumedetect` both report nothing for material they
    cannot measure, and `None` is the honest form of that: a file whose loudness could not be read is not a
    file of loudness zero. See `docs/TRUTH.md`.
    """

    #: The largest sample, in dBFS. What the target is applied to.
    peak_dbfs: float | None
    #: Integrated loudness in LUFS: how loud the file sounds.
    integrated_lufs: float | None
    #: `ebur128`'s loudness range, kept for the report and **not** used to decide anything — see `spread_lu`.
    range_lu: float | None
    #: The **true peak** in dBFS — the largest level between the samples, from `ebur128=peak=true`.
    #:
    #: Read because this is a figure `volumedetect` cannot produce and a lossy encode acts on, and it is
    #: **not** used to choose a figure: it produces a note when it sits a decibel or more above the sample
    #: peak, because that is headroom the file will lose. `None` when it was not measured, which is the honest
    #: form of a figure that is absent.
    true_peak_dbfs: float | None = None
    #: The loudest momentary figure, in LUFS.
    loudest_lufs: float | None = None
    #: `volumedetect`'s mean, in dBFS.
    mean_dbfs: float | None = None


@dataclass(frozen=True)
class Suggestion:
    """Three figures and the reasons for them.

    The reasons are not decoration. A recommendation without its reasoning is a number the operator has to
    take on trust, and this program's position on trust is in `docs/TRUTH.md`: the report says what was measured
    and what was decided, in the words of the person reading it.
    """

    level_dbfs: float
    even_out: float
    makeup_dbfs: float
    #: The spread this was decided from, in LU — the file's own, or the one the caller supplied.
    spread_lu: float
    #: One line per figure, in the operator's words, naming the measurement behind it.
    reasons: tuple[str, ...] = field(default_factory=tuple)
    #: The true peak this was read from, or `None` when it was not measured.
    true_peak_dbfs: float | None = None
    #: A sentence about headroom the file will lose, or `None` when there is nothing to say.
    #:
    #: Separate from `reasons` because it is not a reason for a figure: it is a fact about the file that no
    #: setting here changes.
    headroom_note: str | None = None

    @property
    def levels_dynamics(self) -> bool:
        """Whether this suggestion asks for any evening at all. False is the operator's chain untouched."""
        return self.even_out > 0.0


def spread_lu(quiet_lufs: float | None, loud_lufs: float | None) -> float | None:
    """How far apart a file's quietest fifth of material is from its loudest, in LU.

    ## Why this and not `ebur128`'s `LRA`

    Because `LRA` does not answer the question. Measured on the same files, twice:

    | file | `LRA` | this figure |
    |---|---|---|
    | a 13-minute interview | 12.8 LU | **21.8 LU** |
    | calibration sources whose plateaus are exactly 6, 12, 18, 24 LU apart | 1.8, 2.6, 2.9, 3.0 LU | **6.0, 12.0, 18.0, 24.0 LU** |

    `LRA` gates in 400 ms blocks and takes a range over its own percentiles. The figure an operator means by
    *"the quiet parts are quiet"* is the distance between the quiet material and the loud material, and the
    right-hand column is that distance. The measurement is taken with the same code the report and the
    calibration use, so what the detector decides on is what the operator can see.
    """
    if quiet_lufs is None or loud_lufs is None:
        return None
    return round(abs(loud_lufs - quiet_lufs), 4)


def suggest(
    measured: Measurements,
    *,
    target_dbfs: float | None = None,
    makeup_dbfs: float | None = None,
    spread: float | None = None,
) -> Suggestion | None:
    """The settings for a file, or `None` when there is nothing to decide them from.

    ## The rule

    `even_out = spread − DELIVERY_SPREAD_LU`, clamped to `[0, MAX_EVEN_OUT]`. A file inside the delivery spread
    gets **zero** and keeps all of its dynamics; a file wider than that gets exactly the amount by which it
    exceeds it. `docs/AUTODETECT.md` §2.2 states the rule and §4 gives the measurement behind it.

    ## Why `None` is an answer

    A suggestion made from nothing is indistinguishable from one made from something and is acted on the same
    way, so a file with no loudness — or no level at all, which is what digital silence measures as — gets no
    suggestion and the reason says which measurement is missing. A default dressed as an answer is worse than
    no answer.

    ## Why the target is refused rather than clamped

    The level is a delivery requirement. Clamping one would deliver the wrong thing quietly, which is worse
    than saying no, so a target outside what the engine can run raises `ValueError`.
    """
    # The engine's own defaults, imported here rather than at the top of the module: `normalize` imports the
    # module that spawns processes, and a pure function that cannot be imported without the engine behind it
    # is a pure function that cannot be unit-tested.
    from .normalize import DEFAULT_MAKEUP_DB, DEFAULT_TARGET_DBFS, TARGET_RANGE

    level = DEFAULT_TARGET_DBFS if target_dbfs is None else target_dbfs
    makeup = DEFAULT_MAKEUP_DB if makeup_dbfs is None else makeup_dbfs

    low, high = LEVEL_RANGE
    if not low <= level <= high:
        raise ValueError(
            f"a level of {level} dBFS is outside the range this product works in "
            f"({TARGET_RANGE[0]:g} to {TARGET_RANGE[1]:g} dBFS)"
        )

    # Nothing to decide from. Each is named, because "no suggestion" and "the file could not be measured" are
    # different things to be told and only one of them is actionable.
    if measured.peak_dbfs is None:
        return None
    if measured.integrated_lufs is None:
        return None

    # The spread, in the order of how good an answer each source is.
    #
    # 1. **What the caller measured**, which is the fifth-to-fifth figure and the one the rule is calibrated
    #    against. The window takes it with the same code as the report and the calibration, so the three agree.
    # 2. **`LRA`**, when the caller has nothing better. It is a worse figure — measured, on the same files, as
    #    12.8 against 21.8 and as 1.8–3.0 against 6–24 — and it is still a measurement of the same thing rather
    #    than a default. Using it means a suggestion slightly *under*-evens a file, which is the safe direction:
    #    too little evening leaves dynamics in, and too much takes them out.
    #
    # The floor is not applied and does not need to be: every figure here is a distance between a loud part and
    # a quiet one, and a distance cannot be negative.
    decided_spread = spread if spread is not None else measured.range_lu
    if decided_spread is None:
        return None

    asked = round(max(0.0, decided_spread - DELIVERY_SPREAD_LU), 4)
    even_out = min(asked, MAX_EVEN_OUT)

    reasons = [
        f"the level is what you are delivering at, {level:.1f} dBFS — it is a delivery requirement and not "
        f"something read from this file",
        f"this file's quietest fifth of material sits {decided_spread:.1f} LU below its loudest",
    ]
    if asked == 0.0:
        reasons.append(
            f"it is already even — inside the {DELIVERY_SPREAD_LU:.0f} LU a delivery target allows — so the "
            f"leveler is switched off and its dynamics are left alone"
        )
    elif even_out < asked:
        reasons.append(
            f"that wants {asked:.1f} of evening and the most this will ask for is {MAX_EVEN_OUT:.0f}: the file "
            f"is wider than the tool can close, so it will come out wider than the target"
        )
    else:
        reasons.append(
            f"so the leveler is set to {even_out:.1f} to close the difference, which is what brings the quiet "
            f"passages up toward the loud ones"
        )
    reasons.append(
        f"make up stays at {makeup:+.1f} dB — it decides how hard the limiter is hit and how squashed the "
        f"peaks come out, not how even the file is"
    )

    # The true peak is a **report and not a control**: nothing above this line reads it, and the three figures
    # are the same whether it was measured or not. What it produces is a sentence about headroom the file will
    # lose, which no setting here can prevent — an encoder's decoder rings after every gain in the chain.
    headroom_note = None
    if measured.true_peak_dbfs is not None and measured.peak_dbfs is not None:
        gap = measured.true_peak_dbfs - measured.peak_dbfs
        if gap >= TRUE_PEAK_NOTE_DB:
            headroom_note = (
                f"this file's true peak is {measured.true_peak_dbfs:.1f} dBFS, which is {gap:.1f} dB above its "
                f"loudest sample — an encoder's decoder rings past the samples it is given, so expect it to "
                f"land around {level + gap:.1f} dBFS rather than {level:.1f} after this run"
            )

    return Suggestion(
        level_dbfs=level,
        even_out=even_out,
        makeup_dbfs=makeup,
        spread_lu=decided_spread,
        reasons=tuple(reasons),
        true_peak_dbfs=measured.true_peak_dbfs,
        headroom_note=headroom_note,
    )
