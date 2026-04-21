"""
Tests for garmin_client helpers and sync logic.
Actual Garmin API calls are mocked — no network required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import garmin_client


# ---- Constants ----

def test_running_types_includes_standard_types():
    for t in ["running", "trail_running", "treadmill_running", "track_running"]:
        assert t in garmin_client.RUNNING_TYPES


def test_page_size_is_reasonable():
    assert 50 <= garmin_client.ACTIVITIES_PAGE_SIZE <= 200


# ---- GarminSyncError / MFARequired ----

def test_sync_error_is_exception():
    err = garmin_client.GarminSyncError("oops")
    assert isinstance(err, Exception)
    assert str(err) == "oops"


def test_mfa_required_is_exception():
    err = garmin_client.MFARequired("need code")
    assert isinstance(err, Exception)


# ---- sync_all with mocked Garmin client ----

from datetime import date as _date
_today = _date.today().isoformat()

FAKE_RUN = {
    "activityId": "1",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": f"{_today} 07:00:00",
    "duration": 1800.0,
    "distance": 5000.0,
    "averageHR": 148,
    "maxHR": 165,
    "elevationGain": 30.0,
    "calories": 350,
    "aerobicTrainingEffect": 2.5,
}


async def test_sync_all_stores_activities(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    fake_client = MagicMock()
    fake_client.get_activities.return_value = [FAKE_RUN]
    fake_client.get_sleep_data.return_value = None
    fake_client.get_stats.return_value = None

    with patch("garmin_client._build_client", return_value=fake_client):
        results = await garmin_client.sync_all("a@b.com", "pw", days=7)

    assert results["activities"] == 1
    rows = await database.get_recent_activities(10)
    assert len(rows) == 1
    assert rows[0]["id"] == "1"


async def test_sync_all_raises_on_mfa_required(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    with patch("garmin_client._build_client", side_effect=garmin_client.MFARequired("mfa")):
        with pytest.raises(garmin_client.GarminSyncError, match="MFA_REQUIRED"):
            await garmin_client.sync_all("a@b.com", "pw")


async def test_sync_all_detects_mfa_in_wrapped_exception(tmp_path):
    # garminconnect wraps our MFARequired inside "Login failed: MFA required..."
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    wrapped = Exception("Login failed: MFA required — enter your 6-digit code and try again.")
    with patch("garmin_client._build_client", side_effect=wrapped):
        with pytest.raises(garmin_client.GarminSyncError, match="MFA_REQUIRED"):
            await garmin_client.sync_all("a@b.com", "pw")


async def test_sync_all_raises_on_auth_failure(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    from garminconnect import GarminConnectAuthenticationError
    with patch("garmin_client._build_client", side_effect=GarminConnectAuthenticationError("bad creds")):
        with pytest.raises(garmin_client.GarminSyncError, match="Authentication failed"):
            await garmin_client.sync_all("a@b.com", "wrong")


async def test_sync_skips_non_running_activities(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    cycling = {**FAKE_RUN, "activityId": "2", "activityType": {"typeKey": "cycling"}}
    fake_client = MagicMock()
    fake_client.get_activities.return_value = [FAKE_RUN, cycling]
    fake_client.get_sleep_data.return_value = None
    fake_client.get_stats.return_value = None

    with patch("garmin_client._build_client", return_value=fake_client):
        results = await garmin_client.sync_all("a@b.com", "pw", days=30)

    # Only the run should be stored
    rows = await database.get_recent_activities(10)
    assert all(r["activity_type"] == "running" for r in rows)


async def test_sync_full_history_paginates(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    original_page_size = garmin_client.ACTIVITIES_PAGE_SIZE
    garmin_client.ACTIVITIES_PAGE_SIZE = 2

    page1 = [
        {**FAKE_RUN, "activityId": "1"},
        {**FAKE_RUN, "activityId": "2"},
    ]
    page2 = [
        {**FAKE_RUN, "activityId": "3"},
    ]

    call_count = 0
    def fake_get_activities(start, limit):
        nonlocal call_count
        call_count += 1
        return page1 if start == 0 else page2

    fake_client = MagicMock()
    fake_client.get_activities.side_effect = fake_get_activities
    fake_client.get_sleep_data.return_value = None
    fake_client.get_stats.return_value = None

    with patch("garmin_client._build_client", return_value=fake_client):
        results = await garmin_client.sync_full_history("a@b.com", "pw")

    garmin_client.ACTIVITIES_PAGE_SIZE = original_page_size

    assert results["activities"] == 3
    assert call_count == 2  # two pages fetched
