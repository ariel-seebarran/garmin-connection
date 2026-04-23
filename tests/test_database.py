import pytest
from conftest import SAMPLE_ACTIVITY, SAMPLE_SLEEP, SAMPLE_STATS


async def test_upsert_and_fetch_activity(tmp_db):
    await tmp_db.upsert_activity(SAMPLE_ACTIVITY)
    rows = await tmp_db.get_recent_activities(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "123456789"
    assert row["activity_type"] == "running"
    assert row["avg_heart_rate"] == 145
    assert row["distance_meters"] == 10000.0


async def test_upsert_activity_is_idempotent(tmp_db):
    await tmp_db.upsert_activity(SAMPLE_ACTIVITY)
    await tmp_db.upsert_activity(SAMPLE_ACTIVITY)  # same id, should replace
    rows = await tmp_db.get_recent_activities(10)
    assert len(rows) == 1


async def test_pace_calculated_on_upsert(tmp_db):
    await tmp_db.upsert_activity(SAMPLE_ACTIVITY)
    rows = await tmp_db.get_recent_activities(10)
    # 3600s / (10000m / 1000) = 6.0 min/km
    assert rows[0]["avg_pace_per_km"] == pytest.approx(6.0, rel=0.01)


async def test_pace_none_for_zero_distance(tmp_db):
    activity = {**SAMPLE_ACTIVITY, "activityId": "zero", "distance": 0}
    await tmp_db.upsert_activity(activity)
    rows = await tmp_db.get_recent_activities(10)
    assert rows[0]["avg_pace_per_km"] is None


async def test_upsert_and_fetch_sleep(tmp_db):
    await tmp_db.upsert_sleep("2024-03-15", SAMPLE_SLEEP)
    rows = await tmp_db.get_recent_sleep(7)
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2024-03-15"
    assert row["sleep_score"] == 78
    assert row["total_sleep_seconds"] == 27000
    assert row["avg_hrv"] == pytest.approx(52.3)


async def test_upsert_sleep_is_idempotent(tmp_db):
    await tmp_db.upsert_sleep("2024-03-15", SAMPLE_SLEEP)
    await tmp_db.upsert_sleep("2024-03-15", SAMPLE_SLEEP)
    rows = await tmp_db.get_recent_sleep(7)
    assert len(rows) == 1


async def test_upsert_and_fetch_daily_stats(tmp_db):
    await tmp_db.upsert_daily_stats("2024-03-15", SAMPLE_STATS)
    rows = await tmp_db.get_recent_daily_stats(7)
    assert len(rows) == 1
    row = rows[0]
    assert row["steps"] == 8500
    assert row["resting_heart_rate"] == 52
    assert row["body_battery_high"] == 87


async def test_log_sync_and_get_last(tmp_db):
    await tmp_db.log_sync(10, 7, 7, "success")
    last = await tmp_db.get_last_sync()
    assert last is not None
    assert last["activities_synced"] == 10
    assert last["status"] == "success"
    assert last["error"] is None


async def test_log_sync_with_error(tmp_db):
    await tmp_db.log_sync(0, 0, 0, "partial", "Sleep 2024-03-10 failed")
    last = await tmp_db.get_last_sync()
    assert last["status"] == "partial"
    assert "Sleep" in last["error"]


async def test_get_last_sync_returns_none_on_empty(tmp_db):
    last = await tmp_db.get_last_sync()
    assert last is None


async def test_activities_ordered_newest_first(tmp_db):
    older = {**SAMPLE_ACTIVITY, "activityId": "old", "startTimeLocal": "2024-01-01 08:00:00"}
    newer = {**SAMPLE_ACTIVITY, "activityId": "new", "startTimeLocal": "2024-06-01 08:00:00"}
    await tmp_db.upsert_activity(older)
    await tmp_db.upsert_activity(newer)
    rows = await tmp_db.get_recent_activities(10)
    assert rows[0]["id"] == "new"
    assert rows[1]["id"] == "old"


async def test_get_recent_activities_respects_limit(tmp_db):
    for i in range(5):
        a = {**SAMPLE_ACTIVITY, "activityId": str(i), "startTimeLocal": f"2024-0{i+1}-01 08:00:00"}
        await tmp_db.upsert_activity(a)
    rows = await tmp_db.get_recent_activities(limit=3)
    assert len(rows) == 3


# ---- get_activity_detail ----

async def test_get_activity_detail_found(tmp_db):
    await tmp_db.upsert_activity(SAMPLE_ACTIVITY)
    detail = await tmp_db.get_activity_detail("123456789")
    assert detail is not None
    assert detail["id"] == "123456789"
    assert detail["activity_type"] == "running"
    assert "raw_data" in detail


async def test_get_activity_detail_not_found(tmp_db):
    detail = await tmp_db.get_activity_detail("nonexistent_id")
    assert detail is None


# ---- strava_tokens ----

async def test_save_and_get_strava_tokens(tmp_db):
    import time
    expires = int(time.time()) + 21600
    await tmp_db.save_strava_tokens("acc_tok", "ref_tok", expires, 12345, "Jane Doe")
    tokens = await tmp_db.get_strava_tokens()
    assert tokens is not None
    assert tokens["access_token"] == "acc_tok"
    assert tokens["refresh_token"] == "ref_tok"
    assert tokens["athlete_name"] == "Jane Doe"
    assert tokens["athlete_id"] == 12345


async def test_strava_tokens_singleton(tmp_db):
    import time
    expires = int(time.time()) + 3600
    await tmp_db.save_strava_tokens("tok1", "ref1", expires, 1, "Alice")
    await tmp_db.save_strava_tokens("tok2", "ref2", expires, 2, "Bob")
    tokens = await tmp_db.get_strava_tokens()
    assert tokens["access_token"] == "tok2"
    assert tokens["athlete_name"] == "Bob"


async def test_get_strava_tokens_returns_none_when_empty(tmp_db):
    tokens = await tmp_db.get_strava_tokens()
    assert tokens is None


# ---- upsert_strava_activity ----

async def test_upsert_strava_activity(tmp_db):
    import json, time as _time
    act = {
        "id": "strava_99887766",
        "activity_type": "running",
        "name": "Strava Morning Run",
        "start_time": "2024-03-15 07:30:00",
        "duration_seconds": 1800.0,
        "distance_meters": 5000.0,
        "avg_pace_per_km": 6.0,
        "avg_heart_rate": 148,
        "max_heart_rate": 170,
        "elevation_gain": 45.0,
        "calories": 380,
        "avg_power": None,
        "training_stress_score": None,
        "raw_json": json.dumps({"id": 99887766}),
    }
    await tmp_db.upsert_strava_activity(act)
    rows = await tmp_db.get_recent_activities(10)
    assert len(rows) == 1
    assert rows[0]["id"] == "strava_99887766"
    assert rows[0]["avg_heart_rate"] == 148


async def test_upsert_strava_activity_is_idempotent(tmp_db):
    import json
    act = {
        "id": "strava_111",
        "activity_type": "running",
        "name": "Run",
        "start_time": "2024-03-15 07:30:00",
        "duration_seconds": 1800.0,
        "distance_meters": 5000.0,
        "avg_pace_per_km": 6.0,
        "avg_heart_rate": None,
        "max_heart_rate": None,
        "elevation_gain": None,
        "calories": None,
        "avg_power": None,
        "training_stress_score": None,
        "raw_json": json.dumps({}),
    }
    await tmp_db.upsert_strava_activity(act)
    await tmp_db.upsert_strava_activity(act)
    rows = await tmp_db.get_recent_activities(10)
    assert len(rows) == 1
