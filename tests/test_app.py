"""Headless smoke tests of the Streamlit UI: every page renders without exceptions."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

TIMEOUT = 120
APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _run_page(name: str) -> None:
    import app

    app.PAGES[name]()


def test_main_script_shows_banner_and_overview():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert "SYNTHETIC DATA" in at.warning[0].value
    labels = [m.label for m in at.metric]
    assert "Estimated monthly savings (actionable)" in labels
    assert "Resources analysed" in labels


@pytest.mark.parametrize("page", ["overview", "cost", "recommendations", "evaluation", "settings"])
def test_every_page_renders(page):
    at = AppTest.from_function(_run_page, args=(page,), default_timeout=TIMEOUT).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title


def test_recommendation_drill_down_and_filters():
    at = AppTest.from_function(_run_page, args=("recommendations",), default_timeout=TIMEOUT).run()
    box = at.selectbox[0]
    assert len(box.options) > 1
    box.set_value(box.options[-1]).run()
    assert not at.exception
    at.multiselect[1].set_value(["NEEDS_HUMAN_REVIEW"]).run()
    assert not at.exception
    table = at.dataframe[0].value  # the filtered recommendations table
    assert set(table["outcome"]) == {"NEEDS_HUMAN_REVIEW"}
