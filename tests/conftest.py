import sys
import pytest
import tempfile
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


SAMPLE_ACTIVITY = {
    "activityId": "123456789",
    "activityName": "Morning Run",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": "2024-03-15 07:30:00",
    "duration": 3600.0,
    "distance": 10000.0,
    "averageHR": 145,
    "maxHR": 172,
    "elevationGain": 85.0,
    "calories": 680,
    "aerobicTrainingEffect": 3.2,
}

SAMPLE_SLEEP = {
    "dailySleepDTO": {
        "sleepScores": {"overall": {"value": 78}},
        "sleepTimeSeconds": 27000,
        "deepSleepSeconds": 5400,
        "lightSleepSeconds": 14400,
        "remSleepSeconds": 5400,
        "awakeSleepSeconds": 1800,
    },
    "avgOvernightHrv": 52.3,
}

SAMPLE_STATS = {
    "totalSteps": 8500,
    "restingHeartRate": 52,
    "averageStressLevel": 28,
    "minBodyBattery": 15,
    "maxBodyBattery": 87,
    "activeKilocalories": 420,
}


@pytest.fixture
async def tmp_db(tmp_path):
    """Provide a fresh isolated SQLite database for each test."""
    import database
    original = database.DB_PATH
    database.DB_PATH = tmp_path / "test.db"
    await database.init_db()
    yield database
    database.DB_PATH = original
