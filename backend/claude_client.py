import anthropic
import database

MODEL = "claude-sonnet-4-6"

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "N/A"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def _fmt_pace(pace_per_km: float | None) -> str:
    if not pace_per_km:
        return "N/A"
    m = int(pace_per_km)
    s = int((pace_per_km - m) * 60)
    return f"{m}:{s:02d}/km"


def _fmt_distance(meters: float | None) -> str:
    if not meters:
        return "N/A"
    return f"{meters / 1000:.2f}km"


def _fmt_sleep(seconds: int | None) -> str:
    if not seconds:
        return "N/A"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m"


async def build_data_context() -> str:
    activities = await database.get_recent_activities(30)
    sleep = await database.get_recent_sleep(14)
    daily = await database.get_recent_daily_stats(14)

    lines = ["## ATHLETE TRAINING DATA\n"]

    # --- Activities ---
    lines.append(f"### Recent Activities ({len(activities)} runs)\n")
    if activities:
        total_distance = sum(a["distance_meters"] or 0 for a in activities) / 1000
        avg_hr = [a["avg_heart_rate"] for a in activities if a["avg_heart_rate"]]
        avg_hr_val = round(sum(avg_hr) / len(avg_hr)) if avg_hr else None
        lines.append(f"Total distance (last 30 days): {total_distance:.1f}km")
        if avg_hr_val:
            lines.append(f"Average heart rate: {avg_hr_val} bpm\n")
        lines.append("| Date | Type | Distance | Pace | HR | Duration | Elevation |")
        lines.append("|------|------|----------|------|----|----------|-----------|")
        for a in activities[:20]:
            lines.append(
                f"| {(a['start_time'] or '')[:10]} | {a['activity_type']} | "
                f"{_fmt_distance(a['distance_meters'])} | {_fmt_pace(a['avg_pace_per_km'])} | "
                f"{a['avg_heart_rate'] or 'N/A'} bpm | {_fmt_duration(a['duration_seconds'])} | "
                f"{a['elevation_gain'] or 0:.0f}m |"
            )
    else:
        lines.append("No activities found. Sync your Garmin data first.")

    # --- Sleep ---
    lines.append("\n### Sleep Data (Last 14 Days)\n")
    if sleep:
        scores = [s["sleep_score"] for s in sleep if s["sleep_score"]]
        avg_score = round(sum(scores) / len(scores)) if scores else None
        hrvs = [s["avg_hrv"] for s in sleep if s["avg_hrv"]]
        avg_hrv = round(sum(hrvs) / len(hrvs), 1) if hrvs else None
        if avg_score:
            lines.append(f"Average sleep score: {avg_score}/100")
        if avg_hrv:
            lines.append(f"Average overnight HRV: {avg_hrv}ms\n")
        lines.append("| Date | Score | Total | Deep | REM | HRV |")
        lines.append("|------|-------|-------|------|-----|-----|")
        for s in sleep:
            lines.append(
                f"| {s['date']} | {s['sleep_score'] or 'N/A'} | "
                f"{_fmt_sleep(s['total_sleep_seconds'])} | {_fmt_sleep(s['deep_sleep_seconds'])} | "
                f"{_fmt_sleep(s['rem_sleep_seconds'])} | {s['avg_hrv'] or 'N/A'}ms |"
            )
    else:
        lines.append("No sleep data available.")

    # --- Daily wellness ---
    lines.append("\n### Daily Wellness (Last 14 Days)\n")
    if daily:
        rhr_vals = [d["resting_heart_rate"] for d in daily if d["resting_heart_rate"]]
        avg_rhr = round(sum(rhr_vals) / len(rhr_vals)) if rhr_vals else None
        if avg_rhr:
            lines.append(f"Average resting HR: {avg_rhr} bpm\n")
        lines.append("| Date | Steps | RHR | Stress | Body Battery |")
        lines.append("|------|-------|-----|--------|--------------|")
        for d in daily:
            bb = f"{d['body_battery_low'] or 'N/A'}-{d['body_battery_high'] or 'N/A'}"
            lines.append(
                f"| {d['date']} | {d['steps'] or 'N/A'} | "
                f"{d['resting_heart_rate'] or 'N/A'} bpm | "
                f"{d['avg_stress_level'] or 'N/A'} | {bb} |"
            )
    else:
        lines.append("No daily wellness data available.")

    return "\n".join(lines)


async def chat(messages: list[dict], user_goal: str | None = None) -> str:
    data_context = await build_data_context()

    goal_section = ""
    if user_goal:
        goal_section = f"\n\nATHLETE'S CURRENT GOAL: {user_goal}\n"

    system_content = f"""You are an expert personal running coach and sports scientist. You have access to the athlete's real Garmin training data below. Use this data to give specific, personalized advice — not generic tips.

When analyzing training load, consider:
- Volume (total weekly km and trend)
- Intensity (pace, heart rate zones)
- Recovery signals (sleep score, HRV, resting HR, body battery)
- Consistency (days between runs)

When building training plans:
- Use the athlete's current fitness level as baseline (recent pace, HR)
- Follow the 10% weekly volume rule unless data shows athlete can handle more
- Build in easy days and recovery weeks
- Reference specific recent workouts by date when giving feedback{goal_section}

---

{data_context}"""

    # Use prompt caching: the system prompt (with all data) is cached.
    # Only the most recent few messages are uncached — keeping costs low.
    response = get_client().messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": system_content,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
    )

    return response.content[0].text
