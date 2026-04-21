# Garmin AI Running Coach

AI-powered running coach that uses your complete Garmin history (3+ years) to answer questions, analyze performance, and build personalized training plans.

## Stack
- **Backend**: FastAPI + LangGraph ReAct agent + ChromaDB RAG
- **LLM**: Claude (Sonnet 4.6) with streaming
- **Data**: Garmin Connect → SQLite (raw) + ChromaDB (vector search)
- **MCP Server**: Query your running data from Claude Code directly
- **Frontend**: Vanilla JS + dark UI, no build step

## Setup

```bash
# 1. Create and activate venv
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac/Linux

# 2. Install dependencies
pip install -r backend/requirements.txt
pip install -r mcp_server/requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env with your Garmin email/password and Anthropic API key

# 4. Run
python backend/main.py
# Open http://localhost:8000
```

## First use

1. Open `http://localhost:8000`
2. Click **Sync Garmin** → check **Sync full history** for the first run
3. Wait for sync + indexing (~2-5 min for 3+ years)
4. Ask anything!

## MCP Server (use from Claude Code)

Add to your Claude Code MCP config (`~/.claude/mcp_servers.json`):

```json
{
  "garmin-coach": {
    "command": "C:/Users/ariel/Dev/garmin-connection/.venv/Scripts/python.exe",
    "args": ["C:/Users/ariel/Dev/garmin-connection/mcp_server/server.py"]
  }
}
```

Then in Claude Code: *"What was my best training month last year?"*

## Agent tools

| Tool | Description |
|------|-------------|
| `search_training_history` | Semantic search across full 3+ year history |
| `get_recent_training` | Structured data for last N days |
| `get_personal_records` | Best paces by distance |
| `compare_training_periods` | Year-over-year or any two date ranges |
| `get_recovery_status` | HRV + sleep + RHR synthesis |
| `get_weekly_volume_trend` | Week-by-week mileage with trend analysis |

## API endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/sync` | Sync Garmin data (pass `full_history: true` for all-time) |
| `POST /api/chat` | Streaming SSE chat with the LangGraph agent |
| `GET /api/stats` | Dashboard stats summary |
| `GET /api/activities` | Recent activity list |
| `GET /api/search?q=...` | Semantic search (direct vector DB query) |
| `POST /api/index` | Re-index ChromaDB without re-syncing |
