import aiosqlite
import json
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "garmin.db"

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    garmin_email TEXT,
    garmin_password_enc TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 0,
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
    user_id INTEGER NOT NULL DEFAULT 0,
    date TEXT NOT NULL,
    sleep_score INTEGER,
    total_sleep_seconds INTEGER,
    deep_sleep_seconds INTEGER,
    light_sleep_seconds INTEGER,
    rem_sleep_seconds INTEGER,
    awake_seconds INTEGER,
    avg_hrv REAL,
    raw_json TEXT,
    synced_at TEXT,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    user_id INTEGER NOT NULL DEFAULT 0,
    date TEXT NOT NULL,
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
    synced_at TEXT,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS training_metrics (
    user_id INTEGER NOT NULL DEFAULT 0,
    date TEXT NOT NULL,
    vo2_max REAL,
    training_readiness_score INTEGER,
    training_readiness_level TEXT,
    race_5k_seconds INTEGER,
    race_10k_seconds INTEGER,
    race_half_seconds INTEGER,
    race_marathon_seconds INTEGER,
    synced_at TEXT,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    sync_time TEXT,
    activities_synced INTEGER,
    sleep_days_synced INTEGER,
    stats_days_synced INTEGER,
    status TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS strava_tokens (
    user_id INTEGER PRIMARY KEY,
    access_token TEXT,
    refresh_token TEXT,
    expires_at INTEGER,
    athlete_id INTEGER,
    athlete_name TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS training_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    race_goal TEXT NOT NULL,
    race_date TEXT,
    weekly_days INTEGER,
    current_weekly_km REAL,
    total_weeks INTEGER,
    peak_weekly_km REAL,
    created_at TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    garmin_workout_ids TEXT
);
"""


async def _migrate_to_multiuser(db):
    """One-time migration: add user_id to all tables for multi-user support."""
    # --- sleep_data ---
    cursor = await db.execute("PRAGMA table_info(sleep_data)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE sleep_data RENAME TO _sleep_data_bak")
        await db.execute("""CREATE TABLE sleep_data (
            user_id INTEGER NOT NULL DEFAULT 0, date TEXT NOT NULL,
            sleep_score INTEGER, total_sleep_seconds INTEGER, deep_sleep_seconds INTEGER,
            light_sleep_seconds INTEGER, rem_sleep_seconds INTEGER, awake_seconds INTEGER,
            avg_hrv REAL, raw_json TEXT, synced_at TEXT,
            PRIMARY KEY (user_id, date))""")
        await db.execute("""INSERT INTO sleep_data
            (user_id,date,sleep_score,total_sleep_seconds,deep_sleep_seconds,
             light_sleep_seconds,rem_sleep_seconds,awake_seconds,avg_hrv,raw_json,synced_at)
            SELECT 0,date,sleep_score,total_sleep_seconds,deep_sleep_seconds,
             light_sleep_seconds,rem_sleep_seconds,awake_seconds,avg_hrv,raw_json,synced_at
            FROM _sleep_data_bak""")
        await db.execute("DROP TABLE _sleep_data_bak")

    # --- daily_stats ---
    cursor = await db.execute("PRAGMA table_info(daily_stats)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE daily_stats RENAME TO _daily_stats_bak")
        await db.execute("""CREATE TABLE daily_stats (
            user_id INTEGER NOT NULL DEFAULT 0, date TEXT NOT NULL,
            steps INTEGER, resting_heart_rate INTEGER, avg_stress_level INTEGER,
            body_battery_low INTEGER, body_battery_high INTEGER, active_calories INTEGER,
            total_calories INTEGER, floors_climbed INTEGER,
            intensity_minutes_moderate INTEGER, intensity_minutes_vigorous INTEGER,
            avg_spo2 REAL, avg_respiration_rate REAL, raw_json TEXT, synced_at TEXT,
            PRIMARY KEY (user_id, date))""")
        await db.execute("""INSERT INTO daily_stats
            (user_id,date,steps,resting_heart_rate,avg_stress_level,body_battery_low,
             body_battery_high,active_calories,total_calories,floors_climbed,
             intensity_minutes_moderate,intensity_minutes_vigorous,avg_spo2,avg_respiration_rate,
             raw_json,synced_at)
            SELECT 0,date,steps,resting_heart_rate,avg_stress_level,body_battery_low,
             body_battery_high,active_calories,
             CASE WHEN EXISTS(SELECT 1 FROM pragma_table_info('_daily_stats_bak') WHERE name='total_calories')
               THEN total_calories ELSE NULL END,
             CASE WHEN EXISTS(SELECT 1 FROM pragma_table_info('_daily_stats_bak') WHERE name='floors_climbed')
               THEN floors_climbed ELSE NULL END,
             CASE WHEN EXISTS(SELECT 1 FROM pragma_table_info('_daily_stats_bak') WHERE name='intensity_minutes_moderate')
               THEN intensity_minutes_moderate ELSE NULL END,
             CASE WHEN EXISTS(SELECT 1 FROM pragma_table_info('_daily_stats_bak') WHERE name='intensity_minutes_vigorous')
               THEN intensity_minutes_vigorous ELSE NULL END,
             CASE WHEN EXISTS(SELECT 1 FROM pragma_table_info('_daily_stats_bak') WHERE name='avg_spo2')
               THEN avg_spo2 ELSE NULL END,
             CASE WHEN EXISTS(SELECT 1 FROM pragma_table_info('_daily_stats_bak') WHERE name='avg_respiration_rate')
               THEN avg_respiration_rate ELSE NULL END,
             raw_json, synced_at
            FROM _daily_stats_bak""")
        await db.execute("DROP TABLE _daily_stats_bak")

    # --- training_metrics ---
    cursor = await db.execute("PRAGMA table_info(training_metrics)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE training_metrics RENAME TO _training_metrics_bak")
        await db.execute("""CREATE TABLE training_metrics (
            user_id INTEGER NOT NULL DEFAULT 0, date TEXT NOT NULL,
            vo2_max REAL, training_readiness_score INTEGER, training_readiness_level TEXT,
            race_5k_seconds INTEGER, race_10k_seconds INTEGER,
            race_half_seconds INTEGER, race_marathon_seconds INTEGER, synced_at TEXT,
            PRIMARY KEY (user_id, date))""")
        await db.execute("""INSERT INTO training_metrics
            (user_id,date,vo2_max,training_readiness_score,training_readiness_level,
             race_5k_seconds,race_10k_seconds,race_half_seconds,race_marathon_seconds,synced_at)
            SELECT 0,date,vo2_max,training_readiness_score,training_readiness_level,
             race_5k_seconds,race_10k_seconds,race_half_seconds,race_marathon_seconds,synced_at
            FROM _training_metrics_bak""")
        await db.execute("DROP TABLE _training_metrics_bak")

    # --- strava_tokens: singleton → per-user ---
    cursor = await db.execute("PRAGMA table_info(strava_tokens)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE strava_tokens RENAME TO _strava_tokens_bak")
        await db.execute("""CREATE TABLE strava_tokens (
            user_id INTEGER PRIMARY KEY,
            access_token TEXT, refresh_token TEXT, expires_at INTEGER,
            athlete_id INTEGER, athlete_name TEXT, updated_at TEXT)""")
        await db.execute("""INSERT OR IGNORE INTO strava_tokens
            (user_id,access_token,refresh_token,expires_at,athlete_id,athlete_name,updated_at)
            SELECT 0,access_token,refresh_token,expires_at,athlete_id,athlete_name,updated_at
            FROM _strava_tokens_bak WHERE id = 1""")
        await db.execute("DROP TABLE _strava_tokens_bak")

    # --- activities: add user_id column ---
    cursor = await db.execute("PRAGMA table_info(activities)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE activities ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")

    # --- training_plans: add user_id and garmin_workout_ids ---
    cursor = await db.execute("PRAGMA table_info(training_plans)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE training_plans ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    if "garmin_workout_ids" not in cols:
        await db.execute("ALTER TABLE training_plans ADD COLUMN garmin_workout_ids TEXT")

    # --- sync_log: add user_id ---
    cursor = await db.execute("PRAGMA table_info(sync_log)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "user_id" not in cols:
        await db.execute("ALTER TABLE sync_log ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        for statement in CREATE_TABLES.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                await db.execute(stmt)
        await _migrate_to_multiuser(db)
        await db.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def create_user(username: str, hashed_password: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO users (username, hashed_password, created_at) VALUES (?, ?, ?)",
            (username.lower(), hashed_password, now),
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_by_username(username: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username.lower(),))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_id(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_garmin_credentials(user_id: int, email: str, password_enc: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET garmin_email = ?, garmin_password_enc = ? WHERE id = ?",
            (email, password_enc, user_id),
        )
        await db.commit()


async def get_garmin_credentials(user_id: int) -> tuple[str, str] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT garmin_email, garmin_password_enc FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row and row[0] and row[1]:
            return (row[0], row[1])
        return None


async def user_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

async def upsert_activity(activity: dict, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO activities
               (id, user_id, activity_type, name, start_time, duration_seconds, distance_meters,
                avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                calories, aerobic_training_effect, anaerobic_training_effect,
                avg_power, avg_vertical_oscillation, avg_ground_contact_time,
                avg_stride_length, training_stress_score, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(activity.get("activityId", "")),
                user_id,
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


async def upsert_strava_activity(activity: dict, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO activities
               (id, user_id, activity_type, name, start_time, duration_seconds, distance_meters,
                avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                calories, avg_power, training_stress_score, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                activity["id"], user_id, activity["activity_type"], activity["name"],
                activity["start_time"], activity["duration_seconds"], activity["distance_meters"],
                activity["avg_pace_per_km"], activity["avg_heart_rate"], activity["max_heart_rate"],
                activity["elevation_gain"], activity["calories"], activity["avg_power"],
                activity["training_stress_score"], activity["raw_json"], now,
            ),
        )
        await db.commit()


async def get_activity_detail(activity_id: str, user_id: int = 0) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, activity_type, name, start_time, duration_seconds, distance_meters,
                      avg_pace_per_km, avg_heart_rate, max_heart_rate, elevation_gain,
                      calories, aerobic_training_effect, anaerobic_training_effect,
                      avg_power, avg_vertical_oscillation, avg_ground_contact_time,
                      avg_stride_length, training_stress_score, raw_json
               FROM activities WHERE id = ? AND user_id = ?""",
            (activity_id, user_id)
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


async def get_recent_activities(limit: int = 30, user_id: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT a.id, a.activity_type, a.name, a.start_time, a.duration_seconds, a.distance_meters,
                      a.avg_pace_per_km, a.avg_heart_rate, a.max_heart_rate, a.elevation_gain,
                      a.calories, a.aerobic_training_effect, a.anaerobic_training_effect,
                      a.avg_power, a.avg_vertical_oscillation, a.avg_ground_contact_time,
                      a.avg_stride_length, a.training_stress_score
               FROM activities a
               WHERE a.user_id = ?
               AND NOT (
                   a.id LIKE 'strava_%'
                   AND EXISTS (
                       SELECT 1 FROM activities g
                       WHERE g.id NOT LIKE 'strava_%'
                       AND g.user_id = a.user_id
                       AND ABS(
                           CAST(strftime('%s', g.start_time) AS INTEGER) -
                           CAST(strftime('%s', a.start_time) AS INTEGER)
                       ) < 300
                   )
               )
               ORDER BY a.start_time DESC LIMIT ?""",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Sleep
# ---------------------------------------------------------------------------

async def upsert_sleep(date_str: str, data: dict, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    daily = data.get("dailySleepDTO", {})
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO sleep_data
               (user_id, date, sleep_score, total_sleep_seconds, deep_sleep_seconds,
                light_sleep_seconds, rem_sleep_seconds, awake_seconds, avg_hrv, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, date_str,
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


async def get_recent_sleep(days: int = 14, user_id: int = 0) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT date, sleep_score, total_sleep_seconds, deep_sleep_seconds,
                      light_sleep_seconds, rem_sleep_seconds, awake_seconds, avg_hrv
               FROM sleep_data WHERE user_id = ? AND date >= ? ORDER BY date DESC""",
            (user_id, cutoff),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Daily stats
# ---------------------------------------------------------------------------

async def upsert_daily_stats(date_str: str, data: dict, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO daily_stats
               (user_id, date, steps, resting_heart_rate, avg_stress_level,
                body_battery_low, body_battery_high, active_calories,
                total_calories, floors_climbed,
                intensity_minutes_moderate, intensity_minutes_vigorous,
                raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, date_str,
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


async def update_daily_biometrics(date_str: str, user_id: int = 0,
                                   spo2: float | None = None,
                                   respiration: float | None = None):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO daily_stats (user_id, date, synced_at) VALUES (?, ?, ?)",
            (user_id, date_str, now)
        )
        if spo2 is not None:
            await db.execute(
                "UPDATE daily_stats SET avg_spo2 = ? WHERE user_id = ? AND date = ?",
                (spo2, user_id, date_str)
            )
        if respiration is not None:
            await db.execute(
                "UPDATE daily_stats SET avg_respiration_rate = ? WHERE user_id = ? AND date = ?",
                (respiration, user_id, date_str)
            )
        await db.commit()


async def get_recent_daily_stats(days: int = 14, user_id: int = 0) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT date, steps, resting_heart_rate, avg_stress_level,
                      body_battery_low, body_battery_high, active_calories,
                      total_calories, floors_climbed,
                      intensity_minutes_moderate, intensity_minutes_vigorous,
                      avg_spo2, avg_respiration_rate
               FROM daily_stats WHERE user_id = ? AND date >= ? ORDER BY date DESC""",
            (user_id, cutoff),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Training metrics
# ---------------------------------------------------------------------------

async def upsert_training_readiness(date_str: str, data: dict, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    dto = data.get("trainingReadinessDTO") or data
    score = dto.get("score") or dto.get("trainingReadinessScore")
    level = (dto.get("scoreDesc") or dto.get("level") or dto.get("trainingReadinessLevel") or "")
    level = level.upper() if level else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO training_metrics (user_id, date, training_readiness_score, training_readiness_level, synced_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, date) DO UPDATE SET
                   training_readiness_score = excluded.training_readiness_score,
                   training_readiness_level = excluded.training_readiness_level,
                   synced_at = excluded.synced_at""",
            (user_id, date_str, score, level, now),
        )
        await db.commit()


async def upsert_max_metrics(date_str: str, data: dict, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    vo2 = data.get("vo2MaxRunning") or data.get("vo2MaxCycling") or data.get("vo2Max")
    if vo2 is None:
        metrics_map = (data.get("allMetrics") or {}).get("metricsMap") or {}
        def first_val(key):
            items = metrics_map.get(key) or []
            return items[0].get("value") if items else None
        vo2 = first_val("METRIC_VO2_MAX_RUNNING") or first_val("METRIC_VO2_MAX")
    if vo2 is None:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO training_metrics (user_id, date, vo2_max, synced_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, date) DO UPDATE SET
                   vo2_max = excluded.vo2_max, synced_at = excluded.synced_at""",
            (user_id, date_str, vo2, now),
        )
        await db.commit()


async def upsert_training_status_vo2(date_str: str, data: dict, user_id: int = 0):
    vo2 = (data.get("vo2Max") or data.get("vo2MaxRunning") or data.get("vo2MaxValue")
           or data.get("latestVO2Max") or data.get("latestVo2Max"))
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
            """INSERT INTO training_metrics (user_id, date, vo2_max, synced_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, date) DO UPDATE SET
                   vo2_max = excluded.vo2_max, synced_at = excluded.synced_at""",
            (user_id, date_str, float(vo2), now),
        )
        await db.commit()


async def upsert_race_predictions(date_str: str, data, user_id: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    r5k = r10k = rhalf = rmarathon = None
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
            """INSERT INTO training_metrics
               (user_id, date, race_5k_seconds, race_10k_seconds, race_half_seconds, race_marathon_seconds, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, date) DO UPDATE SET
                   race_5k_seconds = excluded.race_5k_seconds,
                   race_10k_seconds = excluded.race_10k_seconds,
                   race_half_seconds = excluded.race_half_seconds,
                   race_marathon_seconds = excluded.race_marathon_seconds,
                   synced_at = excluded.synced_at""",
            (user_id, date_str, r5k, r10k, rhalf, rmarathon, now),
        )
        await db.commit()


async def sync_vo2_from_activities(user_id: int = 0) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT start_time, raw_json FROM activities WHERE user_id = ? AND raw_json IS NOT NULL ORDER BY start_time DESC",
            (user_id,)
        )
        rows = await cur.fetchall()
        updated = 0
        seen_dates: set[str] = set()
        for row in rows:
            d = (row["start_time"] or "")[:10]
            if not d or d in seen_dates:
                continue
            try:
                vo2 = json.loads(row["raw_json"]).get("vO2MaxValue")
                if vo2 and float(vo2) > 0:
                    await db.execute(
                        """INSERT INTO training_metrics (user_id, date, vo2_max, synced_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(user_id, date) DO UPDATE SET
                               vo2_max = excluded.vo2_max, synced_at = excluded.synced_at""",
                        (user_id, d, float(vo2), now),
                    )
                    seen_dates.add(d)
                    updated += 1
            except Exception:
                pass
        await db.commit()
        return updated


async def get_recent_training_metrics(days: int = 30, user_id: int = 0) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT date, vo2_max, training_readiness_score, training_readiness_level,
                      race_5k_seconds, race_10k_seconds, race_half_seconds, race_marathon_seconds
               FROM training_metrics
               WHERE user_id = ?
                 AND (vo2_max IS NOT NULL OR training_readiness_score IS NOT NULL)
                 AND date >= ?
               ORDER BY date DESC""",
            (user_id, cutoff),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_performance_data(user_id: int = 0) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT date, vo2_max FROM training_metrics WHERE user_id = ? AND vo2_max IS NOT NULL ORDER BY date DESC LIMIT 1",
            (user_id,)
        )
        vo2_row = await cur.fetchone()
        cur = await db.execute(
            """SELECT date, race_5k_seconds, race_10k_seconds, race_half_seconds, race_marathon_seconds
               FROM training_metrics
               WHERE user_id = ?
                 AND (race_5k_seconds IS NOT NULL OR race_marathon_seconds IS NOT NULL)
               ORDER BY date DESC LIMIT 1""",
            (user_id,)
        )
        race_row = await cur.fetchone()
        cur = await db.execute(
            """SELECT date, training_readiness_score, training_readiness_level
               FROM training_metrics
               WHERE user_id = ? AND training_readiness_score IS NOT NULL
               ORDER BY date DESC LIMIT 30""",
            (user_id,)
        )
        readiness_rows = await cur.fetchall()
        return {
            "vo2_max": dict(vo2_row) if vo2_row else None,
            "race_predictions": dict(race_row) if race_row else None,
            "readiness_history": [dict(r) for r in readiness_rows],
        }


# ---------------------------------------------------------------------------
# Strava tokens
# ---------------------------------------------------------------------------

async def save_strava_tokens(
    access_token: str, refresh_token: str, expires_at: int,
    athlete_id: int | None = None, athlete_name: str | None = None,
    user_id: int = 0,
):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO strava_tokens
               (user_id, access_token, refresh_token, expires_at, athlete_id, athlete_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, access_token, refresh_token, expires_at, athlete_id, athlete_name, now),
        )
        await db.commit()


async def get_strava_tokens(user_id: int = 0) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM strava_tokens WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Sync log
# ---------------------------------------------------------------------------

async def log_sync(activities: int, sleep_days: int, stats_days: int,
                   status: str, error: str = None, user_id: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO sync_log
               (user_id, sync_time, activities_synced, sleep_days_synced, stats_days_synced, status, error)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, datetime.now(timezone.utc).isoformat(), activities, sleep_days, stats_days, status, error),
        )
        await db.commit()


async def get_last_sync(user_id: int = 0) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sync_log WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Training plans
# ---------------------------------------------------------------------------

async def create_training_plan(
    name: str, race_goal: str, race_date: str | None,
    weekly_days: int, current_weekly_km: float, plan: dict, user_id: int = 0,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO training_plans
               (user_id, name, race_goal, race_date, weekly_days, current_weekly_km,
                total_weeks, peak_weekly_km, created_at, plan_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, name, race_goal, race_date, weekly_days, current_weekly_km,
                plan.get("total_weeks"), plan.get("peak_weekly_km"),
                now, json.dumps(plan),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_training_plans(user_id: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, name, race_goal, race_date, weekly_days, current_weekly_km,
                      total_weeks, peak_weekly_km, created_at
               FROM training_plans WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_training_plan(plan_id: int, user_id: int = 0) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM training_plans WHERE id = ? AND user_id = ?", (plan_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["plan"] = json.loads(result.pop("plan_json") or "{}")
        except Exception:
            result["plan"] = {}
        return result


async def delete_training_plan(plan_id: int, user_id: int = 0) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM training_plans WHERE id = ? AND user_id = ?", (plan_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def store_garmin_workout_ids(plan_id: int, workout_ids: list[int], user_id: int = 0) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE training_plans SET garmin_workout_ids = ? WHERE id = ? AND user_id = ?",
            (json.dumps(workout_ids), plan_id, user_id),
        )
        await db.commit()


async def get_garmin_workout_ids(plan_id: int, user_id: int = 0) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT garmin_workout_ids FROM training_plans WHERE id = ? AND user_id = ?",
            (plan_id, user_id)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pace_from_activity(activity: dict) -> float | None:
    distance = activity.get("distance", 0)
    duration = activity.get("duration", 0)
    if distance and duration and distance > 0:
        return (duration / 60) / (distance / 1000)
    return None
