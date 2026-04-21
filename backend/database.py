import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "garmin.db"

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    activity_type TEXT,
    name TEXT,
    start_time TEXT,
    duration_seconds REAL,
    distance_meters REAL,
    avg_pace_per_km REAL,
    avg_heart_rate INTEGER,
    max_heart_rate INTEGER,
    elevation_gain REAL,
    calories INTEGER,
    aerobic_training_effect REAL,
    raw_json TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS sleep_data (
    date TEXT PRIMARY KEY,
    sleep_score INTEGER,
    total_sleep_seconds INTEGER,
    deep_sleep_seconds INTEGER,
    light_sleep_seconds INTEGER,
    rem_sleep_seconds INTEGER,
    awake_seconds INTEGER,
    avg_hrv REAL,
    raw_json TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    steps INTEGER,
    resting_heart_rate INTEGER,
    avg_stress_level INTEGER,
    body_battery_low INTEGER,
    body_battery_high INTEGER,
    active_calories INTEGER,
    raw_json TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_time TEXT,
    activities_synced INTEGER,
    sleep_days_synced INTEGER,
    stats_days_synced INTEGER,
    status TEXT,
    error TEXT
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        for statement in CREATE_TABLES.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                await db.execute(stmt)
        await db.commit()


async def upsert_activity(activity: dict):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO activities
               (id, activity_type, name, start_time, duration_seconds, distance_meters,
                avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                calories, aerobic_training_effect, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(activity.get("activityId", "")),
                activity.get("activityType", {}).get("typeKey", "unknown"),
                activity.get("activityName", ""),
                activity.get("startTimeLocal", ""),
                activity.get("duration", 0),
                activity.get("distance", 0),
                _pace_from_activity(activity),
                activity.get("averageHR"),
                activity.get("maxHR"),
                activity.get("elevationGain", 0),
                activity.get("calories", 0),
                activity.get("aerobicTrainingEffect"),
                json.dumps(activity),
                now,
            ),
        )
        await db.commit()


async def upsert_sleep(date: str, data: dict):
    now = datetime.now(timezone.utc).isoformat()
    daily = data.get("dailySleepDTO", {})
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO sleep_data
               (date, sleep_score, total_sleep_seconds, deep_sleep_seconds,
                light_sleep_seconds, rem_sleep_seconds, awake_seconds, avg_hrv, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                date,
                daily.get("sleepScores", {}).get("overall", {}).get("value"),
                daily.get("sleepTimeSeconds"),
                daily.get("deepSleepSeconds"),
                daily.get("lightSleepSeconds"),
                daily.get("remSleepSeconds"),
                daily.get("awakeSleepSeconds"),
                data.get("avgOvernightHrv"),
                json.dumps(data),
                now,
            ),
        )
        await db.commit()


async def upsert_daily_stats(date: str, data: dict):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO daily_stats
               (date, steps, resting_heart_rate, avg_stress_level,
                body_battery_low, body_battery_high, active_calories, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                date,
                data.get("totalSteps"),
                data.get("restingHeartRate"),
                data.get("averageStressLevel"),
                data.get("minBodyBattery"),
                data.get("maxBodyBattery"),
                data.get("activeKilocalories"),
                json.dumps(data),
                now,
            ),
        )
        await db.commit()


async def log_sync(activities: int, sleep_days: int, stats_days: int, status: str, error: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO sync_log (sync_time, activities_synced, sleep_days_synced, stats_days_synced, status, error)
               VALUES (?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), activities, sleep_days, stats_days, status, error),
        )
        await db.commit()


async def get_recent_activities(limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, activity_type, name, start_time, duration_seconds, distance_meters,
                      avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                      calories, aerobic_training_effect
               FROM activities ORDER BY start_time DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_recent_sleep(days: int = 14) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT date, sleep_score, total_sleep_seconds, deep_sleep_seconds,
                      light_sleep_seconds, rem_sleep_seconds, awake_seconds, avg_hrv
               FROM sleep_data ORDER BY date DESC LIMIT ?""",
            (days,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_recent_daily_stats(days: int = 14) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT date, steps, resting_heart_rate, avg_stress_level,
                      body_battery_low, body_battery_high, active_calories
               FROM daily_stats ORDER BY date DESC LIMIT ?""",
            (days,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_last_sync() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


def _pace_from_activity(activity: dict) -> float | None:
    distance = activity.get("distance", 0)
    duration = activity.get("duration", 0)
    if distance and duration and distance > 0:
        return (duration / 60) / (distance / 1000)
    return None
