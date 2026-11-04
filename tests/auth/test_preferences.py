"""Account settings: reading is lenient, writing is strict. No database needed."""

import pytest
from pydantic import ValidationError

from app.auth.preferences import (
    CalendarPrefs,
    PreferencesPatch,
    apply_patch,
    load_preferences,
)


def test_nothing_stored_is_nothing_saved():
    assert load_preferences({}).calendar is None
    assert load_preferences(None).calendar is None


def test_hidden_categories_are_unique_and_sorted():
    got = CalendarPrefs(hidden_categories=["career", "academic", "career"])
    assert got.hidden_categories == ["academic", "career"]
    assert got.favorites_only is False


@pytest.mark.parametrize(
    "bad",
    [
        {"hidden_categories": ["Academic"]},  # upper case
        {"hidden_categories": ["a b"]},
        {"hidden_categories": [""]},
        {"hidden_categories": ["x" * 33]},
        {"hidden_categories": [f"c{i}" for i in range(21)]},  # more than 20
        {"favorites_only": "maybe"},
    ],
)
def test_writing_is_strict(bad):
    with pytest.raises(ValidationError):
        PreferencesPatch(calendar=bad)


def test_reading_drops_an_unreadable_section_instead_of_failing():
    stored = {"calendar": {"hidden_categories": "not a list"}}
    assert load_preferences(stored).calendar is None
    assert load_preferences("garbage").calendar is None


def test_unknown_keys_are_ignored():
    got = load_preferences({"calendar": {"favorites_only": True, "future": 1}, "other": 2})
    assert got.calendar is not None and got.calendar.favorites_only is True


def test_patch_replaces_a_section_and_keeps_the_rest():
    stored = {"calendar": {"hidden_categories": ["career"], "favorites_only": True}, "x": 1}
    patch = PreferencesPatch(calendar={"hidden_categories": ["event"]})
    got = apply_patch(stored, patch)
    assert got["calendar"] == {"hidden_categories": ["event"], "favorites_only": False}
    assert got["x"] == 1  # a key the patch does not know is untouched
    assert stored["calendar"]["hidden_categories"] == ["career"]  # the input is not edited


def test_null_resets_and_an_absent_key_leaves_it():
    stored = {"calendar": {"hidden_categories": ["career"], "favorites_only": False}}
    assert "calendar" not in apply_patch(
        stored, PreferencesPatch.model_validate({"calendar": None})
    )
    assert apply_patch(stored, PreferencesPatch.model_validate({})) == stored
