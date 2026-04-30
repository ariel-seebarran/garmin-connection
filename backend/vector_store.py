"""
ChromaDB vector store for semantic search across full running history.

Each activity is embedded as a natural language description so that queries
like "long runs in summer 2023" or "hard tempo efforts" retrieve relevant
results without requiring exact date/field matches.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

import database

CHROMA_PATH = Path(__file__).parent / "chroma_db"
COLLECTION_NAME = "running_activities"

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        ef = embedding_functions.DefaultEmbeddingFunction()
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _fmt_pace(pace: float | None) -> str:
    if not pace:
        return "unknown pace"
    m, s = int(pace), int((pace % 1) * 60)
    return f"{m}:{s:02d}/km"


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "unknown duration"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _activity_to_text(a: dict) -> str:
    dist_km = (a.get("distance_meters") or 0) / 1000
    dt = a.get("start_time") or ""
    try:
        parsed = datetime.fromisoformat(dt)
        day_name = parsed.strftime("%A")
        month_name = parsed.strftime("%B")
        year = parsed.year
        hour = parsed.hour
        time_of_day = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        date_desc = f"{day_name} {month_name} {parsed.day} {year}, {time_of_day}"
    except Exception:
        date_desc = dt[:10] if dt else "unknown date"

    effort = "easy"
    hr = a.get("avg_heart_rate")
    if hr:
        if hr >= 170:
            effort = "very hard / race effort"
        elif hr >= 160:
            effort = "hard / threshold"
        elif hr >= 150:
            effort = "moderate / tempo"
        elif hr >= 140:
            effort = "comfortable"

    te = a.get("aerobic_training_effect")
    te_desc = f", training effect {te:.1f}" if te else ""

    elev = a.get("elevation_gain") or 0
    elev_desc = f", {elev:.0f}m elevation gain" if elev > 10 else ""

    power = a.get("avg_power")
    power_desc = f", {power}W avg power" if power else ""

    tss = a.get("training_stress_score")
    tss_desc = f", TSS {tss:.0f}" if tss else ""

    return (
        f"{dist_km:.1f}km {effort} {a.get('activity_type', 'run')} on {date_desc}. "
        f"Pace: {_fmt_pace(a.get('avg_pace_per_km'))}, "
        f"heart rate: {hr or 'N/A'} bpm avg, "
        f"duration: {_fmt_duration(a.get('duration_seconds'))}"
        f"{elev_desc}{te_desc}{power_desc}{tss_desc}."
    )


def _activity_metadata(a: dict) -> dict:
    dt = a.get("start_time") or ""
    try:
        parsed = datetime.fromisoformat(dt)
        year = parsed.year
        month = parsed.month
        week = parsed.isocalendar()[1]
    except Exception:
        year = month = week = 0

    return {
        "activity_id": str(a.get("id", "")),
        "date": dt[:10] if dt else "",
        "year": year,
        "month": month,
        "week": week,
        "activity_type": a.get("activity_type", ""),
        "distance_km": round((a.get("distance_meters") or 0) / 1000, 2),
        "avg_pace_per_km": float(a.get("avg_pace_per_km") or 0),
        "avg_heart_rate": int(a.get("avg_heart_rate") or 0),
        "duration_seconds": float(a.get("duration_seconds") or 0),
        "elevation_gain": float(a.get("elevation_gain") or 0),
    }


async def index_all_activities():
    """Embed and store all activities from SQLite into ChromaDB."""
    activities = await database.get_recent_activities(limit=9999)
    if not activities:
        return 0

    collection = await asyncio.get_event_loop().run_in_executor(None, _get_collection)

    batch_size = 100
    total = 0
    for i in range(0, len(activities), batch_size):
        batch = activities[i : i + batch_size]
        ids = [str(a["id"]) for a in batch]
        documents = [_activity_to_text(a) for a in batch]
        metadatas = [_activity_metadata(a) for a in batch]

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: collection.upsert(ids=ids, documents=documents, metadatas=metadatas),
        )
        total += len(batch)

    return total


async def search_activities(
    query: str,
    n_results: int = 8,
    year: int | None = None,
    month: int | None = None,
) -> list[dict]:
    """Semantic search across the full activity history."""
    collection = await asyncio.get_event_loop().run_in_executor(None, _get_collection)

    where = None
    if year and month:
        where = {"$and": [{"year": year}, {"month": month}]}
    elif year:
        where = {"year": year}
    elif month:
        where = {"month": month}

    kwargs = dict(query_texts=[query], n_results=min(n_results, collection.count() or 1))
    if where:
        kwargs["where"] = where

    results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: collection.query(**kwargs)
    )

    hits = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, distances):
        hits.append({"text": doc, "metadata": meta, "similarity": round(1 - dist, 3)})

    return hits


async def get_indexed_count() -> int:
    collection = await asyncio.get_event_loop().run_in_executor(None, _get_collection)
    return collection.count()
