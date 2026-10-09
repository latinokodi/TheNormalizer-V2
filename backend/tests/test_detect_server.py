"""The autodetect route and the suggestion in the plan, asserted before the route existed.

## Why the measurements travel in the request

Because the window already has them. Every file is measured when it is added — the peak by `volumedetect`, the
loudness and the range by `ebur128` — so a route that measured the file again would decode its whole sound a
second time to answer a question the caller could already answer. It also makes these tests need no media and
no ffmpeg: the route is arithmetic and the shape of a reply.

The `engine` fixture is `test_server.py`'s, imported rather than redefined: it is the real application with the
four process-shaped seams replaced, which is what lets a route be tested at all without a child process.
"""

from __future__ import annotations

import pytest

from normalizer import detect
from test_server import Engine, engine  # noqa: F401 — the fixture is the import


def call(running, path: str):
    """`running.get(path)`, named so a test line reads as one thing."""
    return running.get(path)


# ---------------------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------------------


def test_the_suggestion_is_built_from_the_measurements_in_the_request(engine):
    """The interview-shaped figures, which is the case the rule is calibrated on."""
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8",
    )
    assert status == 200
    suggestion = payload["suggestion"]
    assert suggestion["levelDbfs"] == pytest.approx(-6.0)
    assert suggestion["evenOut"] == pytest.approx(14.8)
    assert suggestion["makeupDb"] == pytest.approx(12.0)
    assert suggestion["spreadLu"] == pytest.approx(21.8)


def test_the_route_says_why_for_every_figure(engine):
    """A suggestion without its reasoning is a number to be taken on trust."""
    status, payload = call(
        engine, "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8"
    )
    assert status == 200
    reasons = payload["suggestion"]["reasons"]
    assert isinstance(reasons, list) and len(reasons) >= 3
    assert all(isinstance(reason, str) and reason.strip() for reason in reasons)


def test_a_file_that_is_already_even_is_offered_no_evening(engine):
    status, payload = call(
        engine, "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=2.0&spreadLu=2.0"
    )
    assert status == 200
    assert payload["suggestion"]["evenOut"] == 0.0
    assert payload["suggestion"]["levelsDynamics"] is False


def test_measurements_that_are_missing_are_answered_with_no_suggestion(engine):
    """`204` and not an error: nothing went wrong, there was simply nothing to decide from."""
    status, payload = call(engine, "/api/suggestions?peakDbfs=-1.1")
    assert status == 204
    assert payload is None


def test_a_peak_that_is_not_a_number_is_refused(engine):
    status, payload = call(engine, "/api/suggestions?peakDbfs=loud&integratedLufs=-23.4&rangeLu=12.8")
    assert status == 400
    assert "peakDbfs" in payload["error"]


def test_a_level_the_engine_cannot_run_is_refused_rather_than_clamped(engine):
    """Clamping a delivery requirement delivers the wrong thing quietly, which is worse than saying no."""
    status, payload = call(
        engine, "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&targetDbfs=3"
    )
    assert status == 400
    assert "range" in payload["error"]


def test_the_suggestion_is_offered_the_operators_own_make_up_and_level(engine):
    """The two figures that are the operator's are carried through, not detected."""
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8"
        "&targetDbfs=-3&makeupDb=6",
    )
    assert status == 200
    assert payload["suggestion"]["levelDbfs"] == pytest.approx(-3.0)
    assert payload["suggestion"]["makeupDb"] == pytest.approx(6.0)


def test_the_route_names_the_tool_so_a_warm_port_is_not_mistaken_for_it(engine):
    """Three applications in this family answer a health route; this one says which it is."""
    status, payload = call(engine, "/api/suggestions?peakDbfs=-1&integratedLufs=-20&rangeLu=12")
    assert status == 200
    assert payload["product"] == "TheNormalizer"


def test_the_detector_is_reachable_without_a_measured_file(engine):
    """The end-to-end shape: figures in, a suggestion out, no media anywhere."""
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-6.2&integratedLufs=-19.2&rangeLu=9.9&spreadLu=10.0",
    )
    assert status == 200
    suggestion = payload["suggestion"]
    assert suggestion["evenOut"] == pytest.approx(3.0)
    # And it is a suggestion this product can actually run.
    assert detect.LEVEL_RANGE[0] <= suggestion["levelDbfs"] <= detect.LEVEL_RANGE[1]
    assert 0.0 <= suggestion["evenOut"] <= detect.MAX_EVEN_OUT


# ---------------------------------------------------------------------------------------
# And the figures in a plan, so the window and the run cannot disagree about them
# ---------------------------------------------------------------------------------------


def test_what_the_detector_suggests_is_what_the_engine_will_run(engine, tmp_path):
    """The join that matters: a suggestion's figures, turned into a request, produce that run's plan.

    A suggestion is only worth offering if it can be *run*, and the levels it proposes are the same three the
    plan route takes. This drives the whole path — figures in, a suggestion out, the suggestion as a request,
    the plan that request produces — so a figure that the plan route would ignore or refuse fails here rather
    than in the operator's hands.
    """
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8",
    )
    assert status == 200
    suggestion = payload["suggestion"]

    source = tmp_path / "talk.wav"
    source.write_bytes(b"not really a wave, but it is a file")
    planned = engine.post(
        "/api/normalization-plans",
        json={
                "sources": [str(source)],
                "targetDbfs": suggestion["levelDbfs"],
                "ceilingDbfs": suggestion["levelDbfs"],
                "strategy": "chain",
                "makeupDb": suggestion["makeupDb"],
                "levelingDb": suggestion["evenOut"],
            "peakDbfs": -1.1,
            "meanDbfs": -26.6,
        },
    )
    assert planned[0] == 200, planned
    plan = planned[1]["plan"]
    assert plan["levelingDb"] == pytest.approx(suggestion["evenOut"])
    assert plan["targetDbfs"] == pytest.approx(suggestion["levelDbfs"])
    assert plan["makeupDb"] == pytest.approx(suggestion["makeupDb"])
    assert plan["levelsDynamics"] is True


def test_the_engine_reports_the_suggestion_route_in_its_index(engine):
    """A client discovers the surface from the index, so a route missing from it is a route nobody can use."""
    status, payload = call(engine, "/api")
    assert status == 200
    assert "suggestions" in payload["routes"]


# ---------------------------------------------------------------------------------------
# The true peak, which travels in and is reported out
# ---------------------------------------------------------------------------------------


def test_the_true_peak_is_read_from_the_request_and_reported(engine):
    """`truePeakDbfs` in, `truePeakDbfs` and a `headroomNote` out.

    The figure is `ebur128`'s own and the window already holds it from `/api/probe`, so it travels the same way
    the peak and the loudness do — and it comes back with the note it produced rather than being folded into a
    figure, because no setting here can change it.
    """
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&truePeakDbfs=0.9&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8",
    )
    assert status == 200
    suggestion = payload["suggestion"]
    assert suggestion["truePeakDbfs"] == pytest.approx(0.9)
    assert suggestion["headroomNote"] is not None
    assert "2.0" in suggestion["headroomNote"], suggestion["headroomNote"]


def test_a_true_peak_that_agrees_with_the_sample_peak_produces_no_note(engine):
    """Measured on ordinary material the two agree, and a note on every file is a note nobody reads."""
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&truePeakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8",
    )
    assert status == 200
    assert payload["suggestion"]["headroomNote"] is None


def test_a_true_peak_that_was_not_measured_is_absent_and_not_zero(engine):
    """A figure that was not taken is `null`, which is the honest form of it. Zero would be a level."""
    status, payload = call(
        engine, "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8"
    )
    assert status == 200
    assert payload["suggestion"]["truePeakDbfs"] is None
    assert payload["suggestion"]["headroomNote"] is None


def test_the_true_peak_does_not_change_the_figures_the_engine_will_run(engine):
    """It is a report: the same file with and without one is given the same settings."""
    without = call(
        engine, "/api/suggestions?peakDbfs=-1.1&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8"
    )[1]["suggestion"]
    with_peak = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&truePeakDbfs=2.0&integratedLufs=-23.4&rangeLu=12.8&spreadLu=21.8",
    )[1]["suggestion"]
    for figure in ("levelDbfs", "evenOut", "makeupDb"):
        assert without[figure] == with_peak[figure], figure


def test_a_true_peak_that_is_not_a_number_is_refused(engine):
    status, payload = call(
        engine,
        "/api/suggestions?peakDbfs=-1.1&truePeakDbfs=loud&integratedLufs=-23.4&rangeLu=12.8",
    )
    assert status == 400
    assert "truePeakDbfs" in payload["error"]
