"""The steps behind `specs/features/autodetect.feature`, in the operator's language.

pytest-bdd resolves a feature by name against the step file's own directory, so the canonical copy of the
feature lives in `specs/features/` — where every other feature in this project lives and where the documents
point — and this package runs it from its own directory through `scenarios(...)`.

Every step is a sentence an operator could have written, and the vocabulary is the window's: **level**,
**even out**, **make up**. The implementation's names — `target_dbfs`, `leveling_db`, `makeup_db` — do not
appear, because a feature file that speaks in the code's words is a feature file only the code can read.
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenario, then, when

from normalizer import detect

FEATURES = "autodetect.feature"


@pytest.fixture
def world() -> dict:
    """The one file this scenario is about, and the answers it produced."""
    return {"measured": {}, "spread": None, "suggestion": None, "again": None}


# ---------------------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------------------


@given("a file measured at a peak of -1.1 dBFS", target_fixture="world")
def a_measured_file() -> dict:
    world = {"measured": {}, "spread": None, "suggestion": None, "again": None}
    world["measured"]["peak_dbfs"] = -1.1
    world["measured"]["mean_dbfs"] = -26.6
    return world


@given("its loudness measured at -23.4 LUFS with a loudness range of 12.8 LU")
def its_loudness(world):
    world["measured"]["integrated_lufs"] = -23.4
    world["measured"]["range_lu"] = 12.8
    world["measured"]["loudest_lufs"] = -11.4


@given("its quietest fifth of material is 21.8 LU below its loudest fifth")
def wide(world):
    world["spread"] = 21.8


@given("its quietest fifth of material is 2.0 LU below its loudest fifth")
def already_even(world):
    world["spread"] = 2.0


@given("its quietest fifth of material is 60.0 LU below its loudest fifth")
def very_wide(world):
    world["spread"] = 60.0


@given("the operator is delivering at -6.0 dBFS")
def delivering_at(world):
    world["target"] = -6.0


@given("the file's loudness could not be measured")
def no_loudness(world):
    world["measured"]["integrated_lufs"] = None


@given("the file is digital silence")
def silence(world):
    world["measured"]["peak_dbfs"] = None
    world["measured"]["integrated_lufs"] = None


# ---------------------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------------------


@when("the settings for that file are suggested")
@when("the settings are suggested")
def suggest(world):
    world["suggestion"] = detect.suggest(
        detect.Measurements(**world["measured"]),
        target_dbfs=world.get("target"),
        spread=world["spread"],
    )


@when("the settings are suggested twice")
def suggest_twice(world):
    measured = detect.Measurements(**world["measured"])
    world["suggestion"] = detect.suggest(measured, spread=world["spread"])
    world["again"] = detect.suggest(measured, spread=world["spread"])


# ---------------------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------------------


@then("the suggestion is built from what was measured rather than from a default")
def from_measurements(world):
    suggestion = world["suggestion"]
    assert suggestion is not None, "no suggestion was made from measurements that can support one"
    assert suggestion.level_dbfs == -6.0, "the level is the operator's, and it was carried through"
    assert suggestion.even_out > 0.0, "a 12.8 LU file has something to even out"


@then("every figure in it carries the reason it was chosen")
def every_figure_has_a_reason(world):
    reasons = world["suggestion"].reasons
    assert len(reasons) >= 3, reasons
    assert all(reason.strip() for reason in reasons)


@then("the leveler is set to the amount by which the file exceeds a delivery spread")
def leveler_from_spread(world):
    suggestion = world["suggestion"]
    assert suggestion.even_out == pytest.approx(21.8 - detect.DELIVERY_SPREAD_LU)


@then("the reason names the file's own spread")
def reason_names_spread(world):
    assert any("21.8" in reason for reason in world["suggestion"].reasons), world["suggestion"].reasons


@then("the leveler is switched off")
def leveler_off(world):
    assert world["suggestion"].even_out == 0.0
    assert world["suggestion"].levels_dynamics is False


@then("the reason says the file is already even")
def reason_says_even(world):
    assert any("already" in reason for reason in world["suggestion"].reasons), world["suggestion"].reasons


@then("the target is -6.0 dBFS")
def target_is(world):
    assert world["suggestion"].level_dbfs == -6.0


@then("the reason says the level is a delivery requirement rather than something read from the file")
def reason_is_delivery(world):
    assert any("delivery" in reason for reason in world["suggestion"].reasons), world["suggestion"].reasons


@then("the make-up figure is the operator's own")
def makeup_is_operators(world):
    # The engine's own default, which is the operator's Premiere figure. Not detected, and reported.
    assert world["suggestion"].makeup_dbfs == 12.0


@then("the reason says it decides how hard the limiter is hit rather than how even the file is")
def reason_for_makeup(world):
    assert any("limiter is hit" in reason for reason in world["suggestion"].reasons), world["suggestion"].reasons


@then("no suggestion is offered")
def no_suggestion(world):
    assert world["suggestion"] is None, f"a suggestion was invented: {world['suggestion']}"


@then("the reason is the measurement that is missing")
@then("the reason says there is no sound to measure")
def reason_is_missing(world):
    # The reason *is* the absence: `suggest` answers `None` rather than a sentence, and the route turns that
    # into `204` — there is nothing to explain because nothing was claimed. Asserted as an absence so a
    # future version that invented a default would fail here.
    assert world["suggestion"] is None


@then("the leveler is set to its maximum")
def leveler_capped(world):
    assert world["suggestion"].even_out == detect.MAX_EVEN_OUT


@then("the reason says the file is wider than the tool can close")
def reason_is_cap(world):
    reasons = " ".join(world["suggestion"].reasons)
    assert "wider" in reasons or "most" in reasons, reasons


@then("the same file gets the same answer, because a suggestion that moves on its own is one nobody can check")
def same_answers(world):
    assert world["suggestion"] == world["again"]


# ---------------------------------------------------------------------------------------
# The scenarios, named one by one so a failure names the behaviour that broke
# ---------------------------------------------------------------------------------------


@scenario(FEATURES, "the file's own measurements decide the figures")
def test_measurements_decide():
    """The suggestion comes from the file, not from a default."""


@scenario(FEATURES, "a file whose quiet and loud parts are far apart is evened out")
def test_wide_file_is_evened():
    """How much evening is the amount by which the file exceeds a delivery spread."""


@scenario(FEATURES, "a file that is already even is left alone")
def test_even_file_untouched():
    """The leveler costs dynamics, and a file with none to spare keeps what it has."""


@scenario(FEATURES, "the level is the operator's decision and not the file's")
def test_level_is_the_operators():
    """A delivery requirement is the same for a batch."""


@scenario(FEATURES, "the make-up gain is not detected")
def test_makeup_not_detected():
    """Character, not evenness, and it stays where the operator put it."""


@scenario(FEATURES, "a suggestion that cannot be made is not invented")
def test_no_invention():
    """A default dressed as an answer is acted on as an answer."""


@scenario(FEATURES, "a file with no sound has nothing to even out")
def test_silence_has_nothing():
    """A leveler cannot make silence even."""


@scenario(FEATURES, "asking for more evening than the tool allows is capped rather than silent")
def test_cap_is_stated():
    """A cap applied silently is a promise the tool cannot keep."""


@scenario(FEATURES, "the same file always gets the same answer")
def test_deterministic():
    """A suggestion that moved on its own could not be checked by anybody."""
