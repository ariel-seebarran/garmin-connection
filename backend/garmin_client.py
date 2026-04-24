import asyncio
import os
import re
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from garminconnect import Garmin, GarminConnectAuthenticationError
import database
from logging_config import get_logger

log = get_logger("garmin_client")

RUNNING_TYPES = {
    "running", "trail_running", "treadmill_running", "track_running",
    "ultra_run", "obstacle_run", "virtual_run",
}
ACTIVITIES_PAGE_SIZE = 100

TOKENSTORE = Path(__file__).parent / ".garmin_tokens"


class GarminSyncError(Exception):
    pass


class MFARequired(Exception):
    pass


def _build_client(email: str, password: str, mfa_code: str | None = None) -> Garmin:
    mfa_used = False

    def prompt_mfa():
        nonlocal mfa_used
        if mfa_code:
            mfa_used = True
            return mfa_code
        raise MFARequired("MFA required — enter your 6-digit code and try again.")

    client = Garmin(email, password, is_cn=False, prompt_mfa=prompt_mfa)
    try:
        client.login(tokenstore=str(TOKENSTORE))
    except MFARequired:
        raise
    return client


async def sync_all(email: str, password: str, days: int = 30, mfa_code: str | None = None) -> dict:
    log.info("Logging in to Garmin Connect (sync_all, days=%d)", days)
    loop = asyncio.get_event_loop()
    try:
        client = await loop.run_in_executor(None, partial(_build_client, email, password, mfa_code))
    except MFARequired:
        raise GarminSyncError("MFA_REQUIRED")
    except GarminConnectAuthenticationError as e:
        raise GarminSyncError(f"Authentication failed: {e}")
    except Exception as e:
        if "MFA" in str(e).upper():
            raise GarminSyncError("MFA_REQUIRED")
        raise GarminSyncError(f"Login error: {e}")

    results = {"activities": 0, "sleep_days": 0, "stats_days": 0, "training_metric_days": 0, "errors": []}
    await _sync_activities(client, loop, days, results)
    await _sync_daily_data(client, loop, days, results)
    return results


async def sync_full_history(email: str, password: str, mfa_code: str | None = None) -> dict:
    """Sync all activities ever recorded — paginated for large histories."""
    log.info("Logging in to Garmin Connect (full history sync)")
    loop = asyncio.get_event_loop()
    try:
        client = await loop.run_in_executor(None, partial(_build_client, email, password, mfa_code))
    except MFARequired:
        raise GarminSyncError("MFA_REQUIRED")
    except GarminConnectAuthenticationError as e:
        raise GarminSyncError(f"Authentication failed: {e}")
    except Exception as e:
        if "MFA" in str(e).upper():
            raise GarminSyncError("MFA_REQUIRED")
        raise GarminSyncError(f"Login error: {e}")

    results = {"activities": 0, "sleep_days": 0, "stats_days": 0, "training_metric_days": 0, "errors": [], "pages": 0}
    start = 0
    while True:
        try:
            page = await loop.run_in_executor(
                None, partial(client.get_activities, start, ACTIVITIES_PAGE_SIZE)
            )
        except Exception as e:
            results["errors"].append(f"Page {start}: {e}")
            break

        if not page:
            break

        dicts: list[dict] = [a for a in page if isinstance(a, dict)]
        runs = [a for a in dicts if a.get("activityType", {}).get("typeKey", "") in RUNNING_TYPES]
        if not runs:
            runs = dicts

        for activity in runs:
            await database.upsert_activity(activity)
            results["activities"] += 1

        log.debug("Page %d: fetched %d activities (%d kept)", results["pages"], len(page), len(runs))
        results["pages"] += 1
        if len(page) < ACTIVITIES_PAGE_SIZE:
            break
        start += ACTIVITIES_PAGE_SIZE

    log.info("Activity pages fetched: %d, total activities stored: %d", results["pages"], results["activities"])
    # For full history, sync 90 days of daily data but only 30 days of biometrics
    await _sync_daily_data(client, loop, 90, results, biometric_days=30)
    return results


async def _sync_activities(client: Garmin, loop, days: int, results: dict):
    try:
        raw: list[dict] = await loop.run_in_executor(None, partial(client.get_activities, 0, min(days * 2, 200)))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        recent = [a for a in raw if isinstance(a, dict) and (a.get("startTimeLocal") or "") >= cutoff]
        runs = [a for a in recent if a.get("activityType", {}).get("typeKey", "") in RUNNING_TYPES]
        if not runs:
            runs = recent

        for activity in runs:
            await database.upsert_activity(activity)
            results["activities"] += 1
    except Exception as e:
        results["errors"].append(f"Activities sync failed: {e}")


async def _sync_daily_data(client: Garmin, loop, days: int, results: dict, biometric_days: int = 30):
    """Sync sleep, daily stats, biometrics, and performance metrics for each day."""
    today = date.today()

    # VO2 max and race predictions change slowly — fetch once for today
    await _sync_vo2_and_races(client, loop, today.isoformat(), results)

    for i in range(days):
        day = today - timedelta(days=i)
        day_str = day.isoformat()

        try:
            sleep = await loop.run_in_executor(None, partial(client.get_sleep_data, day_str))
            if sleep and sleep.get("dailySleepDTO"):
                await database.upsert_sleep(day_str, sleep)
                results["sleep_days"] += 1
        except Exception as e:
            results["errors"].append(f"Sleep {day_str}: {e}")

        try:
            stats = await loop.run_in_executor(None, partial(client.get_stats, day_str))
            if stats:
                await database.upsert_daily_stats(day_str, stats)
                results["stats_days"] += 1
        except Exception as e:
            results["errors"].append(f"Stats {day_str}: {e}")

        # Biometrics and training readiness only for recent days (limits API calls)
        if i < biometric_days:
            try:
                spo2_data = await loop.run_in_executor(None, partial(client.get_spo2_data, day_str))
                if spo2_data:
                    avg_spo2 = (spo2_data.get("averageSPO2") or spo2_data.get("avgSpo2")
                                or spo2_data.get("averageSpo2"))
                    if avg_spo2:
                        await database.update_daily_biometrics(day_str, spo2=float(avg_spo2))
            except Exception as e:
                results["errors"].append(f"SpO2 {day_str}: {e}")

            try:
                resp_data = await loop.run_in_executor(None, partial(client.get_respiration_data, day_str))
                if resp_data:
                    avg_resp = (resp_data.get("avgWakingRespirationValue")
                                or resp_data.get("averageWakingRespirationValue")
                                or resp_data.get("avgRespirationValue"))
                    if avg_resp:
                        await database.update_daily_biometrics(day_str, respiration=float(avg_resp))
            except Exception as e:
                results["errors"].append(f"Respiration {day_str}: {e}")

            try:
                tr_data = await loop.run_in_executor(None, partial(client.get_training_readiness, day_str))
                if isinstance(tr_data, list):
                    tr_data = tr_data[0] if tr_data else None
                if tr_data:
                    await database.upsert_training_readiness(day_str, tr_data)
                    results["training_metric_days"] += 1
            except Exception as e:
                results["errors"].append(f"Training readiness {day_str}: {e}")


_DAY_OFFSETS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Maps run effort level to HR zone number
_EFFORT_ZONE: dict[str, int] = {
    "easy": 2, "recovery": 2, "moderate": 3,
    "tempo": 4, "hard": 4, "race": 5,
}

# HRR zone percentages for fallback (matches Garmin's default 5-zone HRR model)
_ZONE_HRR_PCTS: dict[int, tuple[float, float]] = {
    1: (0.50, 0.60), 2: (0.60, 0.70), 3: (0.70, 0.80),
    4: (0.80, 0.90), 5: (0.90, 1.00),
}


# ---------------------------------------------------------------------------
# Regex patterns for parsing coaching notes
# ---------------------------------------------------------------------------

_HR_RE    = re.compile(r'HR\s*(\d{2,3})\s*[-–]\s*(\d{2,3})\s*bpm', re.I)
_PACE_RE  = re.compile(r'(\d+):(\d+)\s*[-–]\s*(\d+):(\d+)\s*/km', re.I)
# e.g. "6 × 400m", "5x1km", "4 X 800m"
_INTERVAL_RE  = re.compile(r'(\d+)\s*[×xX]\s*(\d+(?:\.\d+)?)\s*(km|m)\b', re.I)
_RECOVERY_RE  = re.compile(r'(\d+)\s*(min|sec)\w*\s*(?:recovery|rest|jog)', re.I)


def _no_target() -> dict:
    return {"targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "targetValueOne": None, "targetValueTwo": None}


def _hr_target(lo: int, hi: int) -> dict:
    return {"targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            "targetValueOne": lo, "targetValueTwo": hi}


def _pace_target(slow_secs: int, fast_secs: int) -> dict:
    """slow_secs/fast_secs are pace in seconds/km. Garmin pace.zone uses sec/km directly."""
    return {"targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
            "targetValueOne": fast_secs,    # faster bound (lower sec/km)
            "targetValueTwo": slow_secs}    # slower bound (higher sec/km)


def _parse_zone_list(raw: list) -> dict[int, tuple[int, int]]:
    zones: dict[int, tuple[int, int]] = {}
    for z in raw:
        num = z.get("zoneNumber") or z.get("zone")
        lo  = z.get("minHeartRate") if z.get("minHeartRate") is not None else z.get("lowBpm") or z.get("minValue")
        hi  = z.get("maxHeartRate") if z.get("maxHeartRate") is not None else z.get("highBpm") or z.get("maxValue")
        if num and lo is not None and hi is not None:
            zones[int(num)] = (int(lo), int(hi))
    return zones


async def _get_hr_zones(client: Garmin, loop) -> dict[int, tuple[int, int]]:
    """Return {zone_number: (min_bpm, max_bpm)} from Garmin API or computed from max HR."""

    # Try 1: daily heart rate data — includes zone definitions for the account
    today_str = date.today().isoformat()
    try:
        hr_data = await loop.run_in_executor(
            None, partial(client.get_heart_rate_data, today_str)
        )
        raw = []
        if isinstance(hr_data, dict):
            raw = hr_data.get("heartRateZones") or []
        elif isinstance(hr_data, list):
            raw = hr_data
        zones = _parse_zone_list(raw)
        if zones:
            log.info("Fetched %d HR zones from daily heart rate data", len(zones))
            return zones
    except Exception as e:
        log.warning("Could not fetch HR zones from heart rate data: %s", e)

    # Try 2: biometric service endpoint (works on some account types)
    try:
        data = await loop.run_in_executor(
            None, partial(client.connectapi,
                          client.garmin_connect_biometric_url + "/heartRateZones")
        )
        raw = data if isinstance(data, list) else (
            data.get("heartRateZones") or data.get("zones") or []
            if isinstance(data, dict) else []
        )
        zones = _parse_zone_list(raw)
        if zones:
            log.info("Fetched %d HR zones from biometric API", len(zones))
            return zones
    except Exception as e:
        log.warning("Could not fetch HR zones from biometric API: %s", e)

    # Fallback: compute HRR-based zones from user profile (max HR + resting HR)
    max_hr = 180
    resting_hr = 60
    try:
        settings = await loop.run_in_executor(None, client.get_userprofile_settings)
        log.debug("User profile settings keys: %s", list(settings.keys()) if isinstance(settings, dict) else type(settings))
        if isinstance(settings, dict):
            max_hr = int(
                settings.get("maxHrBpm") or settings.get("maxHeartRate") or
                settings.get("maxHr") or settings.get("maxHeartRateBpm") or 180
            )
            resting_hr = int(
                settings.get("restingHrBpm") or settings.get("restingHeartRate") or
                settings.get("restingHr") or settings.get("restingHeartRateBpm") or 60
            )
            log.info("Computing HRR zones: max HR=%d, resting HR=%d", max_hr, resting_hr)
    except Exception as e:
        log.warning("Could not fetch user profile settings: %s — defaulting max HR=%d, resting HR=%d",
                    e, max_hr, resting_hr)

    hrr = max_hr - resting_hr
    return {z: (int(resting_hr + lo * hrr), int(resting_hr + hi * hrr))
            for z, (lo, hi) in _ZONE_HRR_PCTS.items()}


def _pace_str_to_secs(pace_str: str) -> int:
    """Convert 'M:SS' or 'MM:SS' pace string to seconds/km."""
    parts = pace_str.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _pick_target(notes: str, run_type: str,
                 effort: str = "", hr_zones: dict | None = None,
                 hr_zone: int | None = None,
                 pace_slow: str | None = None, pace_fast: str | None = None) -> dict:
    """Choose the best step target. HR-based plans supply hr_zone; pace-based supply pace_slow/fast."""

    # Explicit pace from pace-based plan
    if pace_slow and pace_fast:
        return _pace_target(_pace_str_to_secs(pace_slow), _pace_str_to_secs(pace_fast))

    # Explicit HR zone from HR-based plan
    if hr_zone and hr_zones:
        zone = hr_zones.get(hr_zone)
        if zone:
            return _hr_target(zone[0], zone[1])

    # User HR zones + effort level → zone lookup
    if hr_zones and effort:
        zone_num = _EFFORT_ZONE.get(effort.lower(), 2)
        zone = hr_zones.get(zone_num)
        if zone:
            pace_m = _PACE_RE.search(notes)
            if pace_m and effort.lower() in ("tempo", "hard", "race"):
                a = int(pace_m.group(1)) * 60 + int(pace_m.group(2))
                b = int(pace_m.group(3)) * 60 + int(pace_m.group(4))
                return _pace_target(max(a, b), min(a, b))
            return _hr_target(zone[0], zone[1])

    # Fallback: parse HR / pace directly from AI-generated notes
    hr_m = _HR_RE.search(notes)
    pace_m = _PACE_RE.search(notes)
    rtype = run_type.lower()
    if hr_m and any(k in rtype for k in ("easy", "long", "recovery")):
        return _hr_target(int(hr_m.group(1)), int(hr_m.group(2)))
    if pace_m:
        a = int(pace_m.group(1)) * 60 + int(pace_m.group(2))
        b = int(pace_m.group(3)) * 60 + int(pace_m.group(4))
        return _pace_target(max(a, b), min(a, b))
    if hr_m:
        return _hr_target(int(hr_m.group(1)), int(hr_m.group(2)))
    return _no_target()


def _exe_step(order: int, type_id: int, type_key: str,
              cond_id: int, cond_key: str, value: float,
              target: dict | None = None, child_id: int | None = None) -> dict:
    t = target or _no_target()
    s = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key},
        "endCondition": {"conditionTypeId": cond_id, "conditionTypeKey": cond_key},
        "endConditionValue": value,
        "targetType": t["targetType"],
        "targetValueOne": t.get("targetValueOne"),
        "targetValueTwo": t.get("targetValueTwo"),
    }
    if child_id is not None:
        s["childStepId"] = child_id
    return s


def _repeat_group(order: int, reps: int, steps: list) -> dict:
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
        "numberOfIterations": reps,
        "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
        "endConditionValue": reps,
        "smartRepeat": False,
        "workoutSteps": steps,
    }


def _build_interval_steps(notes: str, hr_zones: dict | None = None) -> list | None:
    """Parse '6 × 400m at 5K pace, 90sec recovery' into Garmin structured steps."""
    im = _INTERVAL_RE.search(notes)
    if not im:
        return None

    reps = int(im.group(1))
    dist_m = float(im.group(2)) * (1000 if im.group(3).lower() == "km" else 1)

    rm = _RECOVERY_RE.search(notes)
    if rm:
        val = int(rm.group(1))
        recovery_secs = val * 60 if "min" in rm.group(2).lower() else val
    else:
        recovery_secs = 90

    pace_m = _PACE_RE.search(notes)
    interval_target = _no_target()
    if pace_m:
        a = int(pace_m.group(1)) * 60 + int(pace_m.group(2))
        b = int(pace_m.group(3)) * 60 + int(pace_m.group(4))
        interval_target = _pace_target(max(a, b), min(a, b))

    # Warmup / cooldown use Zone 2 from user HR zones if available
    z2 = hr_zones.get(2) if hr_zones else None
    warmup_target  = _hr_target(z2[0], z2[1]) if z2 else _no_target()
    cooldown_target = warmup_target

    return [
        _exe_step(1, 1, "warmup",   2, "time", 600, warmup_target),
        _repeat_group(2, reps, [
            _exe_step(1, 3, "interval", 3, "distance", dist_m, interval_target, child_id=1),
            _exe_step(2, 4, "recovery", 2, "time", recovery_secs, child_id=2),
        ]),
        _exe_step(3, 2, "cooldown", 2, "time", 300, cooldown_target),
    ]


def _run_to_workout(run: dict, week_num: int, hr_zones: dict | None = None) -> dict | None:
    """Convert a plan run entry to a structured Garmin workout. Returns None for rest days."""
    run_type   = run.get("type", "Run")
    effort     = run.get("effort", "") or ""
    notes      = run.get("notes", "") or ""
    dist_km    = run.get("distance_km") or 0
    duration_m = run.get("duration_min") or 0
    hr_zone    = run.get("hr_zone")
    pace_slow  = run.get("pace_slow")
    pace_fast  = run.get("pace_fast")

    if dist_km <= 0 and duration_m <= 0:
        return None

    # Interval workouts always use distance-based reps
    if "interval" in run_type.lower():
        steps = _build_interval_steps(notes, hr_zones)
        if steps:
            label = f"W{week_num} · {run_type}"
            return _workout_json(label, notes, steps)

    target = _pick_target(notes, run_type, effort, hr_zones,
                          hr_zone=hr_zone, pace_slow=pace_slow, pace_fast=pace_fast)

    # HR-based plan: time end condition
    if duration_m > 0 and dist_km <= 0:
        name  = f"W{week_num} · {run_type} · {duration_m:.0f}min"
        steps = [_exe_step(1, 3, "interval", 2, "time", duration_m * 60, target)]
        return _workout_json(name, notes, steps)

    # Pace-based plan (or legacy): distance end condition
    name  = f"W{week_num} · {run_type} · {dist_km:.0f}km"
    steps = [_exe_step(1, 3, "interval", 3, "distance", dist_km * 1000, target)]
    return _workout_json(name, notes, steps)


def _workout_json(name: str, description: str, steps: list) -> dict:
    return {
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutName": name,
        "description": description,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": steps,
        }],
    }


async def push_plan_to_garmin(plan: dict) -> dict:
    """Upload every running workout in a training plan to the Garmin Connect calendar.

    Requires a valid cached session from a previous sync (TOKENSTORE).
    Week 1 is anchored to the nearest Monday from today.
    """
    if not TOKENSTORE.exists():
        raise GarminSyncError(
            "No saved Garmin session — sync Garmin first to create one."
        )

    loop = asyncio.get_event_loop()
    email = os.environ.get("GARMIN_EMAIL", "")
    password = os.environ.get("GARMIN_PASSWORD", "")
    try:
        client = Garmin(email, password)
        await loop.run_in_executor(None, partial(client.login, tokenstore=str(TOKENSTORE)))
    except Exception as e:
        raise GarminSyncError(
            f"Garmin session expired — re-sync Garmin to refresh it. ({e})"
        )

    today = date.today()
    days_to_monday = (7 - today.weekday()) % 7
    week1_start = today + timedelta(days=days_to_monday)

    hr_zones = await _get_hr_zones(client, loop)
    log.info("Using HR zones: %s", hr_zones)

    scheduled, errors, workout_ids = 0, [], []

    for week in plan.get("weeks", []):
        week_num = week.get("week_number", 1)
        week_start = week1_start + timedelta(weeks=week_num - 1)

        for run in week.get("runs", []):
            workout_json = _run_to_workout(run, week_num, hr_zones)
            if not workout_json:
                continue

            day_offset = _DAY_OFFSETS.get((run.get("day") or "").lower(), 0)
            workout_date = (week_start + timedelta(days=day_offset)).isoformat()

            try:
                result = await loop.run_in_executor(
                    None, partial(client.upload_workout, workout_json)
                )
                workout_id = result.get("workoutId")
                if workout_id:
                    await loop.run_in_executor(
                        None, partial(client.schedule_workout, workout_id, workout_date)
                    )
                    workout_ids.append(workout_id)
                    scheduled += 1
                    log.debug("Scheduled '%s' on %s", workout_json["workoutName"], workout_date)
                else:
                    errors.append(f"{workout_date}: upload_workout returned no workoutId")
            except Exception as e:
                errors.append(f"{workout_date} {workout_json['workoutName']}: {e}")
                log.warning("Failed to schedule workout on %s: %s", workout_date, e)

    log.info("Pushed %d workouts to Garmin calendar, %d errors", scheduled, len(errors))
    return {"scheduled": scheduled, "errors": errors, "workout_ids": workout_ids}


async def delete_plan_from_garmin(workout_ids: list[int]) -> dict:
    """Delete previously pushed workouts from Garmin Connect (removes from calendar too)."""
    if not workout_ids:
        return {"deleted": 0, "errors": []}
    if not TOKENSTORE.exists():
        raise GarminSyncError(
            "No saved Garmin session — sync Garmin first to create one."
        )

    loop = asyncio.get_event_loop()
    email = os.environ.get("GARMIN_EMAIL", "")
    password = os.environ.get("GARMIN_PASSWORD", "")
    try:
        client = Garmin(email, password)
        await loop.run_in_executor(None, partial(client.login, tokenstore=str(TOKENSTORE)))
    except Exception as e:
        raise GarminSyncError(
            f"Garmin session expired — re-sync Garmin to refresh it. ({e})"
        )

    deleted, errors = 0, []
    for wid in workout_ids:
        try:
            await loop.run_in_executor(None, partial(client.delete_workout, wid))
            deleted += 1
            log.debug("Deleted Garmin workout %s", wid)
        except Exception as e:
            errors.append(f"workout {wid}: {e}")
            log.warning("Failed to delete Garmin workout %s: %s", wid, e)

    log.info("Deleted %d/%d Garmin workouts, %d errors", deleted, len(workout_ids), len(errors))
    return {"deleted": deleted, "errors": errors}


async def _sync_vo2_and_races(client: Garmin, loop, date_str: str, results: dict):
    # Race predictions
    try:
        preds = await loop.run_in_executor(None, client.get_race_predictions)
        if preds:
            await database.upsert_race_predictions(date_str, preds)
    except Exception as e:
        results["errors"].append(f"Race predictions: {e}")

    # VO2 max — extracted from activity records (vO2MaxValue field)
    vo2_count = await database.sync_vo2_from_activities()
    if vo2_count:
        log.info("VO2 max populated from %d activity dates", vo2_count)
        results["vo2_dates"] = vo2_count
