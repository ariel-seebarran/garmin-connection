import json
import time
from datetime import datetime, timezone, timedelta

import httpx

import database
from logging_config import get_logger

log = get_logger("strava_client")

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"

SPORT_TYPE_MAP = {
    "Run": "running",
    "TrailRun": "trail_running",
    "Treadmill": "treadmill_running",
    "VirtualRun": "virtual_run",
    "Race": "running",
    "LongRun": "running",
    "Workout": "running",
}
RUNNING_SPORT_TYPES = set(SPORT_TYPE_MAP)


def get_auth_url(client_id: str, redirect_uri: str) -> str:
    return (
        f"{AUTH_URL}?client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&approval_prompt=auto"
        f"&scope=activity:read_all"
    )


async def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    async with httpx.AsyncClient() as http:
        r = await http.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        })
        r.raise_for_status()
        return r.json()


async def _get_valid_token(client_id: str, client_secret: str, user_id: int = 0) -> str:
    tokens = await database.get_strava_tokens(user_id)
    if not tokens:
        raise ValueError("Strava not connected")

    if time.time() > tokens["expires_at"] - 60:
        log.info("Strava token expired, refreshing for athlete_id=%s", tokens.get("athlete_id"))
        async with httpx.AsyncClient() as http:
            r = await http.post(TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            })
            r.raise_for_status()
            data = r.json()
        await database.save_strava_tokens(
            data["access_token"], data["refresh_token"], data["expires_at"],
            tokens.get("athlete_id"), tokens.get("athlete_name"), user_id,
        )
        log.info("Strava token refreshed successfully")
        return data["access_token"]

    return tokens["access_token"]


async def sync_activities(client_id: str, client_secret: str, days: int = 30, user_id: int = 0) -> dict:
    log.info("Starting Strava sync: last %d days", days)
    token = await _get_valid_token(client_id, client_secret, user_id)
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    results = {"activities": 0, "errors": []}
    page = 1

    async with httpx.AsyncClient() as http:
        while True:
            try:
                r = await http.get(
                    f"{API_BASE}/athlete/activities",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"after": after, "per_page": 100, "page": page},
                    timeout=30.0,
                )
                r.raise_for_status()
                activities = r.json()
            except Exception as e:
                results["errors"].append(f"Page {page}: {e}")
                break

            if not activities:
                break
            log.debug("Strava sync page %d: %d activities fetched", page, len(activities))

            for act in activities:
                sport_type = act.get("sport_type") or act.get("type", "")
                if sport_type in RUNNING_SPORT_TYPES:
                    try:
                        await database.upsert_strava_activity(_map_activity(act), user_id)
                        results["activities"] += 1
                    except Exception as e:
                        results["errors"].append(f"Activity {act.get('id')}: {e}")

            if len(activities) < 100:
                break
            page += 1

    log.info("Strava sync: %d activities, %d errors", results["activities"], len(results["errors"]))
    return results


def _map_activity(act: dict) -> dict:
    distance = float(act.get("distance") or 0)
    duration = float(act.get("moving_time") or 0)
    pace = (duration / 60) / (distance / 1000) if distance > 0 and duration > 0 else None
    sport_type = act.get("sport_type") or act.get("type", "Run")

    return {
        "id": f"strava_{act['id']}",
        "activity_type": SPORT_TYPE_MAP.get(sport_type, "running"),
        "name": act.get("name") or "Strava Run",
        "start_time": (act.get("start_date_local") or "").rstrip("Z"),
        "duration_seconds": duration,
        "distance_meters": distance,
        "avg_pace_per_km": pace,
        "avg_heart_rate": act.get("average_heartrate"),
        "max_heart_rate": act.get("max_heartrate"),
        "elevation_gain": act.get("total_elevation_gain"),
        "calories": act.get("calories"),
        "avg_power": act.get("average_watts"),
        "training_stress_score": None,
        "raw_json": json.dumps(act),
    }
