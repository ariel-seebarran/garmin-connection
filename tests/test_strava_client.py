"""
Tests for strava_client helpers and sync logic.
All HTTP calls are mocked — no network required.
"""

import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import strava_client


# ---- Constants ----

def test_running_sport_types_is_non_empty():
    assert len(strava_client.RUNNING_SPORT_TYPES) > 0


def test_running_sport_types_includes_core_types():
    for t in ("Run", "TrailRun", "Treadmill"):
        assert t in strava_client.RUNNING_SPORT_TYPES


def test_sport_type_map_run_to_running():
    assert strava_client.SPORT_TYPE_MAP["Run"] == "running"


def test_sport_type_map_trail_run():
    assert strava_client.SPORT_TYPE_MAP["TrailRun"] == "trail_running"


def test_sport_type_map_treadmill():
    assert strava_client.SPORT_TYPE_MAP["Treadmill"] == "treadmill_running"


# ---- get_auth_url ----

def test_get_auth_url_contains_client_id():
    url = strava_client.get_auth_url("my_id", "http://localhost/cb")
    assert "client_id=my_id" in url


def test_get_auth_url_contains_scope():
    url = strava_client.get_auth_url("id", "http://localhost/cb")
    assert "activity:read_all" in url


def test_get_auth_url_contains_redirect_uri():
    uri = "http://localhost:8000/api/strava/callback"
    url = strava_client.get_auth_url("id", uri)
    assert uri in url


# ---- _map_activity ----

FAKE_STRAVA_ACT = {
    "id": 99887766,
    "name": "Morning Run",
    "sport_type": "Run",
    "start_date_local": "2024-03-15T07:30:00Z",
    "moving_time": 1800,
    "distance": 5000.0,
    "average_heartrate": 148,
    "max_heartrate": 170,
    "total_elevation_gain": 45.0,
    "calories": 380,
    "average_watts": None,
}


def test_map_activity_prefixes_id():
    result = strava_client._map_activity(FAKE_STRAVA_ACT)
    assert result["id"] == "strava_99887766"


def test_map_activity_calculates_pace():
    result = strava_client._map_activity(FAKE_STRAVA_ACT)
    # 1800s / 60 = 30 min, 5000m / 1000 = 5km → 6.0 min/km
    assert result["avg_pace_per_km"] == pytest.approx(6.0, rel=0.01)


def test_map_activity_zero_distance_gives_no_pace():
    act = {**FAKE_STRAVA_ACT, "distance": 0}
    result = strava_client._map_activity(act)
    assert result["avg_pace_per_km"] is None


def test_map_activity_strips_trailing_z_from_date():
    result = strava_client._map_activity(FAKE_STRAVA_ACT)
    assert not result["start_time"].endswith("Z")
    assert "2024-03-15" in result["start_time"]


def test_map_activity_maps_sport_type():
    result = strava_client._map_activity(FAKE_STRAVA_ACT)
    assert result["activity_type"] == "running"


def test_map_activity_trail_run_maps_correctly():
    act = {**FAKE_STRAVA_ACT, "sport_type": "TrailRun"}
    result = strava_client._map_activity(act)
    assert result["activity_type"] == "trail_running"


def test_map_activity_unknown_sport_type_defaults_to_running():
    act = {**FAKE_STRAVA_ACT, "sport_type": "AlienRun"}
    result = strava_client._map_activity(act)
    assert result["activity_type"] == "running"


def test_map_activity_raw_json_is_serialized():
    result = strava_client._map_activity(FAKE_STRAVA_ACT)
    parsed = json.loads(result["raw_json"])
    assert parsed["id"] == FAKE_STRAVA_ACT["id"]


# ---- sync_activities (mocked HTTP) ----

def _make_http_mock(pages: list[list]) -> MagicMock:
    """Return a mock httpx.AsyncClient whose .get() returns pages in sequence."""
    responses = []
    for page in pages:
        r = MagicMock()
        r.json.return_value = page
        r.raise_for_status = MagicMock()
        responses.append(r)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=responses)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


async def test_sync_activities_stores_running_only(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    run = {**FAKE_STRAVA_ACT, "id": 1, "sport_type": "Run"}
    ride = {**FAKE_STRAVA_ACT, "id": 2, "sport_type": "Ride"}
    mock_http = _make_http_mock([[run, ride], []])

    with patch("strava_client._get_valid_token", return_value="tok"), \
         patch("httpx.AsyncClient", return_value=mock_http):
        result = await strava_client.sync_activities("cid", "csec", days=7)

    assert result["activities"] == 1
    rows = await database.get_recent_activities(10)
    assert len(rows) == 1
    assert rows[0]["id"] == "strava_1"


async def test_sync_activities_returns_correct_count(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    acts = [{**FAKE_STRAVA_ACT, "id": i, "sport_type": "Run"} for i in range(3)]
    mock_http = _make_http_mock([acts, []])

    with patch("strava_client._get_valid_token", return_value="tok"), \
         patch("httpx.AsyncClient", return_value=mock_http):
        result = await strava_client.sync_activities("cid", "csec", days=30)

    assert result["activities"] == 3
    assert result["errors"] == []


async def test_sync_activities_paginates(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    # First page has 100 items (triggers next page fetch), second has 1
    page1 = [{**FAKE_STRAVA_ACT, "id": i, "sport_type": "Run"} for i in range(100)]
    page2 = [{**FAKE_STRAVA_ACT, "id": 100, "sport_type": "Run"}]
    mock_http = _make_http_mock([page1, page2, []])

    with patch("strava_client._get_valid_token", return_value="tok"), \
         patch("httpx.AsyncClient", return_value=mock_http):
        result = await strava_client.sync_activities("cid", "csec", days=90)

    assert result["activities"] == 101


async def test_sync_activities_raises_when_not_connected(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    with pytest.raises(ValueError, match="not connected"):
        await strava_client.sync_activities("cid", "csec", days=7)


async def test_get_valid_token_refreshes_expired_token(tmp_path):
    import database
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()

    await database.save_strava_tokens(
        access_token="old_token",
        refresh_token="refresh_tok",
        expires_at=int(time.time()) - 3600,  # expired 1h ago
        athlete_id=42,
        athlete_name="Test Runner",
    )

    new_token_resp = MagicMock()
    new_token_resp.json.return_value = {
        "access_token": "new_token",
        "refresh_token": "new_refresh",
        "expires_at": int(time.time()) + 21600,
    }
    new_token_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=new_token_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        token = await strava_client._get_valid_token("cid", "csec")

    assert token == "new_token"
    saved = await database.get_strava_tokens()
    assert saved["access_token"] == "new_token"
