"""
Tests for vector_store helper functions — no ChromaDB needed.
The embedding/search functions are tested via integration, but we can
unit-test the text generation and metadata extraction in isolation.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import vector_store


BASE_ACTIVITY = {
    "id": "abc123",
    "activity_type": "running",
    "start_time": "2024-06-15 07:30:00",
    "distance_meters": 10000.0,
    "duration_seconds": 3600.0,
    "avg_pace_per_km": 6.0,
    "avg_heart_rate": 145,
    "max_heart_rate": 172,
    "elevation_gain": 85.0,
    "aerobic_training_effect": 3.2,
    "calories": 680,
}


# ---- _activity_to_text ----

def test_activity_text_contains_distance():
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "10.0km" in text


def test_activity_text_contains_pace():
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "6:00/km" in text


def test_activity_text_contains_heart_rate():
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "145" in text


def test_activity_text_contains_date():
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "2024" in text
    assert "June" in text


def test_activity_text_contains_effort_label():
    # HR 145 → "comfortable"
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "comfortable" in text


def test_activity_text_hard_effort():
    hard = {**BASE_ACTIVITY, "avg_heart_rate": 165}
    text = vector_store._activity_to_text(hard)
    assert "hard" in text or "threshold" in text


def test_activity_text_very_hard_effort():
    race = {**BASE_ACTIVITY, "avg_heart_rate": 175}
    text = vector_store._activity_to_text(race)
    assert "race" in text or "very hard" in text


def test_activity_text_elevation_shown_when_significant():
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "85m" in text


def test_activity_text_elevation_hidden_when_flat():
    flat = {**BASE_ACTIVITY, "elevation_gain": 5.0}
    text = vector_store._activity_to_text(flat)
    assert "elevation" not in text


def test_activity_text_training_effect_shown():
    text = vector_store._activity_to_text(BASE_ACTIVITY)
    assert "3.2" in text


def test_activity_text_handles_missing_hr():
    no_hr = {**BASE_ACTIVITY, "avg_heart_rate": None}
    text = vector_store._activity_to_text(no_hr)
    assert "N/A" in text


def test_activity_text_handles_invalid_date():
    bad_date = {**BASE_ACTIVITY, "start_time": "not-a-date"}
    text = vector_store._activity_to_text(bad_date)
    assert text  # should not raise


# ---- _activity_metadata ----

def test_metadata_has_required_fields():
    meta = vector_store._activity_metadata(BASE_ACTIVITY)
    for field in ["activity_id", "date", "year", "month", "week", "distance_km",
                  "avg_pace_per_km", "avg_heart_rate", "duration_seconds"]:
        assert field in meta, f"Missing field: {field}"


def test_metadata_year_month_extracted():
    meta = vector_store._activity_metadata(BASE_ACTIVITY)
    assert meta["year"] == 2024
    assert meta["month"] == 6


def test_metadata_distance_converted_to_km():
    meta = vector_store._activity_metadata(BASE_ACTIVITY)
    assert meta["distance_km"] == pytest.approx(10.0)


def test_metadata_handles_bad_date():
    bad = {**BASE_ACTIVITY, "start_time": ""}
    meta = vector_store._activity_metadata(bad)
    assert meta["year"] == 0
    assert meta["month"] == 0


# ---- _fmt_pace / _fmt_duration (internal helpers) ----

def test_fmt_pace_whole_minutes():
    assert vector_store._fmt_pace(5.0) == "5:00/km"


def test_fmt_pace_with_seconds():
    assert vector_store._fmt_pace(5.5) == "5:30/km"


def test_fmt_pace_none():
    assert vector_store._fmt_pace(None) == "unknown pace"


def test_fmt_duration_minutes_only():
    assert vector_store._fmt_duration(1800.0) == "30m"


def test_fmt_duration_hours_and_minutes():
    assert vector_store._fmt_duration(5400.0) == "1h 30m"


def test_fmt_duration_none():
    assert vector_store._fmt_duration(None) == "unknown duration"
