# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

## [0.5.0] — 2026-04-30

### Added
- **Multi-user auth** — JWT httpOnly cookies, open registration, bcrypt password hashing. All data endpoints require authentication; every DB table keyed by `user_id`
- **Per-user Garmin credential storage** — passwords encrypted with AES-128 (Fernet) derived from `SECRET_KEY`; stored in `users` table so each user's Garmin account is independent
- **Per-user Strava tokens** — `strava_tokens` table changed from a singleton to `user_id PRIMARY KEY`
- **`/api/auth/register`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`** endpoints
- **Login / register overlay** — full-screen auth card with tab toggle, shown on first load if unauthenticated; logout button in sidebar header
- **`scripts/push_to_cloud.py`** — reads local SQLite and POSTs to `/api/import` on the cloud server; used to keep cloud Garmin data in sync after a local sync
- **`GET /api/export` / `POST /api/import`** — full user data dump and bulk upsert (activities, sleep, daily stats, training metrics)
- **Route maps in activity detail modal** — Leaflet + OpenStreetMap; Strava activities show full polyline with green/red start/finish markers; Garmin activities show start-point marker
- **`GET /api/activities/{id}/map`** — returns decoded lat/lon coordinate array (route or point) from `raw_json`
- **`ARCHITECTURE.md`** — system diagram, data flow for Garmin/Strava/chat/plans, key file map, auth model, MCP server tool table
- **`deploy/setup.sh`** now auto-generates `SECRET_KEY` and writes `COOKIE_SECURE=true`; basic auth (htpasswd) removed since the app has its own login system
- **Database migration** (`_migrate_to_multiuser`) — detects old single-user schema via `PRAGMA table_info`, recreates date-keyed tables with composite `PRIMARY KEY (user_id, date)`, preserves existing data at `user_id=0`

### Changed
- `deploy/nginx.conf` — removed `auth_basic` block
- `backend/requirements.txt` — added `bcrypt>=4.0.1`, `python-jose[cryptography]`, `cryptography`; removed `passlib` (incompatible with chromadb's bcrypt>=4.0.1 requirement)
- README updated with local-vs-cloud workflow, MCP server setup table, and link to ARCHITECTURE.md

### Fixed
- `vector_store.py` — removed runtime `X | None` type annotation that caused `TypeError` on Python 3.11 with chromadb's version of `PersistentClient`
- `deploy/setup.sh` `chmod 600 .env` now runs as `sudo` to avoid permission denied when the file is owned by the app user

## [0.4.0] — 2026-04-23

### Added
- **Strava OAuth integration** — full connect flow (`GET /api/strava/auth` → OAuth → callback), token storage in a singleton `strava_tokens` table, automatic token refresh 60 s before expiry
- `POST /api/strava/sync` — paginates Strava athlete activities (100/page), filters to running sport types, upserts into the shared `activities` table with a `strava_` id prefix to avoid collision with Garmin integer ids
- `GET /api/activities/{activity_id}` — returns a full activity row with `raw_json` parsed into a `raw_data` dict for the detail modal
- **Activity detail modal** — clicking any run in the sidebar opens an 8-section detail view (Overview, Pace & Speed, Heart Rate, Elevation, Cadence, Power, Running Dynamics, Training Load). Null/zero values are suppressed; each section only renders if it has at least one value. "Ask Coach Claude" button prefills the chat input
- **Linear regression trendline** — all charts gain a dashed overlay computed with least-squares; trendline is excluded from hover tooltips via Chart.js `filter` callback
- **Separate Garmin / Strava modals** — sidebar now shows three buttons: Sync Garmin (blue), Strava (orange with logo), and Performance. Each source opens its own dedicated modal with source-appropriate copy. Strava modal explains which metrics are unavailable (sleep, HRV, readiness)
- **Source-aware sidebar** — on load the app detects whether Strava is connected and whether Garmin-specific fields (HRV, sleep, resting HR) are present. When Strava-only, six Garmin-exclusive stat cards are hidden (`body.strava-only` CSS class)
- **Oracle Cloud Free Tier deployment** — `deploy/setup.sh` (interactive, idempotent Ubuntu 22.04 setup: venv, systemd service, nginx reverse proxy, Let's Encrypt SSL, OS firewall), `deploy/coach-claude.service`, `deploy/nginx.conf` (basic auth + 600 s proxy timeouts for long Garmin syncs)
- **`pyproject.toml`** — replaces `pytest.ini`; declares project metadata, runtime dependencies, dev extras, and `[tool.pytest.ini_options]`
- **App logo** (`frontend/logo.svg`) — 512×512 SVG with dark navy background, runner silhouette, lightning bolt, and "COACH CLAUDE" wordmark; suitable for Strava OAuth app registration
- `test_strava_client.py` — 20 new tests covering `get_auth_url`, `_map_activity` (id prefix, pace calc, zero distance, date stripping, sport type mapping, raw_json serialisation), `sync_activities` (filters non-running, correct count, pagination, not-connected error), `_get_valid_token` (refresh on expiry)
- 9 new `test_database.py` tests: `get_activity_detail` (found / not found), `save_and_get_strava_tokens`, strava_tokens singleton behaviour, `upsert_strava_activity` (stores / idempotent)
- 9 new `test_api.py` tests: `GET /api/activities/{id}` (found / not found), `GET /api/strava/status` (connected / disconnected), `GET /api/strava/auth` (redirects / missing env), `POST /api/strava/sync` (missing env / not connected / success)

### Changed
- `strava_client.py` — added log messages for sync start, per-page fetch count (DEBUG), token refresh start and success
- Sidebar drag-and-drop card ordering and resizable stats/feed divider persist across sessions via `localStorage`
- Quick-prompt buttons cover additional coaching scenarios (year-over-year comparison, best long runs, spot trends)

### Fixed
- `test_api.py` `test_chat_missing_api_key` was checking for `ANTHROPIC_API_KEY` but the app uses `GOOGLE_API_KEY` — corrected
- Ground contact time in activity detail modal now renders as a rounded integer (`Math.round`) instead of a raw float (e.g. `266.6000061035156 ms` → `267 ms`)
- Goal input field removed from chat header (it was non-functional; training plans are requested via the main chat)

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
