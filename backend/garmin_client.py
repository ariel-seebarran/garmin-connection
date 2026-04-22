import asyncio
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
