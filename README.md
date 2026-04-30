# Coach Claude — AI Running Coach

AI-powered running coach that uses your complete training history to answer questions, analyze performance, and build personalised training plans.

## Stack
- **Backend**: FastAPI + LangGraph ReAct agent + ChromaDB RAG
- **LLM**: Google Gemini (via `GOOGLE_API_KEY`)
- **Data**: Garmin Connect or Strava → SQLite + ChromaDB (vector search)
- **Frontend**: Vanilla JS + dark UI, no build step
- **Auth**: Per-user accounts with JWT cookies

---

## Running locally (recommended for Garmin users)

Garmin sync works from a home/residential IP but is blocked from cloud servers. Run locally to get full Garmin data including sleep, HRV, training readiness, and race predictions.

```bash
# 1. Create and activate venv
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac/Linux

# 2. Install dependencies
pip install -r backend/requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY at minimum

# 4. Run
python backend/main.py
# Opens http://localhost:8000
```

**First run:**
1. Register an account at `http://localhost:8000`
2. Click **Sync Garmin**, enter your Garmin credentials, check **Sync full history**
3. Wait ~2–5 min for sync + indexing
4. Ask anything

**Push local Garmin data to your cloud server** (optional — keeps cloud in sync):

```bash
python scripts/push_to_cloud.py
# Enter your cloud username/password when prompted
```

Run this any time after a local Garmin sync to upload your data to the cloud server. Your friends can then ask the coach questions about your data too.

---

## Cloud deployment (for sharing with friends)

Friends connect their own Strava accounts on the cloud server. Garmin sync is blocked by Garmin from cloud IPs — use the push script above to upload your own Garmin data.

See [`deploy/setup.sh`](deploy/setup.sh) for the full Oracle Cloud Free Tier setup script (Ubuntu 22.04).

**Quick deploy:**
```bash
# On your server (Ubuntu 22.04)
curl -o setup.sh https://raw.githubusercontent.com/ariel-seebarran/garmin-connection/main/deploy/setup.sh
chmod +x setup.sh
bash setup.sh
```

The script installs everything, sets up nginx + HTTPS (Let's Encrypt), and starts the app as a systemd service.

**Deploying updates:**
```bash
sudo -u coachclaude git -C /opt/coach-claude/app pull && sudo systemctl restart coach-claude
```

---

## API endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/auth/register` | Create account |
| `POST /api/auth/login` | Log in (sets httpOnly cookie) |
| `POST /api/sync` | Sync Garmin data |
| `GET /api/strava/auth` | Start Strava OAuth flow |
| `POST /api/strava/sync` | Sync Strava activities |
| `POST /api/chat` | Streaming SSE chat with the agent |
| `GET /api/stats` | Dashboard stats summary |
| `GET /api/activities` | Recent activity list |
| `GET /api/export` | Export all your data as JSON |
| `POST /api/import` | Import data (used by push script) |
| `GET /api/search?q=...` | Semantic search |
