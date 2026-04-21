"""
LangGraph ReAct agent — your AI running coach.

The agent has six tools it can call in any order or combination before
answering, allowing multi-hop reasoning like:
  1. search history for similar workouts
  2. check recovery data
  3. synthesize a personalized plan
"""

import json
from datetime import date, timedelta
from typing import AsyncGenerator

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

import database
import vector_store
from logging_config import get_logger

log = get_logger("agent")

MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Helper formatters
# ---------------------------------------------------------------------------

def _fmt_pace(p: float | None) -> str:
    if not p:
        return "N/A"
    m, s = int(p), int((p % 1) * 60)
    return f"{m}:{s:02d}/km"


def _fmt_dur(sec: float | None) -> str:
    if not sec:
        return "N/A"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def _fmt_dist(m: float | None) -> str:
    if not m:
        return "N/A"
    return f"{m / 1000:.2f}km"


def _fmt_sleep(sec: int | None) -> str:
    if not sec:
        return "N/A"
    h, rem = divmod(sec, 3600)
    return f"{h}h {rem // 60}m"


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

@tool
async def search_training_history(query: str, n_results: int = 8) -> str:
    """
    Semantic search through the athlete's ENTIRE running history (3+ years).
    Use this for questions about past performance, specific time periods,
    workout types, or finding similar efforts.
    Examples: "long runs over 20km", "tempo runs summer 2023",
    "runs with high heart rate", "race efforts", "trail runs".
    """
    hits = await vector_store.search_activities(query, n_results=n_results)
    if not hits:
        return "No matching activities found in history."
    lines = [f"Found {len(hits)} relevant activities:\n"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['text']} (similarity: {h['similarity']})")
    return "\n".join(lines)


@tool
async def get_recent_training(days: int = 28) -> str:
    """
    Get structured data on the most recent training period.
    Use this to understand current fitness, recent load, and trends.
    Default is last 28 days. Adjust days as needed (e.g., 7, 14, 28, 90).
    """
    activities = await database.get_recent_activities(limit=200)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [a for a in activities if (a.get("start_time") or "") >= cutoff]

    if not recent:
        return f"No activities in the last {days} days."

    total_dist = sum((a["distance_meters"] or 0) for a in recent) / 1000
    total_time = sum((a["duration_seconds"] or 0) for a in recent) / 3600
    avg_hr_vals = [a["avg_heart_rate"] for a in recent if a["avg_heart_rate"]]
    avg_hr = round(sum(avg_hr_vals) / len(avg_hr_vals)) if avg_hr_vals else None

    lines = [
        f"Last {days} days — {len(recent)} runs, {total_dist:.1f}km total, {total_time:.1f}h",
        f"Average HR: {avg_hr or 'N/A'} bpm\n",
        "Activities (newest first):",
    ]
    for a in recent:
        lines.append(
            f"  {(a['start_time'] or '')[:10]} | {_fmt_dist(a['distance_meters'])} | "
            f"{_fmt_pace(a['avg_pace_per_km'])} | {a['avg_heart_rate'] or 'N/A'}bpm | "
            f"{_fmt_dur(a['duration_seconds'])} | elev +{a['elevation_gain'] or 0:.0f}m"
        )
    return "\n".join(lines)


@tool
async def get_personal_records() -> str:
    """
    Find personal best performances across common distances.
    Returns best pace for 5km, 10km, half marathon, marathon distances
    based on all recorded activities.
    """
    activities = await database.get_recent_activities(limit=9999)
    if not activities:
        return "No activities found."

    buckets = {
        "5km (4.5–5.5km)": (4500, 5500),
        "10km (9–11km)": (9000, 11000),
        "Half marathon (19–22km)": (19000, 22000),
        "Marathon (40–45km)": (40000, 45000),
        "Ultra (45km+)": (45000, 999999),
    }

    lines = ["Personal Records by distance:\n"]
    for label, (lo, hi) in buckets.items():
        candidates = [
            a for a in activities
            if lo <= (a.get("distance_meters") or 0) <= hi
            and a.get("avg_pace_per_km")
        ]
        if candidates:
            best = min(candidates, key=lambda a: a["avg_pace_per_km"])
            lines.append(
                f"  {label}: {_fmt_pace(best['avg_pace_per_km'])} avg pace "
                f"on {(best['start_time'] or '')[:10]} "
                f"({_fmt_dist(best['distance_meters'])})"
            )
        else:
            lines.append(f"  {label}: No matching runs")

    return "\n".join(lines)


@tool
async def compare_training_periods(
    period1_label: str,
    period1_start: str,
    period1_end: str,
    period2_label: str,
    period2_start: str,
    period2_end: str,
) -> str:
    """
    Compare training metrics between two date ranges.
    Dates must be in YYYY-MM-DD format.
    Example: compare "Last month" (2024-03-01 to 2024-03-31)
    vs "Same month last year" (2023-03-01 to 2023-03-31).
    """
    activities = await database.get_recent_activities(limit=9999)

    def filter_period(start: str, end: str) -> list:
        return [
            a for a in activities
            if start <= (a.get("start_time") or "")[:10] <= end
        ]

    def summarize(acts: list, label: str) -> str:
        if not acts:
            return f"{label}: No data"
        dist = sum((a["distance_meters"] or 0) for a in acts) / 1000
        paces = [a["avg_pace_per_km"] for a in acts if a["avg_pace_per_km"]]
        avg_pace = sum(paces) / len(paces) if paces else None
        hrs = [a["avg_heart_rate"] for a in acts if a["avg_heart_rate"]]
        avg_hr = round(sum(hrs) / len(hrs)) if hrs else None
        return (
            f"{label} ({len(acts)} runs, {dist:.1f}km total): "
            f"avg pace {_fmt_pace(avg_pace)}, avg HR {avg_hr or 'N/A'}bpm"
        )

    p1 = filter_period(period1_start, period1_end)
    p2 = filter_period(period2_start, period2_end)

    lines = [
        "Period comparison:",
        summarize(p1, period1_label),
        summarize(p2, period2_label),
    ]

    if p1 and p2:
        d1 = sum((a["distance_meters"] or 0) for a in p1) / 1000
        d2 = sum((a["distance_meters"] or 0) for a in p2) / 1000
        diff = d1 - d2
        lines.append(f"\nVolume change: {diff:+.1f}km ({'+' if diff >= 0 else ''}{diff / d2 * 100:.0f}%)" if d2 else "")

    return "\n".join(lines)


@tool
async def get_recovery_status() -> str:
    """
    Assess current recovery readiness using sleep, HRV, resting HR, and body battery.
    Always call this before recommending training intensity or building a plan.
    """
    sleep = await database.get_recent_sleep(days=7)
    daily = await database.get_recent_daily_stats(days=7)

    if not sleep and not daily:
        return "No recovery data available. Sync wellness data first."

    lines = ["Recovery Status (last 7 days):\n"]

    if sleep:
        lines.append("Sleep:")
        for s in sleep[:5]:
            hrv_str = f", HRV: {s['avg_hrv']:.0f}ms" if s["avg_hrv"] else ""
            lines.append(
                f"  {s['date']}: score {s['sleep_score'] or 'N/A'}/100, "
                f"{_fmt_sleep(s['total_sleep_seconds'])} total"
                f" (deep: {_fmt_sleep(s['deep_sleep_seconds'])}, "
                f"REM: {_fmt_sleep(s['rem_sleep_seconds'])})"
                f"{hrv_str}"
            )
        scores = [s["sleep_score"] for s in sleep if s["sleep_score"]]
        hrvs = [s["avg_hrv"] for s in sleep if s["avg_hrv"]]
        if scores:
            lines.append(f"  → 7-day avg score: {round(sum(scores)/len(scores))}/100")
        if hrvs:
            trend = "↑ improving" if len(hrvs) >= 2 and hrvs[0] > hrvs[-1] else "↓ declining" if len(hrvs) >= 2 and hrvs[0] < hrvs[-1] else "→ stable"
            lines.append(f"  → HRV trend: {round(sum(hrvs)/len(hrvs), 1)}ms avg, {trend}")

    if daily:
        lines.append("\nWellness:")
        for d in daily[:5]:
            bb = f"body battery {d['body_battery_low'] or '?'}-{d['body_battery_high'] or '?'}" if d["body_battery_low"] else ""
            lines.append(
                f"  {d['date']}: RHR {d['resting_heart_rate'] or 'N/A'}bpm, "
                f"stress {d['avg_stress_level'] or 'N/A'}, {bb}"
            )
        rhrs = [d["resting_heart_rate"] for d in daily if d["resting_heart_rate"]]
        if rhrs:
            trend = "↑ elevated (possible fatigue)" if len(rhrs) >= 2 and rhrs[0] > rhrs[-1] + 3 else "→ stable"
            lines.append(f"  → RHR trend: {round(sum(rhrs)/len(rhrs))}bpm avg, {trend}")

    return "\n".join(lines)


@tool
async def get_weekly_volume_trend(weeks: int = 12) -> str:
    """
    Show weekly training volume for the past N weeks.
    Use this to identify overtraining, undertraining, or good periodization.
    Default is 12 weeks of history.
    """
    activities = await database.get_recent_activities(limit=9999)
    today = date.today()

    weekly: dict[str, float] = {}
    for i in range(weeks):
        week_start = today - timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + timedelta(days=6)
        label = week_start.isoformat()
        dist = sum(
            (a["distance_meters"] or 0) / 1000
            for a in activities
            if week_start.isoformat() <= (a.get("start_time") or "")[:10] <= week_end.isoformat()
        )
        weekly[label] = round(dist, 1)

    sorted_weeks = sorted(weekly.items())
    lines = [f"Weekly volume trend ({weeks} weeks):\n"]
    for w, dist in sorted_weeks:
        bar = "█" * int(dist / 2)
        lines.append(f"  {w}: {dist:5.1f}km  {bar}")

    dists = list(weekly.values())
    if dists:
        avg = sum(dists) / len(dists)
        lines.append(f"\n  Average: {avg:.1f}km/week")
        lines.append(f"  Peak:    {max(dists):.1f}km")
        lines.append(f"  Current: {sorted(weekly.items())[-1][1]:.1f}km")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert personal running coach and sports scientist with access to the athlete's complete Garmin training history.

Your approach:
- Always ground advice in the athlete's actual data — call tools before answering
- For plans: check recovery status AND recent volume, then build from real numbers
- For history questions: use search_training_history for semantic lookups, get_recent_training for structured recent data
- Be specific: reference actual dates, paces, distances from the data
- Flag warning signs (rising RHR, falling HRV, abrupt volume spikes)
- Keep plans realistic based on current training load, not aspirational

When building training plans:
- Establish current weekly volume from weekly_volume_trend
- Apply max 10% volume increase per week unless data shows higher tolerance
- Include easy days (HR < 140) and one quality session per week
- Explicitly note what to skip or reduce if recovery scores are low"""


def build_agent():
    llm = ChatGoogleGenerativeAI(model=MODEL, temperature=0)
    tools = [
        search_training_history,
        get_recent_training,
        get_personal_records,
        compare_training_periods,
        get_recovery_status,
        get_weekly_volume_trend,
    ]
    return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)


_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def stream_chat(messages: list[dict], goal: str | None = None) -> AsyncGenerator[str, None]:
    """
    Stream agent responses as Server-Sent Events.
    Yields SSE-formatted strings including tool call notifications.
    """
    lc_messages = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            if goal and len(lc_messages) == 0:
                content = f"[Athlete's goal: {goal}]\n\n{content}"
            lc_messages.append({"role": "human", "content": content})
        elif role == "assistant":
            lc_messages.append({"role": "ai", "content": content})

    try:
        agent = get_agent()
        async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
            kind = event.get("event")

            if kind == "on_tool_start":
                tool_name = event.get("name", "tool")
                tool_input = event.get("data", {}).get("input", {})
                query = tool_input.get("query") or tool_input.get("days") or ""
                label = _tool_label(tool_name, query)
                log.info("Tool call: %s %s", tool_name, f"({query!r})" if query else "")
                yield f"data: {json.dumps({'type': 'tool_start', 'label': label})}\n\n"

            elif kind == "on_tool_end":
                yield f"data: {json.dumps({'type': 'tool_end'})}\n\n"

            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    for block in (chunk.content if isinstance(chunk.content, list) else [chunk.content]):
                        text = block.get("text", "") if isinstance(block, dict) else str(block)
                        if text:
                            yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"
    except Exception as e:
        log.error("Agent stream error: %s", e, exc_info=True)
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def _tool_label(name: str, query: str) -> str:
    labels = {
        "search_training_history": f'Searching history: "{query}"' if query else "Searching training history...",
        "get_recent_training": f"Fetching last {query} days of training..." if query else "Fetching recent training...",
        "get_personal_records": "Looking up personal records...",
        "compare_training_periods": "Comparing training periods...",
        "get_recovery_status": "Checking recovery status...",
        "get_weekly_volume_trend": "Analyzing weekly volume trends...",
    }
    return labels.get(name, f"Running {name}...")
