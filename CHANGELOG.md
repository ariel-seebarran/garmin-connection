# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

## [0.3.0] — 2026-04-19

### Added
- **Unit tests** — 43 tests across `test_database.py`, `test_vector_store.py`, `test_garmin_client.py`, and `test_api.py` using pytest + pytest-asyncio
- **Structured logging** — `logging_config.py` with rotating file handler (`logs/app.log`, 10 MB, 5 backups) and console output. Key events logged: server startup, sync progress, tool calls, chat requests
- `pytest.ini` config with `asyncio_mode = auto` and `pythonpath = backend`
- `requirements-dev.txt` for test dependencies (pytest, pytest-asyncio, httpx)

### Changed
- `garmin_client.py`, `agent.py`, `main.py` — all major operations now emit log messages at appropriate levels

---

## [0.2.0] — 2026-04-19

### Added
- **MFA support** — Garmin accounts with 2-factor authentication are now handled. The sync modal reveals a 6-digit code field automatically when MFA is detected
- Garmin session tokens cached to `.garmin_tokens` — MFA prompt only appears on first sync; subsequent syncs reuse the cached session
- `mfa_code` field on `POST /api/sync` request body
- HTTP 449 response when MFA is required (distinct from 401 auth failure) so the frontend can show the right UI

### Fixed
- `sync_full_history` and `_sync_activities` now type-guard Garmin API responses (`isinstance(a, dict)`) to suppress Pylance false positives and be more defensive

---

## [0.1.0] — 2026-04-18

### Added
- **Full Garmin history sync** — paginated `sync_full_history()` fetches all activities across 3+ years (100 per page), not just recent days. Wellness data (sleep, daily stats) synced for last 90 days
- **ChromaDB RAG** — each activity embedded as a natural language description ("5.2km comfortable run on Saturday morning…") with metadata for year/month/week filtering. Enables semantic queries like "long runs last summer"
- **LangGraph ReAct agent** — six tools the agent chains automatically before answering:
  - `search_training_history` — semantic search across full history
  - `get_recent_training` — structured data for last N days
  - `get_personal_records` — best pace by distance bucket (5k, 10k, HM, marathon, ultra)
  - `compare_training_periods` — year-over-year or any two date ranges
  - `get_recovery_status` — HRV + sleep score + RHR synthesis
  - `get_weekly_volume_trend` — week-by-week mileage with ASCII bar chart
- **Streaming SSE chat** — `POST /api/chat` streams tool call indicators ("Searching history…") and response text as Server-Sent Events
- **MCP server** (`mcp_server/server.py`) — exposes the same six tools to any MCP client; add to Claude Code config to query your running data from the terminal
- **FastAPI backend** with endpoints: `/api/sync`, `/api/chat`, `/api/stats`, `/api/activities`, `/api/search`, `/api/index`
- **Vanilla JS frontend** — dark UI, stats dashboard, activity feed, quick-prompt buttons, goal input field
- SQLite database with tables: `activities`, `sleep_data`, `daily_stats`, `sync_log`
- `.env`-based credential management; credentials never stored beyond the session token cache
- Virtual environment setup with `backend/requirements.txt` and `mcp_server/requirements.txt`
