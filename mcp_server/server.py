"""
Garmin MCP Server — exposes your running data as tools to any MCP client.

Add to Claude Code (.claude/mcp_servers.json or via /mcp):
{
  "garmin-coach": {
    "command": "python",
    "args": ["<absolute-path>/mcp_server/server.py"],
    "env": { "GARMIN_DB_PATH": "<absolute-path>/backend/garmin.db",
             "GARMIN_CHROMA_PATH": "<absolute-path>/backend/chroma_db" }
  }
}

Then in Claude Code you can ask: "What was my best training week last year?"
"""

import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

load_dotenv(Path(__file__).parent.parent / ".env")

# Resolve DB paths — can be overridden via env
DB_PATH = os.getenv("GARMIN_DB_PATH", str(Path(__file__).parent.parent / "backend" / "garmin.db"))
CHROMA_PATH = os.getenv("GARMIN_CHROMA_PATH", str(Path(__file__).parent.parent / "backend" / "chroma_db"))

# Add backend to path so we can reuse database + vector_store modules
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import database
import vector_store

# Override chroma path before importing
vector_store.CHROMA_PATH = Path(CHROMA_PATH)

server = Server("garmin-coach")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_runs",
            description=(
                "Semantic search through your complete running history. "
                "Great for natural language questions like 'long runs last summer', "
                "'my fastest tempo efforts', 'runs in the rain', 'marathon pace workouts'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "n_results": {"type": "integer", "default": 6, "description": "Number of results"},
                    "year": {"type": "integer", "description": "Filter to a specific year (optional)"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_recent_training",
            description="Get structured recent training data with distances, paces, and heart rates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 28, "description": "How many days back to look"},
                },
            },
        ),
        types.Tool(
            name="get_personal_records",
            description="Find best performances for 5km, 10km, half marathon, marathon, and ultra distances.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_recovery_status",
            description="Get current recovery assessment based on HRV, sleep score, resting HR, and body battery.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_weekly_volume",
            description="Show week-by-week training volume to identify load trends, peaks, and consistency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "weeks": {"type": "integer", "default": 12},
                },
            },
        ),
        types.Tool(
            name="compare_periods",
            description="Compare training metrics between two date ranges (e.g. this month vs same month last year).",
            inputSchema={
                "type": "object",
                "properties": {
                    "period1_label": {"type": "string"},
                    "period1_start": {"type": "string", "description": "YYYY-MM-DD"},
                    "period1_end": {"type": "string", "description": "YYYY-MM-DD"},
                    "period2_label": {"type": "string"},
                    "period2_start": {"type": "string", "description": "YYYY-MM-DD"},
                    "period2_end": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["period1_label", "period1_start", "period1_end",
                             "period2_label", "period2_start", "period2_end"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _fmt_pace(p):
    if not p:
        return "N/A"
    m, s = int(p), int((p % 1) * 60)
    return f"{m}:{s:02d}/km"

def _fmt_dur(sec):
    if not sec:
        return "N/A"
    h, rem = divmod(int(sec), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"

def _fmt_sleep(sec):
    if not sec:
        return "N/A"
    h, rem = divmod(sec, 3600)
    return f"{h}h {rem // 60}m"


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # Ensure DB is initialized
    database.DB_PATH = Path(DB_PATH)
    await database.init_db()

    if name == "search_runs":
        hits = await vector_store.search_activities(
            arguments["query"],
            n_results=arguments.get("n_results", 6),
            year=arguments.get("year"),
        )
        if not hits:
            text = "No matching runs found."
        else:
            lines = [f"Found {len(hits)} matching runs:\n"]
            for i, h in enumerate(hits, 1):
                lines.append(f"{i}. {h['text']}")
            text = "\n".join(lines)

    elif name == "get_recent_training":
        days = arguments.get("days", 28)
        activities = await database.get_recent_activities(limit=200)
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        recent = [a for a in activities if (a.get("start_time") or "") >= cutoff]
        if not recent:
            text = f"No activities in the last {days} days."
        else:
            total = sum((a["distance_meters"] or 0) for a in recent) / 1000
            lines = [f"Last {days} days: {len(recent)} runs, {total:.1f}km\n"]
            for a in recent:
                lines.append(
                    f"  {(a['start_time'] or '')[:10]} | "
                    f"{(a['distance_meters'] or 0)/1000:.1f}km | "
                    f"{_fmt_pace(a['avg_pace_per_km'])} | "
                    f"{a['avg_heart_rate'] or 'N/A'}bpm | "
                    f"{_fmt_dur(a['duration_seconds'])}"
                )
            text = "\n".join(lines)

    elif name == "get_personal_records":
        activities = await database.get_recent_activities(limit=9999)
        buckets = {
            "5km": (4500, 5500),
            "10km": (9000, 11000),
            "Half marathon": (19000, 22000),
            "Marathon": (40000, 45000),
            "Ultra (45km+)": (45000, 999999),
        }
        lines = ["Personal Records:\n"]
        for label, (lo, hi) in buckets.items():
            cands = [a for a in activities if lo <= (a.get("distance_meters") or 0) <= hi and a.get("avg_pace_per_km")]
            if cands:
                best = min(cands, key=lambda a: a["avg_pace_per_km"])
                lines.append(f"  {label}: {_fmt_pace(best['avg_pace_per_km'])} on {(best['start_time'] or '')[:10]}")
            else:
                lines.append(f"  {label}: No data")
        text = "\n".join(lines)

    elif name == "get_recovery_status":
        sleep = await database.get_recent_sleep(days=7)
        daily = await database.get_recent_daily_stats(days=7)
        lines = ["Recovery (last 7 days):\n"]
        if sleep:
            lines.append("Sleep:")
            for s in sleep[:5]:
                hrv = f", HRV {s['avg_hrv']:.0f}ms" if s["avg_hrv"] else ""
                lines.append(f"  {s['date']}: score {s['sleep_score'] or 'N/A'}, {_fmt_sleep(s['total_sleep_seconds'])}{hrv}")
        if daily:
            lines.append("\nWellness:")
            for d in daily[:5]:
                lines.append(f"  {d['date']}: RHR {d['resting_heart_rate'] or 'N/A'}bpm, stress {d['avg_stress_level'] or 'N/A'}")
        text = "\n".join(lines) if len(lines) > 1 else "No recovery data. Sync wellness data first."

    elif name == "get_weekly_volume":
        weeks = arguments.get("weeks", 12)
        activities = await database.get_recent_activities(limit=9999)
        today = date.today()
        lines = [f"Weekly volume ({weeks} weeks):\n"]
        for i in range(weeks - 1, -1, -1):
            ws = today - timedelta(days=today.weekday() + 7 * i)
            we = ws + timedelta(days=6)
            dist = sum(
                (a["distance_meters"] or 0) / 1000
                for a in activities
                if ws.isoformat() <= (a.get("start_time") or "")[:10] <= we.isoformat()
            )
            bar = "█" * int(dist / 2)
            lines.append(f"  {ws.isoformat()}: {dist:5.1f}km  {bar}")
        text = "\n".join(lines)

    elif name == "compare_periods":
        activities = await database.get_recent_activities(limit=9999)

        def period(start, end):
            return [a for a in activities if start <= (a.get("start_time") or "")[:10] <= end]

        def summarize(acts, label):
            if not acts:
                return f"{label}: No data"
            dist = sum((a["distance_meters"] or 0) for a in acts) / 1000
            paces = [a["avg_pace_per_km"] for a in acts if a["avg_pace_per_km"]]
            avg_pace = sum(paces) / len(paces) if paces else None
            return f"{label}: {len(acts)} runs, {dist:.1f}km, avg pace {_fmt_pace(avg_pace)}"

        p1 = period(arguments["period1_start"], arguments["period1_end"])
        p2 = period(arguments["period2_start"], arguments["period2_end"])
        text = "Period Comparison:\n" + summarize(p1, arguments["period1_label"]) + "\n" + summarize(p2, arguments["period2_label"])

    else:
        text = f"Unknown tool: {name}"

    return [types.TextContent(type="text", text=text)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
