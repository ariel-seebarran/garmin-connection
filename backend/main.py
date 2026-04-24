import os
import sys
from pathlib import Path

# Ensure backend/ is on sys.path regardless of how the server is invoked
sys.path.insert(0, str(Path(__file__).parent))

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import garmin_client
import strava_client
import vector_store
import agent
import plan_builder
from logging_config import setup_logging, get_logger

load_dotenv(Path(__file__).parent.parent / ".env")
setup_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Garmin AI Coach")
    await database.init_db()
    log.info("Database initialised")
    yield
    log.info("Shutting down")


app = FastAPI(title="Garmin AI Coach", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SyncRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    days: int = 30
    full_history: bool = False
    mfa_code: str | None = None


class ChatRequest(BaseModel):
    messages: list[dict]
    goal: str | None = None


class PlanRequest(BaseModel):
    race_goal: str
    race_date: str | None = None
    days_per_week: int = 5
    current_weekly_km: float = 30.0
    long_run_day: str = "Sunday"
    plan_type: str = "pace"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/sync")
async def sync_garmin(req: SyncRequest):
    email = req.email or os.getenv("GARMIN_EMAIL")
    password = req.password or os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        raise HTTPException(
            400,
            "Garmin credentials required. Set GARMIN_EMAIL/GARMIN_PASSWORD in .env or pass in request.",
        )

    mode = "full history" if req.full_history else f"last {req.days} days"
    log.info("Sync started: %s (mfa=%s)", mode, bool(req.mfa_code))

    try:
        if req.full_history:
            results = await garmin_client.sync_full_history(email, password, req.mfa_code)
        else:
            results = await garmin_client.sync_all(email, password, req.days, req.mfa_code)
    except garmin_client.GarminSyncError as e:
        log.warning("Sync failed: %s", e)
        code = 449 if str(e) == "MFA_REQUIRED" else 401
        raise HTTPException(code, str(e))

    log.info("Sync complete: %d activities, %d sleep days, %d stat days, %d errors",
             results["activities"], results["sleep_days"], results["stats_days"],
             len(results.get("errors", [])))

    # Re-index vector store after sync
    indexed = await vector_store.index_all_activities()
    log.info("Vector store indexed: %d activities", indexed)

    await database.log_sync(
        results["activities"],
        results["sleep_days"],
        results["stats_days"],
        "success" if not results["errors"] else "partial",
        "; ".join(results["errors"]) if results["errors"] else None,
    )

    return {
        **results,
        "indexed_in_vector_store": indexed,
        "errors": results.get("errors", []),
    }


@app.post("/api/index")
async def reindex():
    """Re-embed all activities into ChromaDB without re-syncing from Garmin."""
    indexed = await vector_store.index_all_activities()
    return {"indexed": indexed}


@app.post("/api/backfill-vo2")
async def backfill_vo2():
    """Populate VO2 max in training_metrics from vO2MaxValue stored in activity records."""
    count = await database.sync_vo2_from_activities()
    return {"vo2_dates_populated": count}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not os.getenv("GOOGLE_API_KEY"):
        raise HTTPException(500, "GOOGLE_API_KEY not set in .env")

    n = len(req.messages)
    log.info("Chat request: %d message(s), goal=%r", n, req.goal)

    async def event_stream():
        async for chunk in agent.stream_chat(req.messages, req.goal):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/stats")
async def get_stats():
    activities = await database.get_recent_activities(30)
    sleep = await database.get_recent_sleep(7)
    daily = await database.get_recent_daily_stats(7)
    last_sync = await database.get_last_sync()
    indexed = await vector_store.get_indexed_count()
    training_metrics = await database.get_recent_training_metrics(days=30)

    total_distance = sum(a["distance_meters"] or 0 for a in activities) / 1000
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    weekly_distance = sum(
        (a["distance_meters"] or 0) for a in activities
        if (a["start_time"] or "") >= cutoff
    ) / 1000

    scores = [s["sleep_score"] for s in sleep if s["sleep_score"]]
    avg_sleep = round(sum(scores) / len(scores)) if scores else None

    hrvs = [s["avg_hrv"] for s in sleep if s["avg_hrv"]]
    avg_hrv = round(sum(hrvs) / len(hrvs), 1) if hrvs else None

    rhrs = [d["resting_heart_rate"] for d in daily if d["resting_heart_rate"]]
    rhr = round(sum(rhrs) / len(rhrs)) if rhrs else None

    latest = activities[0] if activities else None
    pace_str = None
    if latest and latest["avg_pace_per_km"]:
        p = latest["avg_pace_per_km"]
        pace_str = f"{int(p)}:{int((p % 1) * 60):02d}/km"

    latest_tr = next((m for m in training_metrics if m.get("training_readiness_score")), None)
    latest_vo2 = next((m for m in training_metrics if m.get("vo2_max")), None)

    # Steps: use today's value if available, otherwise fall back to most recent day with data
    steps_today = None
    steps_date = None
    today_str = date.today().isoformat()
    for d in daily:
        if d.get("steps") is not None:
            steps_today = d["steps"]
            steps_date = d["date"] if d["date"] != today_str else None
            break

    intensity_mod = sum(d.get("intensity_minutes_moderate") or 0 for d in daily)
    intensity_vig = sum(d.get("intensity_minutes_vigorous") or 0 for d in daily)
    intensity_total = intensity_mod + intensity_vig if (intensity_mod + intensity_vig) > 0 else None

    return {
        "total_distance_30d": round(total_distance, 1),
        "weekly_distance": round(weekly_distance, 1),
        "run_count_30d": len(activities),
        "avg_sleep_score_7d": avg_sleep,
        "avg_hrv_7d": avg_hrv,
        "avg_resting_hr_7d": rhr,
        "last_run_date": latest["start_time"][:10] if latest else None,
        "last_run_pace": pace_str,
        "last_run_distance": round((latest["distance_meters"] or 0) / 1000, 2) if latest else None,
        "last_sync": last_sync["sync_time"][:19].replace("T", " ") if last_sync else None,
        "total_indexed": indexed,
        "training_readiness": latest_tr.get("training_readiness_score") if latest_tr else None,
        "training_readiness_level": latest_tr.get("training_readiness_level") if latest_tr else None,
        "vo2_max": latest_vo2.get("vo2_max") if latest_vo2 else None,
        "steps_today": steps_today,
        "steps_date": steps_date,
        "intensity_minutes_week": intensity_total,
    }


@app.get("/api/activities/{activity_id}")
async def activity_detail(activity_id: str):
    activity = await database.get_activity_detail(activity_id)
    if not activity:
        raise HTTPException(404, "Activity not found")
    return activity


@app.get("/api/activities")
async def get_activities(limit: int = 20):
    rows = await database.get_recent_activities(limit)
    out = []
    for a in rows:
        p = a["avg_pace_per_km"]
        pace_str = f"{int(p)}:{int((p % 1) * 60):02d}/km" if p else None
        d = a["duration_seconds"]
        h, rem = divmod(int(d or 0), 3600)
        dur_str = f"{h}h {rem // 60}m" if h else f"{rem // 60}m {rem % 60}s"
        out.append({
            "id": a["id"],
            "type": a["activity_type"],
            "name": a["name"],
            "date": (a["start_time"] or "")[:10],
            "distance_km": round((a["distance_meters"] or 0) / 1000, 2),
            "pace": pace_str,
            "avg_hr": a["avg_heart_rate"],
            "duration": dur_str,
            "elevation_gain": a["elevation_gain"],
            "calories": a["calories"],
            "power": a.get("avg_power"),
            "tss": round(a["training_stress_score"]) if a.get("training_stress_score") else None,
        })
    return {"activities": out}


@app.get("/api/chart-data")
async def get_chart_data(metric: str, days: int = 30):
    if metric == "sleep_score":
        rows = await database.get_recent_sleep(days=days)
        data = [{"date": r["date"], "value": r["sleep_score"]}
                for r in reversed(rows) if r["sleep_score"] is not None]
        return {"data": data, "unit": "/100", "title": "Sleep Score"}

    elif metric == "hrv":
        rows = await database.get_recent_sleep(days=days)
        data = [{"date": r["date"], "value": round(r["avg_hrv"], 1)}
                for r in reversed(rows) if r["avg_hrv"] is not None]
        return {"data": data, "unit": "ms", "title": "HRV"}

    elif metric == "resting_hr":
        rows = await database.get_recent_daily_stats(days=days)
        data = [{"date": r["date"], "value": r["resting_heart_rate"]}
                for r in reversed(rows) if r["resting_heart_rate"] is not None]
        return {"data": data, "unit": "bpm", "title": "Resting Heart Rate", "lower_is_better": True}

    elif metric == "steps":
        rows = await database.get_recent_daily_stats(days=days)
        data = [{"date": r["date"], "value": r["steps"]}
                for r in reversed(rows) if r.get("steps")]
        return {"data": data, "unit": "steps", "title": "Daily Steps"}

    elif metric == "intensity_minutes":
        rows = await database.get_recent_daily_stats(days=days)
        data = []
        for r in reversed(rows):
            v = (r.get("intensity_minutes_moderate") or 0) + (r.get("intensity_minutes_vigorous") or 0)
            if v > 0:
                data.append({"date": r["date"], "value": v})
        return {"data": data, "unit": "min", "title": "Intensity Minutes"}

    elif metric == "training_readiness":
        rows = await database.get_recent_training_metrics(days=days)
        data = [{"date": r["date"], "value": r["training_readiness_score"]}
                for r in reversed(rows) if r["training_readiness_score"] is not None]
        return {"data": data, "unit": "/100", "title": "Training Readiness"}

    elif metric == "vo2_max":
        rows = await database.get_recent_training_metrics(days=days)
        data = [{"date": r["date"], "value": round(r["vo2_max"], 1)}
                for r in reversed(rows) if r["vo2_max"] is not None]
        return {"data": data, "unit": "ml/kg/min", "title": "VO2 Max"}

    elif metric == "daily_distance":
        rows = await database.get_recent_activities(limit=max(days * 3, 500))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        daily: dict[str, float] = defaultdict(float)
        for a in rows:
            d = (a.get("start_time") or "")[:10]
            if d >= cutoff:
                daily[d] += (a.get("distance_meters") or 0) / 1000
        data = [{"date": d, "value": round(v, 2)} for d, v in sorted(daily.items())]
        return {"data": data, "unit": "km", "title": "Daily Distance"}

    elif metric == "weekly_distance":
        rows = await database.get_recent_activities(limit=9999)
        today = date.today()
        weekly: dict[str, float] = {}
        for i in range(min(days // 7 + 1, 104)):
            week_start = today - timedelta(days=today.weekday() + 7 * i)
            weekly[week_start.isoformat()] = 0.0
        for a in rows:
            d_str = (a.get("start_time") or "")[:10]
            if not d_str:
                continue
            try:
                d_obj = date.fromisoformat(d_str)
                key = (d_obj - timedelta(days=d_obj.weekday())).isoformat()
                if key in weekly:
                    weekly[key] += (a.get("distance_meters") or 0) / 1000
            except Exception:
                pass
        data = [{"date": d, "value": round(v, 1)} for d, v in sorted(weekly.items())]
        return {"data": data, "unit": "km", "title": "Weekly Distance"}

    elif metric == "pace":
        rows = await database.get_recent_activities(limit=max(days * 3, 500))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        data = [
            {"date": (a.get("start_time") or "")[:10], "value": round(a["avg_pace_per_km"], 2)}
            for a in reversed(rows)
            if a.get("avg_pace_per_km") and (a.get("start_time") or "")[:10] >= cutoff
        ]
        return {"data": data, "unit": "min/km", "title": "Run Pace", "lower_is_better": True}

    else:
        raise HTTPException(400, f"Unknown metric: {metric}")


@app.get("/api/performance")
async def get_performance():
    data = await database.get_performance_data()

    def fmt_time(secs):
        if not secs:
            return None
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    races = data["race_predictions"]
    return {
        "vo2_max": data["vo2_max"],
        "race_predictions": {
            "date": races["date"] if races else None,
            "5k": fmt_time(races["race_5k_seconds"] if races else None),
            "10k": fmt_time(races["race_10k_seconds"] if races else None),
            "half": fmt_time(races["race_half_seconds"] if races else None),
            "marathon": fmt_time(races["race_marathon_seconds"] if races else None),
        } if races else None,
        "readiness_history": data["readiness_history"],
    }


@app.get("/api/strava/status")
async def strava_status():
    tokens = await database.get_strava_tokens()
    if not tokens:
        return {"connected": False}
    return {"connected": True, "athlete_name": tokens.get("athlete_name"), "athlete_id": tokens.get("athlete_id")}


@app.get("/api/strava/auth")
async def strava_auth():
    client_id = os.getenv("STRAVA_CLIENT_ID")
    if not client_id:
        raise HTTPException(400, "STRAVA_CLIENT_ID not set in .env")
    redirect_uri = os.getenv("STRAVA_REDIRECT_URI", "http://localhost:8000/api/strava/callback")
    return RedirectResponse(strava_client.get_auth_url(client_id, redirect_uri))


@app.get("/api/strava/callback")
async def strava_callback(code: str | None = None, error: str | None = None):
    if error or not code:
        log.warning("Strava OAuth error: %s", error)
        return RedirectResponse("/?strava_error=1")

    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        return RedirectResponse("/?strava_error=missing_creds")

    try:
        data = await strava_client.exchange_code(client_id, client_secret, code)
        athlete = data.get("athlete", {})
        await database.save_strava_tokens(
            data["access_token"], data["refresh_token"], data["expires_at"],
            athlete.get("id"),
            f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip() or None,
        )
        log.info("Strava connected: athlete_id=%s", athlete.get("id"))
    except Exception as e:
        log.error("Strava OAuth token exchange failed: %s", e)
        return RedirectResponse("/?strava_error=1")

    return RedirectResponse("/?strava_connected=1")


@app.post("/api/strava/sync")
async def strava_sync(days: int = 30):
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(400, "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET not set in .env")

    try:
        results = await strava_client.sync_activities(client_id, client_secret, days)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("Strava sync error: %s", e)
        raise HTTPException(500, str(e))

    indexed = await vector_store.index_all_activities()
    log.info("Strava sync complete: %d activities indexed: %d", results["activities"], indexed)
    return {**results, "indexed_in_vector_store": indexed}


@app.get("/api/search")
async def search(q: str, n: int = 8, year: int | None = None):
    hits = await vector_store.search_activities(q, n_results=n, year=year)
    return {"results": hits}


# ---------------------------------------------------------------------------
# Training plans
# ---------------------------------------------------------------------------

def _fitness_context(activities: list[dict], stats: list[dict]) -> str:
    lines = []
    if activities:
        recent = activities[:15]
        total_km = sum((a["distance_meters"] or 0) for a in recent) / 1000
        pace_vals = [a["avg_pace_per_km"] for a in recent if a.get("avg_pace_per_km")]
        avg_pace = sum(pace_vals) / len(pace_vals) if pace_vals else None
        hr_vals = [a["avg_heart_rate"] for a in recent if a.get("avg_heart_rate")]
        avg_hr = round(sum(hr_vals) / len(hr_vals)) if hr_vals else None
        lines.append(f"Recent training (last {len(recent)} runs): {total_km:.1f}km total")
        if avg_pace:
            m, s = int(avg_pace), int((avg_pace % 1) * 60)
            lines.append(f"Average pace: {m}:{s:02d}/km")
        if avg_hr:
            lines.append(f"Average heart rate: {avg_hr} bpm")
        last = activities[0]
        if last.get("start_time"):
            lines.append(
                f"Most recent run: {last['start_time'][:10]} — "
                f"{(last['distance_meters'] or 0) / 1000:.1f}km"
            )
    else:
        lines.append("No run history available — build a plan appropriate for a beginner or returning runner.")
    if stats:
        s = stats[0]
        if s.get("resting_heart_rate"):
            lines.append(f"Resting HR: {s['resting_heart_rate']} bpm")
    return "\n".join(lines)


@app.post("/api/plans")
async def create_plan(req: PlanRequest):
    if not os.getenv("GOOGLE_API_KEY"):
        raise HTTPException(500, "GOOGLE_API_KEY not set in .env")

    if req.race_date:
        try:
            from datetime import date as _date
            weeks = ((_date.fromisoformat(req.race_date) - _date.today()).days) // 7
            if weeks < plan_builder.MIN_WEEKS:
                raise HTTPException(
                    400,
                    f"Race date is only {weeks} week{'s' if weeks != 1 else ''} away — "
                    f"a minimum of {plan_builder.MIN_WEEKS} weeks is needed to build a meaningful plan."
                )
        except ValueError:
            raise HTTPException(400, "Invalid race date format.")

    activities = await database.get_recent_activities(limit=20)
    stats = await database.get_recent_daily_stats(days=7)
    context = _fitness_context(activities, stats)

    try:
        plan = await plan_builder.generate_training_plan(
            race_goal=req.race_goal,
            race_date=req.race_date,
            days_per_week=req.days_per_week,
            current_weekly_km=req.current_weekly_km,
            long_run_day=req.long_run_day,
            fitness_context=context,
            plan_type=req.plan_type,
        )
    except Exception as e:
        log.error("Plan generation failed: %s", e)
        raise HTTPException(500, f"Plan generation failed: {e}")

    plan_id = await database.create_training_plan(
        name=plan.get("title") or req.race_goal,
        race_goal=req.race_goal,
        race_date=req.race_date,
        weekly_days=req.days_per_week,
        current_weekly_km=req.current_weekly_km,
        plan=plan,
    )
    log.info("Plan saved: id=%d title=%r", plan_id, plan.get("title"))
    return {"id": plan_id, "plan": plan}


@app.get("/api/plans")
async def list_plans():
    plans = await database.get_training_plans()
    return {"plans": plans}


@app.get("/api/plans/{plan_id}")
async def get_plan(plan_id: int):
    plan = await database.get_training_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


@app.delete("/api/plans/{plan_id}")
async def delete_plan(plan_id: int):
    deleted = await database.delete_training_plan(plan_id)
    if not deleted:
        raise HTTPException(404, "Plan not found")
    return {"deleted": plan_id}


@app.post("/api/plans/{plan_id}/push-to-garmin")
async def push_plan_to_garmin(plan_id: int):
    plan_data = await database.get_training_plan(plan_id)
    if not plan_data:
        raise HTTPException(404, "Plan not found")
    try:
        result = await garmin_client.push_plan_to_garmin(plan_data["plan"])
        if result.get("workout_ids"):
            await database.store_garmin_workout_ids(plan_id, result["workout_ids"])
        return result
    except garmin_client.GarminSyncError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/plans/{plan_id}/garmin-workouts")
async def delete_plan_from_garmin(plan_id: int):
    workout_ids = await database.get_garmin_workout_ids(plan_id)
    if not workout_ids:
        raise HTTPException(404, "No Garmin workouts found for this plan — push to Garmin first")
    try:
        result = await garmin_client.delete_plan_from_garmin(workout_ids)
        if result["deleted"] > 0:
            await database.store_garmin_workout_ids(plan_id, [])
        return result
    except garmin_client.GarminSyncError as e:
        raise HTTPException(400, str(e))


# Serve frontend static files last (catch-all)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import threading
    import webbrowser
    import uvicorn

    def open_browser():
        import time
        time.sleep(1.2)
        webbrowser.open_new_tab("http://localhost:8000")

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "datefmt": "%Y-%m-%d %H:%M:%S"},
            },
            "handlers": {
                "default": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO"},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"handlers": ["default"], "level": "WARNING"},
                "watchfiles": {"handlers": ["default"], "level": "WARNING"},
            },
        },
    )
