"""What the autodetector has to be true of, asserted before it was written.

## Why these are unit tests and not an integration test

The detector is a **pure function over five numbers**. Nothing about it needs ffmpeg, a file or a server, and
every rule in `docs/AUTODETECT.md` §2 is a statement about those numbers. So each rule gets a test that names
the rule, and the failure of one says which rule broke rather than which file was wrong.

The measurements arrive from the engine — `process.Loudness` and `process.Levels` — and the detector takes
them as a small value of its own so that a caller cannot hand it a half-filled engine object and get an answer
built from whatever happened to be `None`.
"""

from __future__ import annotations

import pytest

from normalizer import detect


def measured(**overrides) -> detect.Measurements:
    """A file like the 13-minute interview the calibration was taken on, unless a test says otherwise."""
    fields = {
        "peak_dbfs": -1.1,
        "true_peak_dbfs": -1.1,
        "integrated_lufs": -23.4,
        "range_lu": 12.8,
        "loudest_lufs": -11.4,
        "mean_dbfs": -26.6,
    }
    return detect.Measurements(**{**fields, **overrides})


# ---------------------------------------------------------------------------------------
# The spread, which is the figure the detector decides on
# ---------------------------------------------------------------------------------------


def test_the_spread_is_the_distance_between_the_quiet_and_the_loud_parts():
    """`LRA` is not this figure, and the difference was measured rather than preferred.

    On the interview: `LRA` said 12.8 LU and the fifth-to-fifth spread of the same file was 21.8 LU. On four
    calibration sources whose plateaus are exactly 6, 12, 18 and 24 LU apart, `LRA` said 1.8, 2.6, 2.9 and 3.0
    and the spread said 6.0, 12.0, 18.0 and 24.0. See `docs/AUTODETECT.md` §1.
    """
    assert detect.spread_lu(-41.0, -19.3) == pytest.approx(21.7)
    assert detect.spread_lu(-19.3, -41.0) == pytest.approx(21.7), "the order must not matter"


def test_a_spread_cannot_be_read_from_a_file_that_has_no_loudness():
    assert detect.spread_lu(None, -19.3) is None
    assert detect.spread_lu(-41.0, None) is None
    assert detect.spread_lu(None, None) is None


# ---------------------------------------------------------------------------------------
# The suggestion
# ---------------------------------------------------------------------------------------


def test_the_level_is_the_operator_s_and_not_the_file_s():
    """Two files at different levels are two files that do not match, which is what the operator is avoiding.

    A delivery requirement is the same for a batch, so it is carried through unchanged and the reason says so.
    """
    suggestion = detect.suggest(measured(), target_dbfs=-6.0)
    assert suggestion is not None
    assert suggestion.level_dbfs == -6.0
    assert any("delivery" in reason for reason in suggestion.reasons)


def test_a_wide_file_is_evened_out_by_the_amount_it_exceeds_a_delivery_spread():
    """The rule: `even_out = spread - 7`, clamped. Seven is `loudnorm`'s own default loudness-range target."""
    suggestion = detect.suggest(measured(), spread=21.8)
    assert suggestion is not None
    assert suggestion.even_out == pytest.approx(14.8)
    assert any("21.8" in reason for reason in suggestion.reasons)


def test_a_file_that_is_already_even_is_left_alone():
    """The leveler costs dynamics. A file inside the delivery spread has none to spare, so it is switched off."""
    suggestion = detect.suggest(measured(), spread=2.0)
    assert suggestion is not None
    assert suggestion.even_out == 0.0
    assert any("already" in reason for reason in suggestion.reasons)


def test_a_file_exactly_at_the_delivery_spread_needs_no_leveling():
    """The boundary, asserted because an off-by-one here is a file flattened for nothing."""
    suggestion = detect.suggest(measured(), spread=detect.DELIVERY_SPREAD_LU)
    assert suggestion is not None
    assert suggestion.even_out == 0.0


def test_more_evening_than_the_tool_allows_is_capped_and_says_so():
    """The cap is a real limit of the filter, and a cap applied silently is a promise the tool cannot keep."""
    suggestion = detect.suggest(measured(), spread=60.0)
    assert suggestion is not None
    assert suggestion.even_out == detect.MAX_EVEN_OUT
    assert any("wider" in reason or "most" in reason for reason in suggestion.reasons)


def test_the_make_up_figure_is_not_detected():
    """It decides how hard the limiter is hit — a question of character — and the leveler has decided evenness.

    The suggestion reports the operator's own figure so the plan and the panel agree, and it says that it is
    the operator's rather than claiming to have read it from the file.
    """
    suggestion = detect.suggest(measured(), makeup_dbfs=9.0)
    assert suggestion is not None
    assert suggestion.makeup_dbfs == 9.0
    assert any("make up" in reason.lower() for reason in suggestion.reasons)


def test_every_figure_carries_the_reason_it_was_chosen():
    """A recommendation without its reasoning is a number to be taken on trust, and this program's position
    on trust is that it is not asked for. See `docs/TRUTH.md`."""
    suggestion = detect.suggest(measured())
    assert suggestion is not None
    assert len(suggestion.reasons) >= 3, suggestion.reasons
    assert all(reason.strip() for reason in suggestion.reasons)


def test_a_suggestion_that_cannot_be_made_is_not_invented():
    """A default dressed as an answer is indistinguishable from an answer and is acted on the same way.

    Two ways there is nothing to decide from, and each names the measurement that is missing: no loudness at
    all, and no level at all — which is what digital silence measures as.
    """
    assert detect.suggest(measured(integrated_lufs=None)) is None
    assert detect.suggest(measured(peak_dbfs=None)) is None
    assert detect.suggest(measured(range_lu=None), spread=21.8) is not None, (
        "a spread supplied by the caller is enough: `LRA` is not what the rule reads"
    )


def test_a_file_with_no_sound_has_nothing_to_even_out():
    """Negative infinity is not a very quiet file, and a leveler cannot make silence even."""
    assert detect.suggest(measured(peak_dbfs=None, integrated_lufs=None)) is None


def test_the_same_file_always_gets_the_same_answer():
    """Determinism, asserted because a suggestion that moved on its own could not be checked by anybody."""
    first = detect.suggest(measured(), spread=21.8)
    second = detect.suggest(measured(), spread=21.8)
    assert first == second


def test_the_suggestion_is_usable_as_a_specification_of_the_run():
    """What comes out has to be a legal request: the engine refuses a level outside its own range, and a
    suggestion that could not be run would be worse than none."""
    suggestion = detect.suggest(measured())
    assert suggestion is not None
    assert detect.LEVEL_RANGE[0] <= suggestion.level_dbfs <= detect.LEVEL_RANGE[1]
    assert 0.0 <= suggestion.even_out <= detect.MAX_EVEN_OUT
    assert detect.MAKEUP_RANGE[0] <= suggestion.makeup_dbfs <= detect.MAKEUP_RANGE[1]


def test_a_level_outside_what_the_engine_can_run_is_refused_rather_than_clamped():
    """Clamping a delivery requirement would deliver the wrong thing quietly, which is worse than saying no."""
    with pytest.raises(ValueError):
        detect.suggest(measured(), target_dbfs=3.0)


# ---------------------------------------------------------------------------------------
# The true peak, which is read and reported but does not choose a figure
# ---------------------------------------------------------------------------------------


def test_a_true_peak_above_the_sample_peak_is_said_out_loud():
    """An encoder's decoder rings past the samples it was given, and that is lost headroom.

    The figures are unchanged — the true peak is not a control — and the file is *told about* rather than
    silently adjusted. Measured on ordinary material the two agree to 0.03 dB; on a file that has already been
    through a lossy codec the gap is ordinary.
    """
    # The sample peak defaults to −1.1, so a true peak of +0.9 is a gap of exactly 2.0 dB.
    suggestion = detect.suggest(measured(true_peak_dbfs=0.9))
    assert suggestion is not None
    assert suggestion.true_peak_dbfs == pytest.approx(0.9)
    assert suggestion.headroom_note is not None
    assert "2.0" in suggestion.headroom_note, suggestion.headroom_note


def test_a_true_peak_that_agrees_with_the_sample_peak_says_nothing():
    """A note on every file is a note nobody reads. Measured: a source at −18.1 dBFS sample peak had a true
    peak of −18.1, so on this material there is nothing to say."""
    suggestion = detect.suggest(measured(true_peak_dbfs=-1.1))
    assert suggestion is not None
    assert suggestion.headroom_note is None


def test_a_file_with_no_true_peak_measured_is_not_invented_one():
    """`None` is the honest form of "not measured", and a note cannot be written about a figure that is absent."""
    suggestion = detect.suggest(measured(true_peak_dbfs=None))
    assert suggestion is not None
    assert suggestion.true_peak_dbfs is None
    assert suggestion.headroom_note is None


def test_the_true_peak_does_not_change_any_of_the_three_figures():
    """It is a report, not a control: the same file with and without one gets the same settings."""
    without = detect.suggest(measured())
    with_peak = detect.suggest(measured(true_peak_dbfs=2.0))
    assert without is not None and with_peak is not None
    assert without.level_dbfs == with_peak.level_dbfs
    assert without.even_out == with_peak.even_out
    assert without.makeup_dbfs == with_peak.makeup_dbfs
