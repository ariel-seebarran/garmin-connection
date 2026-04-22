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
    anaerobic_training_effect REAL,
    avg_power INTEGER,
    avg_vertical_oscillation REAL,
    avg_ground_contact_time INTEGER,
    avg_stride_length REAL,
    training_stress_score REAL,
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
    total_calories INTEGER,
    floors_climbed INTEGER,
    intensity_minutes_moderate INTEGER,
    intensity_minutes_vigorous INTEGER,
    avg_spo2 REAL,
    avg_respiration_rate REAL,
    raw_json TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS training_metrics (
    date TEXT PRIMARY KEY,
    vo2_max REAL,
    training_readiness_score INTEGER,
    training_readiness_level TEXT,
    race_5k_seconds INTEGER,
    race_10k_seconds INTEGER,
    race_half_seconds INTEGER,
    race_marathon_seconds INTEGER,
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

CREATE TABLE IF NOT EXISTS strava_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT,
    refresh_token TEXT,
    expires_at INTEGER,
    athlete_id INTEGER,
    athlete_name TEXT,
    updated_at TEXT
);
"""

# Columns to add when upgrading an existing database
_COLUMN_MIGRATIONS = [
    ("activities", "anaerobic_training_effect", "REAL"),
    ("activities", "avg_power", "INTEGER"),
    ("activities", "avg_vertical_oscillation", "REAL"),
    ("activities", "avg_ground_contact_time", "INTEGER"),
    ("activities", "avg_stride_length", "REAL"),
    ("activities", "training_stress_score", "REAL"),
    ("daily_stats", "total_calories", "INTEGER"),
    ("daily_stats", "floors_climbed", "INTEGER"),
    ("daily_stats", "intensity_minutes_moderate", "INTEGER"),
    ("daily_stats", "intensity_minutes_vigorous", "INTEGER"),
    ("daily_stats", "avg_spo2", "REAL"),
    ("daily_stats", "avg_respiration_rate", "REAL"),
]


async def _migrate_db(db):
    for table, column, col_type in _COLUMN_MIGRATIONS:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception:
            pass  # column already exists


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        for statement in CREATE_TABLES.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                await db.execute(stmt)
        await _migrate_db(db)
        await db.commit()


async def upsert_activity(activity: dict):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO activities
               (id, activity_type, name, start_time, duration_seconds, distance_meters,
                avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                calories, aerobic_training_effect, anaerobic_training_effect,
                avg_power, avg_vertical_oscillation, avg_ground_contact_time,
                avg_stride_length, training_stress_score, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                activity.get("anaerobicTrainingEffect"),
                activity.get("avgPower"),
                activity.get("avgVerticalOscillation"),
                activity.get("avgGroundContactTime"),
                activity.get("avgStrideLength"),
                activity.get("trainingStressScore"),
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
                body_battery_low, body_battery_high, active_calories,
                total_calories, floors_climbed,
                intensity_minutes_moderate, intensity_minutes_vigorous,
                raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date,
                data.get("totalSteps"),
                data.get("restingHeartRate"),
                data.get("averageStressLevel"),
                data.get("minBodyBattery"),
                data.get("maxBodyBattery"),
                data.get("activeKilocalories"),
                data.get("totalKilocalories"),
                data.get("floorsAscended"),
                data.get("moderateIntensityMinutes"),
                data.get("vigorousIntensityMinutes"),
                json.dumps(data),
                now,
            ),
        )
        await db.commit()


async def update_daily_biometrics(date: str, spo2: float | None = None, respiration: float | None = None):
    """Update SpO2 and/or respiration for a day (creates stub row if needed)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO daily_stats (date, synced_at) VALUES (?, ?)", (date, now)
        )
        if spo2 is not None:
            await db.execute("UPDATE daily_stats SET avg_spo2 = ? WHERE date = ?", (spo2, date))
        if respiration is not None:
            await db.execute("UPDATE daily_stats SET avg_respiration_rate = ? WHERE date = ?", (respiration, date))
        await db.commit()


async def upsert_training_readiness(date: str, data: dict):
    now = datetime.now(timezone.utc).isoformat()
    dto = data.get("trainingReadinessDTO") or data
    score = dto.get("score") or dto.get("trainingReadinessScore")
    level = (dto.get("scoreDesc") or dto.get("level") or dto.get("trainingReadinessLevel") or "")
    level = level.upper() if level else None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO training_metrics (date, training_readiness_score, training_readiness_level, synced_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   training_readiness_score = excluded.training_readiness_score,
                   training_readiness_level = excluded.training_readiness_level,
                   synced_at = excluded.synced_at""",
            (date, score, level, now),
        )
        await db.commit()


async def upsert_max_metrics(date: str, data: dict):
    """Store VO2 max from Garmin's maxmet/daily endpoint (multiple response formats)."""
    now = datetime.now(timezone.utc).isoformat()

    # Format 1: direct camelCase keys (maxmet/daily newer API)
    vo2 = data.get("vo2MaxRunning") or data.get("vo2MaxCycling") or data.get("vo2Max")

    # Format 2: allMetrics.metricsMap structure (older API)
    if vo2 is None:
        metrics_map = (data.get("allMetrics") or {}).get("metricsMap") or {}
        def first_val(key):
            items = metrics_map.get(key) or []
            return items[0].get("value") if items else None
        vo2 = first_val("METRIC_VO2_MAX_RUNNING") or first_val("METRIC_VO2_MAX")

    if vo2 is None:
        return  # nothing to store

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO training_metrics (date, vo2_max, synced_at)
               VALUES (?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   vo2_max = excluded.vo2_max,
                   synced_at = excluded.synced_at""",
            (date, vo2, now),
        )
        await db.commit()


async def sync_vo2_from_activities() -> int:
    """Populate training_metrics.vo2_max from vO2MaxValue stored in activity raw_json."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT start_time, raw_json FROM activities WHERE raw_json IS NOT NULL ORDER BY start_time DESC"
        )
        rows = await cur.fetchall()
        updated = 0
        seen_dates: set[str] = set()
        for row in rows:
            date = (row["start_time"] or "")[:10]
            if not date or date in seen_dates:
                continue
            try:
                vo2 = json.loads(row["raw_json"]).get("vO2MaxValue")
                if vo2 and float(vo2) > 0:
                    await db.execute(
                        """INSERT INTO training_metrics (date, vo2_max, synced_at)
                           VALUES (?, ?, ?)
                           ON CONFLICT(date) DO UPDATE SET
                               vo2_max = excluded.vo2_max,
                               synced_at = excluded.synced_at""",
                        (date, float(vo2), now),
                    )
                    seen_dates.add(date)
                    updated += 1
            except Exception:
                pass
        await db.commit()
        return updated


async def upsert_training_status_vo2(date: str, data: dict):
    """Extract VO2 max from training status response and store it."""
    # Try common key names used in trainingstatus/aggregated response
    vo2 = (data.get("vo2Max") or data.get("vo2MaxRunning") or data.get("vo2MaxValue")
           or data.get("latestVO2Max") or data.get("latestVo2Max"))

    # Some responses nest it under a metrics object
    if vo2 is None:
        for key in ("mostRecentVO2Max", "mostRecentVo2Max", "currentVo2Max"):
            sub = data.get(key)
            if isinstance(sub, dict):
                vo2 = sub.get("value") or sub.get("vo2Max")
            elif sub:
                vo2 = sub
            if vo2:
                break

    if vo2 is None:
        return

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO training_metrics (date, vo2_max, synced_at)
               VALUES (?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   vo2_max = excluded.vo2_max,
                   synced_at = excluded.synced_at""",
            (date, float(vo2), now),
        )
        await db.commit()


async def upsert_race_predictions(date: str, data):
    """Store race predictions from Garmin's racepredictions endpoint."""
    now = datetime.now(timezone.utc).isoformat()

    r5k = r10k = rhalf = rmarathon = None

    # Format 1: list of dicts with 'distance'/'raceDistance' and 'seconds'/'raceTime'
    if isinstance(data, list):
        for item in data:
            dist = (item.get("distance") or item.get("raceDistance") or "").upper().replace(" ", "")
            secs = item.get("seconds") or item.get("raceTime") or item.get("timeInSeconds")
            if not secs:
                continue
            if "5K" in dist or dist == "5000":
                r5k = int(secs)
            elif "10K" in dist or dist == "10000":
                r10k = int(secs)
            elif "HALF" in dist or "21" in dist:
                rhalf = int(secs)
            elif "MARATHON" in dist or "42" in dist:
                rmarathon = int(secs)

    # Format 2: dict with direct time keys
    elif isinstance(data, dict):
        def _secs(keys):
            for k in keys:
                v = data.get(k)
                if v:
                    return int(v)
            return None
        r5k = _secs(["time5K", "raceTime5K", "predictedTime5K"])
        r10k = _secs(["time10K", "raceTime10K", "predictedTime10K"])
        rhalf = _secs(["timeHalfMarathon", "raceTimeHalfMarathon", "predictedTimeHalf"])
        rmarathon = _secs(["timeMarathon", "raceTimeMarathon", "predictedTimeMarathon"])

    if not any([r5k, r10k, rhalf, rmarathon]):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO training_metrics (date, race_5k_seconds, race_10k_seconds, race_half_seconds, race_marathon_seconds, synced_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   race_5k_seconds = excluded.race_5k_seconds,
                   race_10k_seconds = excluded.race_10k_seconds,
                   race_half_seconds = excluded.race_half_seconds,
                   race_marathon_seconds = excluded.race_marathon_seconds,
                   synced_at = excluded.synced_at""",
            (date, r5k, r10k, rhalf, rmarathon, now),
        )
        await db.commit()


async def get_performance_data() -> dict:
    """Return latest VO2 max, race predictions, and 14-day training readiness history."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Latest VO2 max
        cur = await db.execute(
            "SELECT date, vo2_max FROM training_metrics WHERE vo2_max IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        vo2_row = await cur.fetchone()

        # Latest race predictions
        cur = await db.execute(
            """SELECT date, race_5k_seconds, race_10k_seconds, race_half_seconds, race_marathon_seconds
               FROM training_metrics
               WHERE race_5k_seconds IS NOT NULL OR race_marathon_seconds IS NOT NULL
               ORDER BY date DESC LIMIT 1"""
        )
        race_row = await cur.fetchone()

        # 30-day readiness history
        cur = await db.execute(
            """SELECT date, training_readiness_score, training_readiness_level
               FROM training_metrics
               WHERE training_readiness_score IS NOT NULL
               ORDER BY date DESC LIMIT 30"""
        )
        readiness_rows = await cur.fetchall()

        return {
            "vo2_max": dict(vo2_row) if vo2_row else None,
            "race_predictions": dict(race_row) if race_row else None,
            "readiness_history": [dict(r) for r in readiness_rows],
        }


async def save_strava_tokens(
    access_token: str, refresh_token: str, expires_at: int,
    athlete_id: int | None = None, athlete_name: str | None = None,
):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO strava_tokens
               (id, access_token, refresh_token, expires_at, athlete_id, athlete_name, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?)""",
            (access_token, refresh_token, expires_at, athlete_id, athlete_name, now),
        )
        await db.commit()


async def get_strava_tokens() -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM strava_tokens WHERE id = 1")
        row = await cur.fetchone()
        return dict(row) if row else None


async def upsert_strava_activity(activity: dict):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO activities
               (id, activity_type, name, start_time, duration_seconds, distance_meters,
                avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                calories, avg_power, training_stress_score, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                activity["id"], activity["activity_type"], activity["name"],
                activity["start_time"], activity["duration_seconds"], activity["distance_meters"],
                activity["avg_pace_per_km"], activity["avg_heart_rate"], activity["max_heart_rate"],
                activity["elevation_gain"], activity["calories"], activity["avg_power"],
                activity["training_stress_score"], activity["raw_json"], now,
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


async def get_activity_detail(activity_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, activity_type, name, start_time, duration_seconds, distance_meters,
                      avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                      calories, aerobic_training_effect, anaerobic_training_effect,
                      avg_power, avg_vertical_oscillation, avg_ground_contact_time,
                      avg_stride_length, training_stress_score, raw_json
               FROM activities WHERE id = ?""",
            (activity_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result['raw_data'] = json.loads(result.pop('raw_json') or '{}')
        except Exception:
            result['raw_data'] = {}
        return result


async def get_recent_activities(limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, activity_type, name, start_time, duration_seconds, distance_meters,
                      avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                      calories, aerobic_training_effect, anaerobic_training_effect,
                      avg_power, avg_vertical_oscillation, avg_ground_contact_time,
                      avg_stride_length, training_stress_score
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
                      body_battery_low, body_battery_high, active_calories,
                      total_calories, floors_climbed,
                      intensity_minutes_moderate, intensity_minutes_vigorous,
                      avg_spo2, avg_respiration_rate
               FROM daily_stats ORDER BY date DESC LIMIT ?""",
            (days,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_recent_training_metrics(days: int = 30) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT date, vo2_max, training_readiness_score, training_readiness_level,
                      race_5k_seconds, race_10k_seconds, race_half_seconds, race_marathon_seconds
               FROM training_metrics
               WHERE vo2_max IS NOT NULL OR training_readiness_score IS NOT NULL
               ORDER BY date DESC LIMIT ?""",
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
