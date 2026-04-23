"""
FastAPI endpoint tests using an in-process test client.
Garmin and Claude calls are mocked — only the HTTP layer and business logic are exercised.
"""

import pytest
import os
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from fastapi.testclient import TestClient
from conftest import SAMPLE_ACTIVITY, SAMPLE_SLEEP, SAMPLE_STATS


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with an isolated DB and no real Garmin/Claude calls."""
    import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")

    # Import app after patching DB path so lifespan picks up the right path
    import importlib
    import main as m
    importlib.reload(m)

    with TestClient(m.app) as c:
        yield c


# ---- /api/stats ----

def test_stats_empty_db(client):
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    assert data["run_count_30d"] == 0
    assert data["total_distance_30d"] == 0.0
    assert data["last_sync"] is None


def test_stats_returns_required_fields(client):
    res = client.get("/api/stats")
    data = res.json()
    for field in ["total_distance_30d", "weekly_distance", "run_count_30d",
                  "avg_sleep_score_7d", "avg_hrv_7d", "avg_resting_hr_7d",
                  "last_run_date", "last_sync", "total_indexed"]:
        assert field in data, f"Missing field: {field}"


# ---- /api/activities ----

def test_activities_empty_db(client):
    res = client.get("/api/activities")
    assert res.status_code == 200
    assert res.json()["activities"] == []


def test_activities_default_limit(client):
    res = client.get("/api/activities")
    assert res.status_code == 200
    assert "activities" in res.json()


def test_activities_custom_limit(client):
    res = client.get("/api/activities?limit=5")
    assert res.status_code == 200


# ---- /api/sync ----

def test_sync_missing_credentials(client, monkeypatch):
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    res = client.post("/api/sync", json={})
    assert res.status_code == 400
    assert "credentials" in res.json()["detail"].lower()


def test_sync_mfa_required_returns_449(client, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "pw")

    import garmin_client
    with patch.object(garmin_client, "sync_all", side_effect=garmin_client.GarminSyncError("MFA_REQUIRED")):
        res = client.post("/api/sync", json={"days": 7})
    assert res.status_code == 449


def test_sync_auth_failure_returns_401(client, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "wrong")

    import garmin_client
    with patch.object(garmin_client, "sync_all", side_effect=garmin_client.GarminSyncError("Authentication failed")):
        res = client.post("/api/sync", json={"days": 7})
    assert res.status_code == 401


def test_sync_success(client, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "pw")

    import garmin_client
    import vector_store

    fake_results = {"activities": 5, "sleep_days": 3, "stats_days": 3, "errors": []}

    with patch.object(garmin_client, "sync_all", return_value=fake_results), \
         patch.object(vector_store, "index_all_activities", return_value=5):
        res = client.post("/api/sync", json={"days": 7})

    assert res.status_code == 200
    data = res.json()
    assert data["activities"] == 5
    assert data["indexed_in_vector_store"] == 5


# ---- /api/chat ----

def test_chat_missing_api_key(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    res = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert res.status_code == 500
    assert "GOOGLE_API_KEY" in res.json()["detail"]


# ---- /api/search ----

def test_search_returns_results_structure(client):
    import vector_store
    with patch.object(vector_store, "search_activities", return_value=[]):
        res = client.get("/api/search?q=long+run")
    assert res.status_code == 200
    assert "results" in res.json()


# ---- /api/index ----

def test_reindex_endpoint(client):
    import vector_store
    with patch.object(vector_store, "index_all_activities", return_value=42):
        res = client.post("/api/index")
    assert res.status_code == 200
    assert res.json()["indexed"] == 42


# ---- /api/activities/{id} ----

def test_activity_detail_not_found(client):
    res = client.get("/api/activities/nonexistent_id")
    assert res.status_code == 404


def test_activity_detail_found(client):
    import database, asyncio
    from conftest import SAMPLE_ACTIVITY
    asyncio.run(database.upsert_activity(SAMPLE_ACTIVITY))
    res = client.get("/api/activities/123456789")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "123456789"
    assert data["activity_type"] == "running"
    assert "raw_data" in data


# ---- /api/strava/status ----

def test_strava_status_disconnected(client):
    res = client.get("/api/strava/status")
    assert res.status_code == 200
    assert res.json()["connected"] is False


def test_strava_status_connected(client):
    import database, asyncio, time
    asyncio.run(
        database.save_strava_tokens("tok", "ref", int(time.time()) + 3600, 99, "Test Runner")
    )
    res = client.get("/api/strava/status")
    assert res.status_code == 200
    data = res.json()
    assert data["connected"] is True
    assert data["athlete_name"] == "Test Runner"


# ---- /api/strava/auth ----

def test_strava_auth_missing_env(client, monkeypatch):
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    res = client.get("/api/strava/auth", follow_redirects=False)
    assert res.status_code == 400


def test_strava_auth_redirects_to_strava(client, monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "http://localhost/cb")
    res = client.get("/api/strava/auth", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert "strava.com" in res.headers["location"]
    assert "test_client_id" in res.headers["location"]


# ---- /api/strava/sync ----

def test_strava_sync_missing_env(client, monkeypatch):
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    res = client.post("/api/strava/sync")
    assert res.status_code == 400


def test_strava_sync_not_connected(client, monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "cid")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "csec")
    import strava_client
    with patch.object(strava_client, "sync_activities", side_effect=ValueError("Strava not connected")):
        res = client.post("/api/strava/sync")
    assert res.status_code == 400
    assert "not connected" in res.json()["detail"].lower()


def test_strava_sync_success(client, monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "cid")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "csec")
    import strava_client, vector_store
    fake = {"activities": 4, "errors": []}
    with patch.object(strava_client, "sync_activities", return_value=fake), \
         patch.object(vector_store, "index_all_activities", return_value=4):
        res = client.post("/api/strava/sync?days=14")
    assert res.status_code == 200
    data = res.json()
    assert data["activities"] == 4
    assert data["indexed_in_vector_store"] == 4


# ---- Frontend ----

def test_frontend_served_at_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
