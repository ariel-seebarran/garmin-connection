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
python backend/main.py      # Windows
python3 backend/main.py     # Mac/Linux
# Opens http://localhost:8000 automatically
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

---

## MCP Server (use from Claude Code)

The MCP server lets you query your running data directly from Claude Code in your IDE — no browser needed.

**Setup:** add to `~/.claude/mcp_servers.json`:

```json
{
  "garmin-coach": {
    "command": "C:/Users/ariel/Dev/garmin-connection/.venv/Scripts/python.exe",
    "args": ["C:/Users/ariel/Dev/garmin-connection/mcp_server/server.py"]
  }
}
```

Or via the Claude Code CLI:
```bash
claude mcp add garmin-coach \
  .venv/Scripts/python.exe mcp_server/server.py
```

The MCP server reads your local SQLite + ChromaDB directly — no HTTP server needed.

**Available tools:**

| Tool | Example prompt |
|------|---------------|
| `search_runs` | "Find my long runs from last summer" |
| `get_recent_training` | "What have I done in the last 4 weeks?" |
| `get_personal_records` | "What's my 10K PR?" |
| `get_recovery_status` | "How recovered am I based on HRV and sleep?" |
| `get_weekly_volume` | "Show my mileage trend over the last 3 months" |
| `compare_periods` | "Compare this April to last April" |

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full system diagram, data flow, and file map.

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
