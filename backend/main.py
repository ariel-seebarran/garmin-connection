import os
import sys
from pathlib import Path

# Ensure backend/ is on sys.path regardless of how the server is invoked
sys.path.insert(0, str(Path(__file__).parent))

from contextlib import asynccontextmanager
from datetime import date, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import garmin_client
import vector_store
import agent
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
    }


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
        })
    return {"activities": out}


@app.get("/api/search")
async def search(q: str, n: int = 8, year: int | None = None):
    hits = await vector_store.search_activities(q, n_results=n, year=year)
    return {"results": hits}


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
