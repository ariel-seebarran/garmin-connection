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
