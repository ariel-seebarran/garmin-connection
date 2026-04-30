# Architecture

## System diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  Your local machine (Windows/Mac)                                   │
│                                                                     │
│  ┌─────────────────┐    Garmin Connect API                         │
│  │  backend/       │ ←─────────────────────── garminconnect lib    │
│  │  main.py        │    (blocked from cloud IPs; works locally)    │
│  │  (FastAPI)      │                                               │
│  │                 │    SQLite (garmin.db)                         │
│  │  ChromaDB       │ ←─ activities, sleep, HRV, daily stats       │
│  │  (chroma_db/)   │    training metrics, plans, users             │
│  └────────┬────────┘                                               │
│           │  POST /api/import                                       │
│           │  (push_to_cloud.py)                                     │
│           ▼                                                         │
│  ┌─────────────────┐                                               │
│  │  mcp_server/    │ ←── Claude Code (IDE) via MCP stdio           │
│  │  server.py      │     search_runs, get_recent_training,         │
│  │                 │     get_personal_records, compare_periods ...  │
│  └─────────────────┘                                               │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ push_to_cloud.py
                                   │ (after each Garmin sync)
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Oracle Cloud Free Tier (Ubuntu 22.04, coachclaude.mooo.com)        │
│                                                                     │
│  ┌──────────┐    ┌──────────────────┐    ┌───────────────────────┐ │
│  │  nginx   │    │  backend/        │    │  SQLite (garmin.db)   │ │
│  │  :443    │───▶│  main.py         │───▶│  per-user data:       │ │
│  │  (HTTPS) │    │  (FastAPI/       │    │  activities, sleep,   │ │
│  │          │    │   uvicorn)       │    │  daily stats,         │ │
│  │  Let's   │    │  :8000           │    │  training metrics,    │ │
│  │  Encrypt │    │                  │    │  plans, users         │ │
│  └──────────┘    │  ChromaDB        │    └───────────────────────┘ │
│                  │  (chroma_db/)    │                               │
│                  │                  │    Strava API (OAuth)         │
│                  │  LangGraph agent │ ←─ works fine from cloud     │
│                  │  Google Gemini   │                               │
│                  └──────────────────┘                               │
│                                                                     │
│  Friends connect via browser → register → link Strava               │
│  You push Garmin data via push_to_cloud.py                          │
└─────────────────────────────────────────────────────────────────────┘
```

## Data flow

### Garmin sync (local only)
```
Garmin Connect → garminconnect lib → FastAPI /api/sync
→ SQLite (activities, sleep, daily_stats, training_metrics)
→ ChromaDB (vector embeddings for semantic search)
→ push_to_cloud.py → POST /api/import on cloud server
```

### Strava sync (cloud)
```
User clicks "Connect Strava" → OAuth redirect → Strava API
→ access token saved per user → /api/strava/sync
→ SQLite activities → ChromaDB
```

### Chat / coaching
```
User message → POST /api/chat (SSE streaming)
→ LangGraph ReAct agent
→ Tools: get_recent_activities, get_sleep_data, search_vector_store ...
→ Google Gemini (LLM) generates response
→ Streamed token-by-token to browser
```

### Training plans
```
User fills plan builder form → POST /api/plans
→ LLM generates week-by-week plan → saved to SQLite
→ Optional: POST /api/plans/{id}/push-to-garmin
  → garminconnect lib creates workouts on watch
```

---

## Key files

```
garmin-connection/
├── backend/
│   ├── main.py            # FastAPI app, all HTTP endpoints
│   ├── auth.py            # JWT cookies, bcrypt, Fernet encryption
│   ├── database.py        # SQLite helpers, schema, migration
│   ├── garmin_client.py   # Garmin sync logic (local only)
│   ├── strava_client.py   # Strava OAuth + activity sync
│   ├── agent.py           # LangGraph ReAct agent + tools
│   ├── plan_builder.py    # Training plan generation
│   ├── vector_store.py    # ChromaDB indexing + search
│   └── logging_config.py
├── frontend/
│   ├── index.html         # Single-page app shell
│   ├── app.js             # All UI logic (auth, chat, charts, plans)
│   └── style.css          # Dark theme
├── mcp_server/
│   └── server.py          # MCP stdio server for Claude Code
├── scripts/
│   └── push_to_cloud.py   # Push local Garmin data to cloud server
└── deploy/
    ├── setup.sh           # Oracle Cloud setup script
    ├── nginx.conf         # nginx reverse proxy config
    └── coach-claude.service  # systemd service unit
```

---

## Auth model

- **Registration**: open (anyone with the URL can register)
- **Sessions**: JWT stored in httpOnly cookie, 30-day expiry
- **Passwords**: bcrypt hashed
- **Garmin credentials**: AES-128 (Fernet) encrypted with a key derived from `SECRET_KEY`, stored per user in SQLite
- **Data isolation**: all tables keyed by `user_id`; queries always filter by the authenticated user

---

## MCP Server

See [MCP Server](#mcp-server-use-from-claude-code) in the README for setup. The MCP server reads directly from the local SQLite + ChromaDB — it does not go through the HTTP API and requires no server to be running.

Tools exposed:

| Tool | Description |
|------|-------------|
| `search_runs` | Semantic search across full history via ChromaDB |
| `get_recent_training` | Structured activity list for last N days |
| `get_personal_records` | Best pace per distance bucket (5K → ultra) |
| `get_recovery_status` | HRV, sleep score, RHR for last 7 days |
| `get_weekly_volume` | Week-by-week km totals with ASCII bar chart |
| `compare_periods` | Two date ranges side-by-side (runs, km, avg pace) |
